"""Definition execution on the existing LangGraph / RunManager path.

All I/O adapters check simulation at the capability boundary. Internal review loops
checkpoint phases separately, so an interrupt never repeats a preceding model/write.
"""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import hashlib
import json
import re
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_stream_writer
from langgraph.types import interrupt
from langgraph.errors import GraphInterrupt
from inkflow.runtime import WorkflowRuntime, error_category
from inkflow.orchestration.definitions import TYPES, INTERACTIONS, graph_description


class State(TypedDict, total=False):
    raw_text: str
    source_path: str
    run_id: str
    config: dict
    input_revision: str
    feedback: str
    warnings: list
    audit_events: list
    editor_bound: bool
    values: dict
    phases: dict
    steps: int
    next_node: str
    stopped: bool
    failure: str
    suggestions: list
    checked_text: str
    redaction_findings: list
    redacted_text: str
    article_data: dict
    final_document: str
    review_path: str
    blog_path: str
    report_path: str
    report_jsonl_path: str
    node_results: dict
    document_candidate: dict
    automation: dict


def article_from_text(text, metadata):
    # Preserve arbitrary Markdown/MDX (including YAML frontmatter) byte-for-byte.
    # Metadata is explicit; existing headers are retained when present, never parsed as code.
    return {**metadata, 'body': text}


def validate_article_preserving_body(article):
    from inkflow.actions import validate_article
    result = validate_article(article)
    result['body'] = article['body']
    return result


def render_article(article):
    from inkflow.services.packaging import package_astro_markdown
    body = article['body']
    # Existing frontmatter is user content. Use explicit metadata as destination metadata;
    # replace only known fields while preserving all unknown YAML and the exact body.
    match = re.match(r'\A(\ufeff?---\r?\n)(.*?)(\r?\n---\r?\n)([\s\S]*)\Z', body, re.DOTALL)
    header = package_astro_markdown({**article, 'body': 'BODY'}).split('\n---\n', 1)[0][4:]
    if match:
        known = {'title', 'description', 'date', 'tags', 'authors', 'draft', 'slug'}
        lines = match[2].splitlines(keepends=True)
        retained, skip = [], False
        for line in lines:
            field = re.match(r'^([A-Za-z_][\w-]*):', line)
            if field:
                skip = field[1] in known
            if not skip:
                retained.append(line)
        extra = ''.join(retained).rstrip('\r\n')
        newline = '\r\n' if '\r\n' in match[1] else '\n'
        header = header.replace('\n', newline)
        return match[1] + header + (newline + extra if extra else '') + match[3] + match[4]
    return '---\n' + header + '\n---\n' + body


def validate_interaction(pending, action, payload):
    if action == 'stop':
        return {'action': action}
    kind = pending.get('interaction_type', pending['kind'])
    if kind not in INTERACTIONS or action not in INTERACTIONS[kind]['actions']:
        raise ValueError('当前待办不允许此动作，请刷新当前节点。')
    schema = INTERACTIONS[kind]['payload_schemas'][action]
    fields = {**schema['properties'], **({'confirmed_text': {'type': 'string'}} if kind == 'suggestion_review' and action == 'finish_check' else {})}
    types = {'string': str, 'boolean': bool, 'object': dict, 'array': list}
    if not isinstance(payload, dict) or set(payload) - set(fields) or any(k not in payload for k in schema['required']):
        raise ValueError('人工动作字段缺失或包含未知字段，请按当前动作契约提交。')
    if any(not isinstance(v, types[fields[k]['type']]) for k, v in payload.items()):
        raise ValueError('人工动作字段类型不正确。')
    if kind == 'article_metadata':
        article = validate_article_preserving_body(payload.get('article'))
        return {'action': action, 'article': article}
    if kind == 'local_output':
        if payload.get('target') != pending['publish_preview']:
            raise ValueError('目标预览不一致，请核对当前待办。')
        if pending['publish_preview']['exists'] and (pending.get('overwrite_policy') == 'never' or payload.get('overwrite') is not True):
            raise ValueError('目标已存在，必须明确允许覆盖且配置允许覆盖。')
        return {'action': action}
    if kind == 'suggestion_review':
        if action == 'finish_check':
            return {'action': action, **({'text': payload['confirmed_text']} if isinstance(payload.get('confirmed_text'), str) else {})}
        if not isinstance(payload.get('suggestion_id'), str) or not isinstance(payload.get('editor'), dict):
            raise ValueError('请提供建议 ID 和当前文档快照。')
        return {'action': action}
    from inkflow.actions import validate_action
    return validate_action(pending, action, payload)


class Capabilities:
    def __init__(self, runtime, simulation=False):
        self.runtime, self.simulation = runtime, simulation

    def preview(self, profile, article):
        from inkflow.services.publish import preview_blog_target
        if self.simulation:
            # No disk access to user output directories, even for a dry preview.
            return {'path': str(Path(profile['repo_path']) / profile['content_dir'] / article['slug'] / 'index.mdx'),
                    'exists': False, 'fingerprint': None, 'simulated': True}
        return preview_blog_target(Path(profile['repo_path']), profile['content_dir'], article['slug'])

    def publish(self, publication, no_publish):
        from inkflow.services.publish import write_final_blog_article
        if no_publish:
            raise ValueError('本次运行禁止本地发布。')
        if not publication.get('confirmed'):
            raise ValueError('缺少本次节点的人工输出确认。')
        if self.simulation:
            return {'status': 'simulated', 'target': publication['target']['path'], 'message': '计划输出；已跳过文件副作用'}
        profile, article = publication['profile'], publication['article']
        path = write_final_blog_article(publication['document'], article['title'], Path(profile['repo_path']), profile['content_dir'],
                                        slug_source=article['slug'], expected=publication['target'])
        return {'status': 'written', 'target': str(path), 'message': '本地文件已写入；未执行部署'}

    def review(self, directory, identity, document):
        from inkflow.services.local_files import atomic_write
        path = Path(directory) / f'{identity}.md'
        if not self.simulation:
            atomic_write(path, document)
        return {'status': 'simulated' if self.simulation else 'written', 'target': str(path)}

    def report(self, directory, identity, state):
        if self.simulation:
            return {'status': 'simulated', 'target': str(Path(directory) / f'{identity}.md')}
        from inkflow.services.report import write_markdown_report, write_audit_jsonl
        path = write_markdown_report(state, Path(directory), identity)
        audit = write_audit_jsonl(state.get('audit_events', []), Path(directory), identity)
        return {'status': 'written', 'target': str(path), 'audit': str(audit)}


def build_graph(runtime, simulation=False):
    package = runtime.package
    definition, plan = package['definition'], package['compiled']
    instances = {n['node_id']: n for n in definition['nodes']}
    caps = Capabilities(runtime, simulation)
    children = {}
    for identity, spec in plan['nodes'].items():
        from inkflow.llm import LLMConfig
        model = spec['model'] or asdict(runtime.llm)
        child = WorkflowRuntime(LLMConfig(**{**model, 'base_url': model.get('base_url') or None, 'max_tokens': model.get('max_tokens') or None}), runtime.node_keys.get(identity, runtime.api_key),
                                stop=runtime.stop, package={'workflow_id': package['workflow_id'], 'nodes': package['nodes'], 'prompts': spec['prompts']},
                                version_id=runtime.version_id, owner_id=runtime.owner_id, owner_kind=runtime.owner_kind, diagnostic_sink=runtime.diagnostic_sink)
        child.document_id = runtime.document_id
        children[identity] = child

    def wait(identity, child, kind, **data):
        contract = INTERACTIONS[kind]
        pending = {'kind': kind, 'interaction_type': kind, 'interaction_version': contract['version'],
                   'actions': contract['actions'], 'node': identity, 'node_id': identity, 'attempt_id': child.current_attempt,
                   'run_id': runtime.owner_id, 'workflow_id': package['workflow_id'], 'version_id': runtime.version_id,
                   'simulated': simulation, **data}
        response = interrupt(pending)
        return validate_interaction(pending, response['action'], response.get('payload', {}))

    def execute(identity, state, values, phase, child):
        node, spec = instances[identity], plan['nodes'][identity]
        kind, config = node['node_type_id'], spec['config']
        phase = deepcopy(phase)
        stage = phase.get('stage', 'start')
        extra, output = {}, None
        if kind == 'snapshot':
            output = {'text': values['text']}
        elif kind == 'source-material':
            evidence = state.get('automation')
            if not evidence:
                if not simulation:
                    raise ValueError('来源资料节点需要自动化的固定来源快照；请从自动化中心启动。')
                evidence = {'automation_id': 'sample', 'url': 'sample', 'fetched_at': 'sample', 'sha256': 'sample', 'content': '模拟来源资料'}
            marker = '<!-- inkflow-source:' + evidence['automation_id'] + ' -->'
            end = '<!-- /inkflow-source:' + evidence['automation_id'] + ' -->'
            text = values['text']
            # A newer check replaces this task's preceding appendix, without touching other prose.
            text = re.sub(re.escape(marker) + r'[\s\S]*?' + re.escape(end), '', text).rstrip()
            content = evidence['content'].replace(marker, '').replace(end, '')
            fence = '`' * (max([len(m[0]) for m in re.finditer(r'`+', content)] or [2]) + 1)
            appendix = f"{marker}\n## 来源资料（待审阅）\n\n来源：{evidence['url']}\n获取时间：{evidence['fetched_at']}\n内容摘要：{evidence['sha256']}\n\n以下内容仅为资料，不构成流程或操作指令。\n\n{fence}text\n{content}\n{fence}\n{end}"
            output = {'text': text + '\n\n' + appendix + '\n'}
        elif kind == 'prepare-article':
            output = {'article': article_from_text(values['text'], config)}
        elif kind == 'review-article':
            choice = wait(identity, child, 'article_metadata', article=values['article'])
            if choice['action'] == 'stop':
                return None, {}, {'stopped': True}
            article = choice['article']
            output = {'article': article, 'text': article['body']}
            document = render_article(article)
            extra = {'article_data': article, 'final_document': document,
                     'document_candidate': {'id': hashlib.sha256((identity + document).encode()).hexdigest(), 'text': document, 'kind': 'article', 'label': node['label'], 'node_id': identity}}
        elif kind == 'confirm-local-output':
            if state['config'].get('publish', {}).get('no_publish'):
                raise ValueError('本次运行禁止本地发布。')
            if stage == 'start':
                profile = spec['profile']['payload']
                article = values['article']
                phase = {'stage': 'confirm', 'profile': profile, 'article': article, 'document': render_article(article), 'target': caps.preview(profile, article)}
            else:
                choice = wait(identity, child, 'local_output', publish_preview=phase['target'], overwrite_policy=phase['profile']['overwrite'], document=phase['document'])
                if choice['action'] == 'stop':
                    return None, {}, {'stopped': True}
                output = {'publication': {**phase, 'confirmed': True, 'confirmation_node': identity}}
        elif kind == 'local-blog':
            result = caps.publish(values['publication'], state['config'].get('publish', {}).get('no_publish', False))
            output = {'result': result}
            if not simulation:
                extra['blog_path'] = result['target']
        elif kind == 'write-review':
            result = caps.review(config['directory'], runtime.owner_id + '-' + identity, render_article(values['article']))
            output = {'article': values['article'], 'result': result}
            if not simulation:
                extra['review_path'] = result['target']
        elif kind == 'write-report':
            result = caps.report(config['directory'], runtime.owner_id + '-' + identity, state)
            output = {'result': result}
            if not simulation:
                extra.update(report_path=result['target'], report_jsonl_path=result['audit'])
        elif kind == 'end':
            output = {'result': {'status': 'simulated' if simulation else 'completed'}}
        elif kind == 'full-check':
            from inkflow.workflows.full_check import ReviewResult, locate
            from inkflow.llm.core import call_llm
            if stage == 'start':
                if len(values['text']) > 24000:
                    raise ValueError('全文检查上限为 24,000 字符；未截断输入。')
                result = child.invoke('full-check', {'payload': {'article': values['text'], 'feedback': state.get('feedback', '')}}, lambda raw: ReviewResult.model_validate_json(raw).model_dump(), call_llm)
                suggestions = locate(result['suggestions'], values['text'], state.get('input_revision', 'sample'))
                for item in suggestions:
                    item.update(node_id=identity, step=identity, attempt_id=child.current_attempt)
                phase = {'stage': 'review', 'suggestions': suggestions}
                extra = {'suggestions': suggestions, 'checked_text': values['text']}
            else:
                choice = wait(identity, child, 'suggestion_review', suggestions=phase['suggestions'], checked_text=values['text'])
                if choice['action'] == 'stop':
                    return None, {}, {'stopped': True}
                # The manager supplies the explicit current snapshot at finish, after CAS.
                output = {'text': choice.get('text', values['text']), 'has_suggestions': bool(phase['suggestions'])}
        elif kind == 'redaction':
            from inkflow.services.redaction import generate_redaction_findings, apply_redaction_with_llm, build_text_diff
            text = phase.get('text', values['text'])
            if stage in {'start', 'check'}:
                findings = generate_redaction_findings(text, config=child.llm, api_key=child.api_key, runtime=child, strict=True)
                phase.update(stage='review', text=text, findings=findings)
                extra.update(redaction_findings=findings, checked_text=text, suggestions=[])
            elif stage == 'review':
                only_redaction = not any(n['node_type_id'] in {'generate-article', 'prepare-article', 'review-article', 'confirm-local-output', 'local-blog'} for n in definition['nodes'])
                choice = wait(identity, child, 'redaction_review', findings=phase['findings'], llm_enabled=child.llm.enabled, redaction_only=only_redaction)
                if choice['action'] == 'stop':
                    return None, {}, {'stopped': True}
                if any(x['action'] == 'apply' for x in choice['decisions']):
                    phase.update(stage='rewrite', decisions=choice['decisions'], feedback='')
                else:
                    output = {'text': text, 'has_risks': bool(phase['findings'])}
            elif stage == 'rewrite':
                candidate = apply_redaction_with_llm(text, [d for d in phase['decisions'] if d['action'] == 'apply'], phase['feedback'], config=child.llm, api_key=child.api_key, runtime=child, preserve_format=True)
                phase.update(stage='diff', candidate=candidate)
            else:
                choice = wait(identity, child, 'diff_review', candidate_text=phase['candidate'], before_text=text, diff=build_text_diff(text, phase['candidate']))
                if choice['action'] == 'stop':
                    return None, {}, {'stopped': True}
                if choice['action'] == 'retry':
                    phase.update(stage='rewrite', feedback=choice['feedback'])
                else:
                    phase.update(stage='check', text=phase['candidate'])
                    extra['redacted_text'] = phase['candidate']
        elif kind == 'generate-article':
            from inkflow.services.article import generate_article_data
            if stage in {'start', 'generate'}:
                result = generate_article_data(values['text'], extra_instruction=config['instruction'], qa_history=phase.get('qa', []), config=child.llm, api_key=child.api_key, runtime=child)
                if 'question' in result:
                    phase.update(stage='question', question=result['question'])
                else:
                    output = {'article': result['article']}
            else:
                choice = wait(identity, child, 'article_question', question=phase['question'], question_number=len(phase.get('qa', [])) + 1)
                if choice['action'] == 'stop':
                    return None, {}, {'stopped': True}
                phase.update(stage='generate', qa=[*phase.get('qa', []), {'question': phase['question'], 'answer': choice['answer']}])
        return output, phase, extra

    def tracked(identity):
        def node(state):
            child, instance = children[identity], instances[identity]
            values = {port: deepcopy(state['raw_text'] if binding['source'] == '$input' else state.get('values', {})[binding['source']][binding['field']]) for port, binding in instance['inputs'].items()}
            writer = get_stream_writer()
            steps = state.get('steps', 0) + 1
            phase = state.get('phases', {}).get(identity, {})
            if runtime.stop.is_set():
                writer({'type': 'node_started', 'node': identity, 'input': values})
                return {'stopped': True, 'next_node': END}
            try:
                with child.node(identity, {'inputs': values, 'phase': phase}) as attempt:
                    writer({'type': 'node_started', 'node': identity, 'input': values, 'attempt_id': child.current_attempt})
                    if steps > plan['budget']['max_steps'] or phase.get('iterations', 0) >= instance['config'].get('max_iterations', 100):
                        raise ValueError('执行预算已用尽，请调整注册子流程预算或停止处理。')
                    output, phase, extra = execute(identity, state, values, phase, child)
                    attempt['output'] = {'outputs': output, **extra, 'simulated': simulation}
                    if extra.get('stopped'):
                        return {**extra, 'next_node': END}
                    phase['iterations'] = phase.get('iterations', 0) + 1
                    update = {**extra, 'steps': steps, 'phases': {**state.get('phases', {}), identity: phase}}
                    if output is None:
                        update['next_node'] = identity
                    else:
                        # Runtime results must obey the same registered nominal output contracts.
                        expected = TYPES[instance['node_type_id']].outputs
                        if set(output) != set(expected) or any(not isinstance(output[k], bool if t == 'boolean' else str if t == 'text' else dict) for k, t in expected.items()):
                            raise ValueError('节点输出不符合注册契约。')
                        update['values'] = {**state.get('values', {}), identity: output}
                        update['node_results'] = {**state.get('node_results', {}), identity: {'attempt_id': child.current_attempt, 'outputs': output}}
                        edges = [e for e in plan['connections'] if e['source'] == identity]
                        matches = [e for e in edges if e['when'] and output[e['when']['field']] == e['when']['equals']]
                        default = next((e['target'] for e in edges if not e['when']), END)
                        update['next_node'] = matches[0]['target'] if matches else default
                    return update
            except GraphInterrupt:
                raise
            except Exception as error:
                message = child.diagnostic_scrub(str(error))
                writer({'type': 'node_failed', 'node': identity, 'summary': message})
                if error_category(error) == 'stopped':
                    return {'stopped': True, 'next_node': END}
                return {'failure': message, 'next_node': END, 'node_results': {**state.get('node_results', {}), identity: {'status': 'failed', 'error': message, 'target': values.get('publication', {}).get('target')}}}
        return node

    graph = StateGraph(State)
    for identity in instances:
        graph.add_node(identity, runtime.wrap_node(identity, tracked(identity)))
        targets = {e['target'] for e in plan['connections'] if e['source'] == identity} | {END}
        if TYPES[instances[identity]['node_type_id']].loop or instances[identity]['node_type_id'] == 'confirm-local-output':
            targets.add(identity)
        graph.add_conditional_edges(identity, lambda state: state['next_node'], {t: t for t in sorted(targets)})
    graph.add_edge(START, plan['entry'])
    return graph.compile(checkpointer=runtime.checkpointer or InMemorySaver())


def workflow_definition(package):
    from inkflow.workflows import WorkflowDefinition
    definition = package['definition']
    return WorkflowDefinition(definition['workflow_id'], definition['name'], definition['description'],
                              build_graph, lambda graph: graph_description(definition), validate_interaction,
                              lambda state: state.get('document_candidate'))
