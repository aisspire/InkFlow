"""Separate sample ownership with the identical compiler, graph and capability adapters."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from uuid import uuid4
from langgraph.types import Command
from inkflow.runtime import WorkflowRuntime
from inkflow.web.storage import now, encode
from inkflow.orchestration.execution import build_graph, validate_interaction


class DefinitionTests:
    def __init__(self, catalog, diagnostics):
        self.catalog, self.diagnostics = catalog, diagnostics
        self.store = catalog.store
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='inkflow-definition-test')
        self.live = {}
        with self.store.connection() as db:
            rows = db.execute('SELECT id,payload FROM definition_tests').fetchall()
            for row in rows:
                value = json.loads(row['payload'])
                if value['status'] in {'running', 'waiting', 'stopping'}:
                    value.update(status='interrupted', pending=None)
                    db.execute('UPDATE definition_tests SET payload=? WHERE id=?', (encode(value), row['id']))

    def save(self, item, runtime):
        with self.store.connection() as db:
            db.execute('INSERT INTO definition_tests VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                       (item['id'], item['workflow_id'], item['created_at'], encode(runtime.diagnostic_scrub(item))))

    def start(self, workflow_id, package, text, allow_model=False, actions=None, automation=None):
        if workflow_id != package.get('workflow_id') or package.get('schema_version') != 2:
            raise ValueError('请选择此流程的定义包。')
        self.catalog.validate(package)
        fixed = self.catalog.definitions.compile(package)
        if any(n['type']['uses_model'] for n in fixed['compiled']['nodes'].values()) and not allow_model:
            raise ValueError('此流程包含模型节点；须显式允许样例调用，或使用不含模型的流程。')
        config, llm, key = self.catalog.runtime_config({'payload': fixed})
        if key and key in text:
            raise ValueError('样例包含当前模型密钥。')
        identity = uuid4().hex
        runtime = WorkflowRuntime(llm, key, package=fixed, version_id=fixed['compiled']['plan_hash'], owner_id=identity, owner_kind='test', diagnostic_sink=self.diagnostics.save)
        runtime.node_keys = self.catalog.instance_keys(fixed)
        if any(secret and secret in text for secret in runtime.node_keys.values()):
            raise ValueError('样例包含节点模型凭据。')
        graph = build_graph(runtime, simulation=True)
        state = {'raw_text': text, 'run_id': identity, 'config': config, 'warnings': [], 'audit_events': [], 'input_revision': 'fixed-sample'}
        if automation:
            state['automation'] = deepcopy(automation)
        origin = self.catalog.view(workflow_id)
        item = {'id': identity, 'workflow_id': workflow_id, 'created_at': now(), 'status': 'running', 'pending': None,
                'package': fixed, 'source': {'active_version': origin['active']['id'], 'draft_revision': origin['draft']['revision'] if origin['draft'] else None,
                                            'plan_hash': fixed['compiled']['plan_hash'], 'saved': package == (origin['draft']['payload'] if origin['draft'] else origin['active']['payload'])},
                'fixed_input': text, 'state': state, 'executions': [], 'simulated': True, 'requests': {}, 'sample_actions': deepcopy(actions or [])}
        with self.lock:
            if len(self.live) >= 4:
                raise ValueError('已有四个流程试运行，请完成或停止后重试。')
            self.live[identity] = (graph, runtime)
            self.save(item, runtime)
            self.pool.submit(self.drive, item, state)
        return runtime.diagnostic_scrub(item)

    def drive(self, item, value):
        identity = item['id']
        graph, runtime = self.live[identity]
        config = {'configurable': {'thread_id': identity}, 'recursion_limit': 1000}
        try:
            for mode, chunk in graph.stream(value, config, stream_mode=['updates', 'custom']):
                if mode == 'custom':
                    item['executions'].append(runtime.diagnostic_scrub(chunk))
                else:
                    for node, update in chunk.items():
                        if node != '__interrupt__' and update:
                            item['state'].update(update)
            snapshot = graph.get_state(config)
            item['state'].update(dict(snapshot.values))
            pending = [x for task in snapshot.tasks for x in task.interrupts]
            if pending:
                item.update(status='waiting', pending={**pending[0].value, 'id': uuid4().hex})
            else:
                item.update(status='failed' if item['state'].get('failure') else 'stopped' if item['state'].get('stopped') else 'simulated', pending=None)
        except Exception as error:
            item.update(status='failed', pending=None, error=runtime.diagnostic_scrub(str(error)))
        with self.lock:
            self.save(item, runtime)
            if item['status'] != 'waiting':
                self.live.pop(identity, None)
            elif item['sample_actions']:
                sample = item['sample_actions'].pop(0)
                # Samples name the intended node and kind, never silently approve a different wait.
                if sample.get('node_id') != item['pending']['node_id'] or sample.get('kind') != item['pending']['kind']:
                    item.update(status='failed', error='样例动作不属于当前人工节点。', pending=None)
                    self.save(item, runtime); self.live.pop(identity, None)
                else:
                    self.save(item, runtime)
                    self.action(item['workflow_id'], identity, {'request_id': uuid4().hex, 'pending_id': item['pending']['id'], 'action': sample['action'], 'payload': sample.get('payload', {})})

    def action(self, workflow_id, identity, request):
        from inkflow.web.runs import ConflictError
        from inkflow.workflow_assets.catalog import digest
        with self.lock:
            item = self.get(workflow_id, identity)
            previous = item['requests'].get(request['request_id'])
            if previous:
                if previous != digest(request):
                    raise ConflictError('请求 ID 已用于不同的样例动作。')
                return item
            if identity not in self.live or item['status'] != 'waiting' or request.get('pending_id') != item['pending']['id']:
                raise ConflictError('样例待办已过期。')
            validate_interaction(item['pending'], request['action'], request.get('payload', {}))
            if request['action'] in {'accept', 'ignore', 'reject'} and item['pending']['kind'] == 'suggestion_review':
                raise ValueError('流程试运行的建议仅供查看；可显式结束检查，或正式运行后采用。')
            item['requests'][request['request_id']] = digest(request)
            item.update(status='running', pending=None)
            runtime = self.live[identity][1]
            self.save(item, runtime)
            self.pool.submit(self.drive, item, Command(resume=request))
            return item

    def get(self, workflow_id, identity):
        with self.store.connection() as db:
            row = db.execute('SELECT payload FROM definition_tests WHERE workflow_id=? AND id=?', (workflow_id, identity)).fetchone()
        if not row:
            raise KeyError(identity)
        return json.loads(row['payload'])

    def stop(self, workflow_id, identity):
        with self.lock:
            item = self.get(workflow_id, identity)
            if identity not in self.live:
                return item
            self.live[identity][1].stop.set()
            if item['status'] == 'waiting':
                return self.action(workflow_id, identity, {'request_id': uuid4().hex, 'pending_id': item['pending']['id'], 'action': 'stop', 'payload': {}})
            # The worker owns the final record and retains the returned model diagnostics.
            return {**item, 'status': 'stopping'}

    def list(self, workflow_id):
        with self.store.connection() as db:
            rows = db.execute('SELECT payload FROM definition_tests WHERE workflow_id=? ORDER BY rowid DESC LIMIT 100', (workflow_id,)).fetchall()
        return [{k: v for k, v in json.loads(r['payload']).items() if k in {'id', 'workflow_id', 'created_at', 'status', 'source', 'simulated', 'error'}} for r in rows]

    def close(self):
        with self.lock:
            for _, runtime in self.live.values():
                runtime.stop.set()
        self.pool.shutdown(wait=True)
