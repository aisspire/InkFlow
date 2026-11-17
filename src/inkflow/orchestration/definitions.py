"""No executable expressions: bindings and branch predicates are structured data."""
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from inkflow.workflow_assets.prompts import AssetValidationError, validate_prompt, initial_prompts
from inkflow.settings import ModelSettings

COMPILER_VERSION = 'inkflow-plan/1'

PORT_SCHEMAS = {
    'text': {'type': 'string', 'maxLength': 1100000},
    'boolean': {'type': 'boolean'},
    'article': {'type': 'object', 'required': ['title', 'slug', 'date', 'body', 'description', 'authors', 'tags', 'draft'],
                'properties': {**{k: {'type': 'string'} for k in ('title', 'slug', 'date', 'body', 'description')},
                               'authors': {'type': 'array', 'items': {'type': 'string'}}, 'tags': {'type': 'array', 'items': {'type': 'string'}}, 'draft': {'type': 'boolean'}}, 'additionalProperties': False},
    'publication': {'type': 'object', 'required': ['article', 'document', 'target', 'profile', 'confirmed', 'confirmation_node']},
    'result': {'type': 'object', 'required': ['status']},
}
PORT_SCHEMAS['reviewed-article'] = {**PORT_SCHEMAS['article'], 'description': 'Only produced after article_metadata approval; cannot bind a literal.'}


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Empty(Strict):
    pass


class Metadata(Strict):
    title: str = Field(default='', max_length=2000)
    slug: str = Field(default='', max_length=80)
    date: str = Field(default='', max_length=10)
    description: str = Field(default='', max_length=2000)
    authors: list[str] = Field(default_factory=lambda: ['huijue'], max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=100)
    draft: bool = False


class Loop(Strict):
    max_iterations: int = Field(default=12, ge=2, le=100)


class Generation(Strict):
    max_iterations: int = Field(default=8, ge=2, le=20)
    instruction: str = Field(default='', max_length=20000)


class ProfileRef(Strict):
    profile_id: str = Field(min_length=1, max_length=100)
    revision: str = Field(min_length=1, max_length=100)


class Directory(Strict):
    directory: str = Field(min_length=1, max_length=2000)


@dataclass(frozen=True)
class NodeType:
    id: str
    label: str
    inputs: dict
    outputs: dict
    config: type[BaseModel] = Empty
    prompts: tuple = ()
    interactions: tuple = ()
    capabilities: tuple = ()
    side_effect: bool = False
    loop: bool = False

    def describe(self):
        from inkflow.workflow_assets.catalog import EXECUTION_FINGERPRINT
        return {'node_type_id': self.id, 'type_version': 1, 'label': self.label,
                'implementation': f'inkflow.orchestration.execution:{self.id}@sha256:{EXECUTION_FINGERPRINT}',
                'inputs': self.inputs, 'outputs': self.outputs, 'config_schema': self.config.model_json_schema(),
                'input_schema': {'type': 'object', 'required': list(self.inputs), 'additionalProperties': False, 'properties': {k: PORT_SCHEMAS[v] for k, v in self.inputs.items()}},
                'output_schema': {'type': 'object', 'required': list(self.outputs), 'additionalProperties': False, 'properties': {k: PORT_SCHEMAS[v] for k, v in self.outputs.items()}},
                'output_ports': [] if self.id == 'end' else ['next'], 'uses_model': bool(self.prompts),
                'prompt_ids': list(self.prompts), 'model_schema': ModelSettings.model_json_schema() if self.prompts else None,
                'interactions': {kind: INTERACTIONS[kind] for kind in self.interactions},
                'capabilities': list(self.capabilities), 'side_effect': self.side_effect,
                'simulation': 'adapter' if self.side_effect else 'same-executor; explicit sample actions',
                'limits': {'steps': 'definition.budget.max_steps', 'loop': 'config.max_iterations' if self.loop else None,
                           'model_timeout': 'instance.model.timeout_seconds' if self.prompts else None,
                           'retries': 'explicit human action only'}, 'controlled_loop': self.loop}


# Stable interaction identifiers also select existing UI handlers. Payloads receive additional
# domain validation by the same existing action validators used by P1/P2.
INTERACTIONS = {
    'suggestion_review': {'version': 1, 'actions': {'accept': {'suggestion_id': 'string', 'editor': 'object'}, 'ignore': {'suggestion_id': 'string', 'editor': 'object'}, 'reject': {'suggestion_id': 'string', 'editor': 'object'}, 'finish_check': {}}},
    'redaction_review': {'version': 1, 'actions': {'review': {'decisions': 'array'}}},
    'diff_review': {'version': 1, 'actions': {'accept': {'editor': 'object'}, 'retry': {'feedback': 'string'}}},
    'article_question': {'version': 1, 'actions': {'answer': {'answer': 'string'}}},
    'article_metadata': {'version': 1, 'actions': {'approve_article': {'article': 'object'}}},
    'local_output': {'version': 1, 'actions': {'confirm_output': {'target': 'object', 'overwrite': 'boolean'}}},
}

for _kind, _interaction in INTERACTIONS.items():
    _interaction['payload_schemas'] = {}
    for _action, _fields in _interaction['actions'].items():
        _optional = {'feedback': {'type': 'string', 'maxLength': 20000}, 'confirm_conflict': {'type': 'boolean'}}
        if _kind == 'article_metadata':
            # Formal runs require this snapshot in RunManager; simulations have no document.
            _optional['editor'] = {'type': 'object'}
        _interaction['payload_schemas'][_action] = {'type': 'object', 'required': list(_fields),
            'properties': {**{k: {'type': v} for k, v in _fields.items()}, **_optional}, 'additionalProperties': False}

TYPES = {t.id: t for t in [
    NodeType('snapshot', '固定文档快照', {'text': 'text'}, {'text': 'text'}),
    NodeType('source-material', '来源资料候选（自动化）', {'text': 'text'}, {'text': 'text'}, capabilities=('candidate',)),
    NodeType('prepare-article', '文章与元数据准备', {'text': 'text'}, {'article': 'article'}, Metadata),
    NodeType('review-article', '人工审阅文章与元数据', {'article': 'article'}, {'article': 'reviewed-article', 'text': 'text'}, interactions=('article_metadata',), capabilities=('human',)),
    NodeType('confirm-local-output', '确认本地输出目标', {'article': 'reviewed-article'}, {'publication': 'publication'}, ProfileRef, interactions=('local_output',), capabilities=('human', 'local-blog')),
    NodeType('local-blog', '写入本地博客', {'publication': 'publication'}, {'result': 'result'}, capabilities=('local-blog',), side_effect=True),
    NodeType('full-check', '全文检查与建议处理', {'text': 'text'}, {'text': 'text', 'has_suggestions': 'boolean'}, Loop, ('full-check',), ('suggestion_review',), ('model', 'human', 'candidate'), loop=True),
    NodeType('redaction', '脱敏审核与复查', {'text': 'text'}, {'text': 'text', 'has_risks': 'boolean'}, Loop, ('risk-check', 'redaction-rewrite'), ('redaction_review', 'diff_review'), ('model', 'human', 'candidate'), loop=True),
    NodeType('generate-article', '生成文章与补充问答', {'text': 'text'}, {'article': 'article'}, Generation, ('article-generation',), ('article_question',), ('model', 'human', 'candidate'), loop=True),
    NodeType('write-review', '写入审阅稿', {'article': 'reviewed-article'}, {'article': 'reviewed-article', 'result': 'result'}, Directory, capabilities=('local-review',), side_effect=True),
    NodeType('write-report', '写入报告', {'result': 'result'}, {'result': 'result'}, Directory, capabilities=('local-report',), side_effect=True),
    NodeType('end', '结束', {}, {'result': 'result'}),
]}


class Binding(Strict):
    source: str
    field: str


class Instance(Strict):
    node_id: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_-]{0,79}$')
    node_type_id: str
    type_version: Literal[1]
    label: str = Field(min_length=1, max_length=200)
    inputs: dict[str, Binding]
    config: dict = Field(default_factory=dict)
    prompts: dict = Field(default_factory=dict)
    model: dict = Field(default_factory=dict)


class Condition(Strict):
    field: str
    equals: str | bool


class Connection(Strict):
    edge_id: str = Field(min_length=1, max_length=100)
    source: str
    target: str
    port: Literal['next'] = 'next'
    when: Condition | None = None


class Budget(Strict):
    max_steps: int = Field(ge=1, le=500)


class Position(Strict):
    x: float | int
    y: float | int


class Definition(Strict):
    schema_version: Literal[1]
    workflow_id: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_-]{0,99}$')
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(max_length=2000)
    input_contract: Literal['document-snapshot-v1']
    output_contract: Literal['node-results-v1']
    nodes: list[Instance] = Field(max_length=100)
    connections: list[Connection] = Field(max_length=300)
    entry: str
    ends: list[str] = Field(max_length=100)
    capabilities: list[Literal['human', 'model', 'candidate', 'local-blog', 'local-review', 'local-report']]
    budget: Budget
    canvas: dict[str, Position] = Field(default_factory=dict)
    source: dict = Field(default_factory=dict)


def schema_errors(error, prefix='definition'):
    return [{'field': prefix + '.' + '.'.join(map(str, e['loc'])),
             'message': '字段缺失、类型或范围错误；请按节点目录填写，删除不支持的字段。'} for e in error.errors()]


def graph_description(definition):
    nodes = {n['node_id']: n for n in definition['nodes']}
    visited, order = set(), []
    def visit(identity):
        if identity in visited or identity not in nodes:
            return
        visited.add(identity)
        for edge in definition['connections']:
            if edge['source'] == identity:
                visit(edge['target'])
        order.append(identity)
    visit(definition['entry'])
    ordered = [nodes[n] for n in reversed(order)] + [n for n in definition['nodes'] if n['node_id'] not in visited]
    return {'id': definition['workflow_id'], 'name': definition['name'], 'description': definition['description'],
            'nodes': [{'id': n['node_id'], 'label': n['label'], 'description': TYPES[n['node_type_id']].label if n['node_type_id'] in TYPES else '未知类型',
                       'node_type_id': n['node_type_id'], 'human': bool(TYPES[n['node_type_id']].interactions) if n['node_type_id'] in TYPES else False,
                       'position': definition.get('canvas', {}).get(n['node_id'], {'x': (i % 3) * 290, 'y': (i // 3) * 170})}
                      for i, n in enumerate(ordered)],
            'edges': [{'id': e['edge_id'], 'source': e['source'], 'target': e['target']} for e in definition['connections']] +
                     [{'id': n['node_id'] + '-internal', 'source': n['node_id'], 'target': n['node_id']}
                      for n in definition['nodes'] if n['node_type_id'] in {'full-check', 'redaction', 'generate-article', 'confirm-local-output'}]}


def compile_definition(raw, profiles):
    """Resolve only installed types. Dominance prevents reads from unexecuted branches."""
    from inkflow.workflow_assets.catalog import digest
    try:
        definition = Definition.model_validate(raw).model_dump()
    except ValidationError as error:
        raise AssetValidationError(schema_errors(error)) from None
    errors = []
    def fail(field, message, node=None, edge=None):
        errors.append({'field': field, 'message': message, 'node_id': node, 'edge_id': edge})
    nodes, resolved = {}, {}
    if not definition['nodes'] or not definition['ends']:
        fail('definition.ends', '流程需要入口和明确结束节点，请添加并连接“结束”节点。')
    for i, node in enumerate(definition['nodes']):
        identity = node['node_id']
        prefix = f'definition.nodes.{i}'
        if identity in nodes:
            fail(prefix + '.node_id', '节点 ID 重复；请为每个实例使用独立 ID。', identity)
        nodes[identity] = node
        kind = TYPES.get(node['node_type_id'])
        if not kind:
            fail(prefix + '.node_type_id', '节点类型未安装，请从节点目录选择。', identity)
            continue
        try:
            node['config'] = kind.config.model_validate(node['config']).model_dump()
        except ValidationError as error:
            errors.extend({**e, 'node_id': identity} for e in schema_errors(error, prefix + '.config'))
        if set(node['inputs']) != set(kind.inputs):
            fail(prefix + '.inputs', '输入端口必须为：' + ', '.join(kind.inputs), identity)
        if not set(kind.capabilities).issubset(definition['capabilities']):
            fail(prefix + '.capabilities', '流程必须声明此节点所需能力：' + ', '.join(kind.capabilities), identity)
        if set(node['prompts']) != set(kind.prompts):
            fail(prefix + '.prompts', '请固定此实例所需的全部提示词：' + ', '.join(kind.prompts), identity)
        for pid, prompt in node['prompts'].items():
            try:
                validate_prompt(prompt, pid)
                prompt['revision'] = digest({k: v for k, v in prompt.items() if k != 'revision'})
            except (AssetValidationError, KeyError) as error:
                fail(prefix + '.prompts.' + pid, str(error), identity)
        if kind.prompts:
            try:
                node['model'] = ModelSettings.model_validate({**node['model'], 'base_url': node['model'].get('base_url') or '', 'max_tokens': node['model'].get('max_tokens') or 0}).model_dump()
            except ValidationError as error:
                errors.extend({**e, 'node_id': identity} for e in schema_errors(error, prefix + '.model'))
        elif node['model']:
            fail(prefix + '.model', '此节点没有模型参数，请移除此配置。', identity)
        resolved[identity] = {'type': kind.describe(), 'config': deepcopy(node['config']), 'prompts': deepcopy(node['prompts']), 'model': deepcopy(node['model'])}
        if kind.id == 'confirm-local-output':
            try:
                resolved[identity]['profile'] = profiles.revision(node['config']['profile_id'], node['config']['revision'])
            except (KeyError, ValueError):
                fail(prefix + '.config', '发布配置修订不存在，请选择已保存的配置修订。', identity)
    outgoing = {n: [] for n in nodes}
    incoming = {n: set() for n in nodes}
    seen_edges = set()
    for i, edge in enumerate(definition['connections']):
        prefix = f'definition.connections.{i}'
        if edge['edge_id'] in seen_edges:
            fail(prefix + '.edge_id', '连线 ID 重复。', edge=edge['edge_id'])
        seen_edges.add(edge['edge_id'])
        if edge['source'] not in nodes or edge['target'] not in nodes:
            fail(prefix, '连线端点不存在；删除连线或选择已有节点。', edge=edge['edge_id'])
            continue
        outgoing[edge['source']].append(edge)
        incoming[edge['target']].add(edge['source'])
        when = edge['when']
        kind = TYPES.get(nodes[edge['source']]['node_type_id'])
        if when and (not kind or kind.outputs.get(when['field']) not in {'boolean', 'text'} or
                     (kind.outputs.get(when['field']) == 'boolean') != isinstance(when['equals'], bool)):
            fail(prefix + '.when', '条件只能比较源节点声明的布尔或文本输出，值类型必须一致。', edge=edge['edge_id'])
    if definition['entry'] not in nodes:
        fail('definition.entry', '请选择已有入口节点。')
    elif incoming[definition['entry']]:
        fail('definition.entry', '入口不能有上游；循环请使用带预算的注册子流程。', definition['entry'])
    for end in definition['ends']:
        if end not in nodes or nodes[end]['node_type_id'] != 'end':
            fail('definition.ends', '终点必须指向“结束”类型实例。', end)
    for identity, node in nodes.items():
        edges = outgoing[identity]
        if node['node_type_id'] == 'end':
            if edges or identity not in definition['ends']:
                fail('definition.ends', '结束节点不能连出，且必须声明为终点。', identity)
        elif sum(e['when'] is None for e in edges) != 1:
            fail('definition.connections', '每个非终点节点必须有且仅有一个兜底出口；条件出口可选。', identity)
        predicates = [str(e['when']) for e in edges if e['when']]
        if len(set(predicates)) != len(predicates):
            fail('definition.connections', '条件分支重复，请使用互斥条件。', identity)
    for identity, parents in incoming.items():
        if len(parents) > 1:
            fail('definition.connections', '当前类型不支持合并多条并行路径；请拆分流程或使用受控子流程。', identity)
    reached, visiting, order = set(), set(), []
    def walk(identity):
        if identity in visiting:
            fail('definition.connections', '发现未声明的循环；请使用带退出条件和预算的脱敏／问答子流程。', identity)
            return
        if identity in reached or identity not in nodes:
            return
        reached.add(identity); visiting.add(identity)
        for edge in outgoing[identity]:
            walk(edge['target'])
        visiting.remove(identity); order.append(identity)
    walk(definition['entry'])
    for identity in nodes.keys() - reached:
        fail('definition.nodes', '节点不可达，请从入口连接或删除。', identity)
    order.reverse()
    dominators = {n: set(nodes) for n in nodes}
    if definition['entry'] in nodes:
        dominators[definition['entry']] = {definition['entry']}
    for _ in nodes:
        for n in order:
            if n != definition['entry']:
                parents = incoming[n]
                dominators[n] = {n} | (set.intersection(*(dominators[p] for p in parents)) if parents else set())
    for identity, node in nodes.items():
        kind = TYPES.get(node['node_type_id'])
        if not kind:
            continue
        for port, binding in node['inputs'].items():
            source, field = binding['source'], binding['field']
            output_type = {'text': 'text'}.get(field) if source == '$input' else TYPES.get(nodes.get(source, {}).get('node_type_id'), NodeType('', '', {}, {})).outputs.get(field)
            if not output_type:
                fail(f'definition.nodes.{identity}.inputs.{port}', '绑定的节点或输出字段不存在。', identity)
            elif kind.inputs.get(port) != output_type:
                fail(f'definition.nodes.{identity}.inputs.{port}', f'类型不兼容：此端口需要 {kind.inputs.get(port)}，来源为 {output_type}。', identity)
            if source != '$input' and (source == identity or source not in dominators[identity]):
                fail(f'definition.nodes.{identity}.inputs.{port}', '来源必须在每条执行路径上先于本节点；不能读取未执行分支。', identity)
    if errors:
        raise AssetValidationError(errors)
    # Layout is intentionally excluded from execution identity and order.
    execution = {k: v for k, v in definition.items() if k not in {'canvas', 'name', 'description', 'source'}}
    return {'protocol_version': COMPILER_VERSION, 'definition_hash': digest(execution), 'entry': definition['entry'],
            'ends': definition['ends'], 'order': order, 'connections': definition['connections'],
            'nodes': resolved, 'budget': definition['budget'], 'capabilities': definition['capabilities']}


def new_instance(type_id, identity, model):
    kind = TYPES[type_id]
    config = kind.config().model_dump() if type_id not in {'confirm-local-output', 'write-review', 'write-report'} else ({'profile_id': '', 'revision': ''} if type_id == 'confirm-local-output' else {'directory': ''})
    return {'node_id': identity, 'node_type_id': type_id, 'type_version': 1, 'label': kind.label,
            'inputs': {port: {'source': '$input', 'field': 'text'} for port in kind.inputs}, 'config': config,
            'prompts': initial_prompts(set(kind.prompts)), 'model': deepcopy(model) if kind.prompts else {}}


def template(workflow_id, name, model, profile=None, variant='publish-existing'):
    kinds = ['snapshot', 'prepare-article', 'review-article', 'confirm-local-output', 'local-blog', 'end']
    if variant == 'source-update':
        kinds = ['source-material', 'prepare-article', 'review-article', 'end']
    elif variant == 'full-check':
        kinds = ['snapshot', 'full-check', 'end']
    elif variant == 'redaction-only':
        kinds = ['snapshot', 'redaction', 'end']
    elif variant == 'reviewed-publishing':
        kinds = ['snapshot', 'redaction', 'generate-article', 'review-article', 'confirm-local-output', 'local-blog', 'end']
    nodes = [new_instance(k, f'n{i + 1}', model) for i, k in enumerate(kinds)]
    for i, n in enumerate(nodes[1:], 1):
        for port in n['inputs']:
            n['inputs'][port] = {'source': nodes[i - 1]['node_id'], 'field': 'text' if port == 'text' else 'article' if port == 'article' else 'publication'}
        if n['node_type_id'] == 'confirm-local-output' and profile:
            n['config'] = {'profile_id': profile['profile_id'], 'revision': profile['revision']}
    return {'schema_version': 1, 'workflow_id': workflow_id, 'name': name, 'description': '使用已注册节点组合；保存草稿后显式启用。',
            'input_contract': 'document-snapshot-v1', 'output_contract': 'node-results-v1', 'nodes': nodes,
            'connections': [{'edge_id': f'e{i}', 'source': a['node_id'], 'target': b['node_id'], 'port': 'next', 'when': None} for i, (a, b) in enumerate(zip(nodes, nodes[1:]))],
            'entry': nodes[0]['node_id'], 'ends': [nodes[-1]['node_id']], 'canvas': {}, 'budget': {'max_steps': 100},
            'capabilities': sorted({c for k in kinds for c in TYPES[k].capabilities}), 'source': {}}
