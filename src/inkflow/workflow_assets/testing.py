"""固定样例试运行只执行单个模型能力，不创建文章运行或执行副作用。"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading
from uuid import uuid4

from inkflow.runtime import WorkflowRuntime, error_category
from inkflow.web.storage import encode, now
from inkflow.workflow_assets.catalog import NODE_PROMPTS, digest
from inkflow.workflow_assets.prompts import render, invalid


def parser_for(prompt_id, variables):
    if prompt_id == 'full-check':
        from inkflow.workflows.full_check import ReviewResult
        return lambda raw: ReviewResult.model_validate_json(raw).model_dump()
    if prompt_id == 'risk-check':
        from inkflow.services.redaction import parse_findings
        return parse_findings
    if prompt_id == 'article-generation':
        from inkflow.services.article import parse_article_response
        # Only fixed messages are tested, with no graph continuation or question loop.
        return lambda raw: parse_article_response(raw, variables['today'], [])
    return lambda raw: raw


class ModelTests:
    def __init__(self, catalog, diagnostics):
        self.catalog, self.diagnostics = catalog, diagnostics
        self.store = catalog.store
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='inkflow-model-test')
        self.live = {}
        self.lock = threading.RLock()

    def prepare(self, workflow_id, node_id, package, values, source=None, selected_prompt=None):
        if package.get('workflow_id') != workflow_id:
            invalid('workflow_id', '样例不属于此流程。')
        self.catalog.validate(package)
        definition_node = next((n for n in package.get('definition', {}).get('nodes', []) if n['node_id'] == node_id), None)
        prompt_id = (selected_prompt or next(iter(definition_node['prompts']), None)) if definition_node else package['nodes'].get(node_id, {}).get('prompt_id')
        if not prompt_id:
            invalid('node_id', '此节点不调用模型，不能编辑提示词或试运行。')
        prompt = deepcopy(definition_node['prompts'][prompt_id] if definition_node else package['prompts'][prompt_id])
        prompt['revision'] = digest({k: v for k, v in prompt.items() if k != 'revision'})
        messages = render(prompt, values)
        _, _, key = self.catalog.runtime_config({'payload': package})
        if key and key in encode(values):
            invalid('variables', '样例包含当前模型密钥，请移除后重试。')
        fixed = deepcopy(package)
        fixed['prompts'][prompt_id] = prompt
        if definition_node:
            fixed['model'] = deepcopy(definition_node['model'])
        catalog = self.catalog.view(workflow_id)
        draft = catalog['draft']
        origin = {'base_version': catalog['active']['id'], 'draft_revision': None, 'saved': False}
        if draft:
            origin.update(base_version=draft['base_version'], draft_revision=draft['revision'], saved=package == draft['payload'])
        elif package == catalog['active']['payload']:
            origin.update(saved=True)
        if source and source.get('base_version'):
            historic = self.catalog.version(workflow_id, source['base_version'])
            if historic['payload'] == package:
                origin.update(base_version=historic['id'], draft_revision=None, saved=True)
        return {'workflow_id': workflow_id, 'node_id': node_id, 'prompt_id': prompt_id, 'prompt_revision': prompt['revision'],
                'source': {**origin, 'content_hash': digest(fixed), 'label': '固定的当前编辑内容'},
                'messages': messages, 'model': deepcopy(fixed['model']), 'variables': deepcopy(values), 'package': fixed}

    def start(self, prepared):
        with self.lock:
            if len(self.live) >= 4:
                invalid('test', '已有四个试运行，请等待或停止后重试。')
            identity = uuid4().hex
            config, llm, key = self.catalog.runtime_config({'payload': prepared['package']})
            runtime = WorkflowRuntime(llm, key, package=prepared['package'], version_id=prepared['source']['content_hash'],
                                      owner_id=identity, owner_kind='test', diagnostic_sink=self.diagnostics.save)
            item = runtime.scrub({**prepared, 'id': identity, 'created_at': now(), 'status': 'running'})
            with self.store.connection() as db:
                db.execute('INSERT INTO model_tests VALUES (?,?,?,?)', (identity, item['workflow_id'], item['created_at'], encode(runtime.diagnostic_scrub(item))))
            self.live[identity] = runtime
            self.pool.submit(self._execute, item, runtime)
        return runtime.diagnostic_scrub(item)

    def _execute(self, item, runtime):
        from inkflow.llm.core import call_llm
        try:
            with runtime.node(item['node_id'], item['variables']) as attempt:
                result = runtime.invoke(item['prompt_id'], item['variables'], parser_for(item['prompt_id'], item['variables']), call_llm)
                attempt['output'] = result
            item.update(status='completed', result=result)
        except Exception as error:
            item.update(status='stopped' if error_category(error) == 'stopped' else 'failed',
                        error={'category': error_category(error), 'message': runtime.diagnostic_scrub(str(error))})
        finally:
            with self.lock:
                if runtime.stop.is_set():
                    item['status'] = 'stopped'
                item['finished_at'] = now()
                with self.store.connection() as db:
                    db.execute('UPDATE model_tests SET payload=? WHERE id=?', (encode(runtime.diagnostic_scrub(item)), item['id']))
                self.live.pop(item['id'], None)

    def get(self, workflow_id, identity):
        with self.store.connection() as db:
            row = db.execute('SELECT payload FROM model_tests WHERE workflow_id=? AND id=?', (workflow_id, identity)).fetchone()
        if not row:
            raise KeyError(identity)
        return json.loads(row['payload'])

    def list(self, workflow_id):
        with self.store.connection() as db:
            rows = db.execute('SELECT payload FROM model_tests WHERE workflow_id=? ORDER BY rowid DESC LIMIT 100', (workflow_id,)).fetchall()
        fields = ('id', 'node_id', 'created_at', 'finished_at', 'status', 'source', 'prompt_revision', 'error')
        return [{key: value for key, value in json.loads(row['payload']).items() if key in fields} for row in rows]

    def stop(self, workflow_id, identity):
        with self.lock:
            item = self.get(workflow_id, identity)
            if identity in self.live:
                self.live[identity].stop.set()
                item['status'] = 'stopping'
                with self.store.connection() as db:
                    db.execute('UPDATE model_tests SET payload=? WHERE id=?', (encode(item), identity))
        return item

    def close(self):
        with self.lock:
            for runtime in self.live.values():
                runtime.stop.set()
        self.pool.shutdown(wait=True)
