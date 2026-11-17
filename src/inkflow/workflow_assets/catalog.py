"""SQLite 流程草稿、不可变版本及原子启用指针。"""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from inkflow.settings import ModelSettings, SettingsData, resolve_directory
from inkflow.workflow_assets.prompts import PROTOCOLS, invalid, initial_prompts, validate_prompt

NODE_PROMPTS = {'full_check': 'full-check', 'redaction_plan': 'risk-check',
                'apply_redaction': 'redaction-rewrite', 'article_generation': 'article-generation'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def source_fingerprint():
    root = Path(__file__).resolve().parents[1]
    paths = sorted(p for p in root.rglob('*.py') if not {'web', '__pycache__'} & set(p.relative_to(root).parts))
    return hashlib.sha256(b''.join(p.relative_to(root).as_posix().encode() + p.read_bytes().replace(b'\r\n', b'\n') for p in paths)).hexdigest()


# Freeze the installed implementation for this process; source edits require a controlled restart.
EXECUTION_FINGERPRINT = source_fingerprint()


def implementation_ids(nodes):
    return {node['id']: {'implementation': f"inkflow:{node['id']}@sha256:{EXECUTION_FINGERPRINT}",
                         **({'prompt_id': NODE_PROMPTS[node['id']]} if node['id'] in NODE_PROMPTS else {})} for node in nodes}


def initial_package(definition, config):
    graph = definition.describe()
    nodes = implementation_ids(graph['nodes'])
    prompts = initial_prompts({v['prompt_id'] for v in nodes.values() if 'prompt_id' in v})
    return {'schema_version': 1, 'workflow_id': definition.id, 'graph': graph,
            'input_contract': 'document-snapshot-v1', 'output_contract': 'builtin-result-v1',
            'nodes': nodes, 'prompts': prompts, 'model': deepcopy(config['llm']),
            'config_directory': config['_config_dir'],
            'node_config': {'write_review': {'review': config['review']}, 'publish': {'blog': config['blog']},
                            'report': {'report': config['report']}} if definition.id == 'reviewed-publishing' else {}}


class WorkflowCatalog:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        with store.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS asset_counters(name TEXT PRIMARY KEY, value INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow_versions(id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow_heads(workflow_id TEXT PRIMARY KEY, version_id TEXT NOT NULL, generation INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow_drafts(workflow_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, base_version TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS prompt_revisions(prompt_id TEXT NOT NULL, revision TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(prompt_id,revision));
            ''')
        with store.connection() as db:
            db.execute("INSERT OR IGNORE INTO asset_counters VALUES ('draft', (SELECT COALESCE(MAX(revision),0) FROM workflow_drafts))")
        from inkflow.orchestration.catalog import DefinitionCatalog
        self.definitions = DefinitionCatalog(self)
        from inkflow.workflows import WORKFLOWS
        config, _, _ = settings.snapshot()
        from inkflow.web.storage import now, encode
        with store.connection() as db:
            for definition in WORKFLOWS.values():
                if db.execute('SELECT 1 FROM workflow_heads WHERE workflow_id=?', (definition.id,)).fetchone():
                    continue
                package = initial_package(definition, config)
                self.validate(package)
                self._freeze_prompts(db, package)
                identity = uuid4().hex
                db.execute('INSERT INTO workflow_versions VALUES (?,?,?,?)', (identity, definition.id, now(), encode(package)))
                db.execute('INSERT INTO workflow_heads VALUES (?,?,1)', (definition.id, identity))

    def _freeze_prompts(self, db, package):
        from inkflow.web.storage import encode
        prompts = list(package['prompts'].items())
        if package.get('schema_version') == 2:
            prompts += [(pid, prompt) for node in package['definition']['nodes'] for pid, prompt in node['prompts'].items()]
        for identity, prompt in prompts:
            prompt['revision'] = digest({k: v for k, v in prompt.items() if k != 'revision'})
            db.execute('INSERT OR IGNORE INTO prompt_revisions VALUES (?,?,?)', (identity, prompt['revision'], encode(prompt)))

    def version(self, workflow_id, version_id):
        with self.store.connection() as db:
            row = db.execute('SELECT * FROM workflow_versions WHERE workflow_id=? AND id=?', (workflow_id, version_id)).fetchone()
        if not row:
            raise KeyError(version_id)
        return {**dict(row), 'payload': json.loads(row['payload'])}

    def active(self, workflow_id):
        with self.store.connection() as db:
            row = db.execute('SELECT version_id,generation FROM workflow_heads WHERE workflow_id=?', (workflow_id,)).fetchone()
        if not row:
            raise KeyError(workflow_id)
        if not row['version_id']:
            with self.store.connection() as db:
                draft = db.execute('SELECT payload FROM workflow_drafts WHERE workflow_id=?', (workflow_id,)).fetchone()
            return {'id': '', 'workflow_id': workflow_id, 'created_at': '', 'generation': 0, 'payload': json.loads(draft['payload'])}
        return {**self.version(workflow_id, row['version_id']), 'generation': row['generation']}

    def view(self, workflow_id):
        active = self.active(workflow_id)
        with self.store.connection() as db:
            draft = db.execute('SELECT * FROM workflow_drafts WHERE workflow_id=?', (workflow_id,)).fetchone()
            versions = [dict(row) for row in db.execute('SELECT id,created_at FROM workflow_versions WHERE workflow_id=? ORDER BY rowid DESC', (workflow_id,))]
            heads = list(db.execute('SELECT h.workflow_id,v.payload FROM workflow_heads h JOIN workflow_versions v ON v.id=h.version_id'))
        impacts = {identity: [h['workflow_id'] for h in heads if identity in json.loads(h['payload'])['prompts']] for identity in active['payload']['prompts']}
        return {'active': active, 'draft': {**dict(draft), 'payload': json.loads(draft['payload'])} if draft else None,
                'versions': versions, 'shared_prompt_users': impacts, 'protocols': PROTOCOLS}

    def create_draft(self, workflow_id, version_id=None):
        from inkflow.web.storage import encode
        from inkflow.web.runs import ConflictError
        source = self.version(workflow_id, version_id) if version_id else self.active(workflow_id)
        payload = deepcopy(source['payload'])
        # New draft explicitly adopts the installed adapter; historical packages remain untouched.
        if payload.get('schema_version') == 2:
            payload = self.definitions.package(payload['definition'])
        else:
            payload['nodes'] = implementation_ids(payload['graph']['nodes'])
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM workflow_drafts WHERE workflow_id=?', (workflow_id,)).fetchone():
                raise ConflictError('此流程已有草稿，请先加载并继续编辑；不会覆盖已有草稿。')
            revision = self._next_revision(db)
            db.execute('INSERT INTO workflow_drafts VALUES (?,?,?,?)', (workflow_id, revision, source['id'], encode(payload)))
        return self.view(workflow_id)

    def save(self, workflow_id, revision, package):
        from inkflow.web.storage import encode
        from inkflow.web.runs import ConflictError
        if package.get('workflow_id') != workflow_id:
            invalid('workflow_id', '草稿不属于此流程。')
        if package.get('schema_version') == 2:
            # Incomplete definitions can be saved; only activation requires compilation.
            from inkflow.orchestration.definitions import Definition, schema_errors
            from inkflow.workflow_assets.prompts import AssetValidationError
            from pydantic import ValidationError
            try:
                Definition.model_validate(package['definition'])
            except ValidationError as error:
                raise AssetValidationError(schema_errors(error)) from None
            if package['definition']['workflow_id'] != workflow_id:
                invalid('definition.workflow_id', '不能改变流程稳定身份。')
            package = self.definitions.package(package['definition'])
        else:
            self.validate(package)
        with self.store.connection() as db:
            if not db.in_transaction:
                db.execute('BEGIN IMMEDIATE')
            next_revision = self._next_revision(db)
            updated = db.execute('UPDATE workflow_drafts SET revision=?,payload=? WHERE workflow_id=? AND revision=?', (next_revision, encode(package), workflow_id, revision))
            if updated.rowcount != 1:
                raise ConflictError('流程草稿已被其它页面修改，请保留本地内容并加载最新草稿比较。')
        return self.view(workflow_id)

    @staticmethod
    def _next_revision(db):
        db.execute("UPDATE asset_counters SET value=value+1 WHERE name='draft'")
        return db.execute("SELECT value FROM asset_counters WHERE name='draft'").fetchone()['value']

    def activate(self, workflow_id, revision, generation):
        from inkflow.web.storage import encode, now
        from inkflow.web.runs import ConflictError
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM workflow_drafts WHERE workflow_id=? AND revision=?', (workflow_id, revision)).fetchone()
            head = db.execute('SELECT generation FROM workflow_heads WHERE workflow_id=?', (workflow_id,)).fetchone()
            if not row or not head or head['generation'] != generation:
                raise ConflictError('草稿或启用版本已变化，请刷新后核对再启用。')
            package = json.loads(row['payload'])
            from inkflow.ai.service import activation_gate
            activation_gate(db, workflow_id, revision, package)
            self.validate(package)
            self._freeze_prompts(db, package)
            if package.get('schema_version') == 2:
                package = self.definitions.compile(package)
            identity = uuid4().hex
            db.execute('INSERT INTO workflow_versions VALUES (?,?,?,?)', (identity, workflow_id, now(), encode(package)))
            db.execute('UPDATE workflow_heads SET version_id=?,generation=generation+1 WHERE workflow_id=?', (identity, workflow_id))
            db.execute('DELETE FROM workflow_drafts WHERE workflow_id=?', (workflow_id,))
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='ai_proposals'").fetchone():
                for proposal in db.execute('SELECT payload FROM ai_proposals WHERE workflow_id=?', (workflow_id,)).fetchall():
                    item = json.loads(proposal['payload'])
                    if item.get('applied_revision') == revision and item['status'] == 'applied':
                        item.update(status='activated', activated_version=identity)
                        db.execute('UPDATE ai_proposals SET payload=? WHERE id=?', (encode(item), item['id']))
        return self.view(workflow_id)

    def rollback(self, workflow_id, version_id, generation):
        from inkflow.web.runs import ConflictError
        version = self.version(workflow_id, version_id)
        self.validate(version['payload'])
        with self.store.connection() as db:
            result = db.execute('UPDATE workflow_heads SET version_id=?,generation=generation+1 WHERE workflow_id=? AND generation=?', (version_id, workflow_id, generation))
            if result.rowcount != 1:
                raise ConflictError('启用版本已变化，请刷新后再回退。')
        return self.view(workflow_id)

    def validate(self, package):
        from inkflow.workflows import get_workflow
        from inkflow.workflow_assets.prompts import AssetValidationError
        from pydantic import ValidationError
        if not isinstance(package, dict):
            invalid('package', '请提交完整流程包。')
        if source_fingerprint() != EXECUTION_FINGERPRINT:
            invalid('nodes', '执行源码已变化，请先重启服务，再创建并启用适配当前实现的草稿。')
        if not isinstance(package.get('workflow_id'), str):
            invalid('workflow_id', '流程 ID 必须为文本。')
        if package.get('schema_version') == 2:
            return self.definitions.validate(package)
        definition = get_workflow(package.get('workflow_id', ''))
        config, _, key = self.settings.snapshot()
        reference = initial_package(definition, config)
        if set(package) != set(reference):
            invalid('package', '流程包字段不完整或包含未知字段。')
        for field in ('schema_version', 'workflow_id', 'graph', 'input_contract', 'output_contract', 'nodes', 'config_directory'):
            if package[field] != reference[field]:
                invalid(field, '内置图、契约、实现标识和配置基准不可编辑；代码升级后请创建新草稿。')
        if not isinstance(package['prompts'], dict) or set(package['prompts']) != set(reference['prompts']):
            invalid('prompts', '缺少模型节点提示词或存在未知提示词。')
        for identity, prompt in package['prompts'].items():
            validate_prompt(prompt, identity)
        model = deepcopy(package['model'])
        if not isinstance(model, dict):
            invalid('model', '模型参数必须为对象。')
        model['base_url'] = model.get('base_url') or ''
        model['max_tokens'] = model.get('max_tokens') or 0
        try:
            ModelSettings.model_validate(model)
        except ValidationError as error:
            raise AssetValidationError([{'field': 'model.' + '.'.join(map(str, e['loc'])), 'message': '参数类型或取值不合法，请按模型设置的范围填写。'} for e in error.errors()]) from None
        node_config = package['node_config']
        if not isinstance(node_config, dict) or set(node_config) != set(reference['node_config']):
            invalid('node_config', '输出配置必须归属原有输出节点。')
        if node_config:
            try:
                if any(set(node_config[n]) != set(reference['node_config'][n]) for n in node_config):
                    invalid('node_config', '输出节点包含未知字段。')
                outputs = SettingsData.model_validate({**node_config['write_review'], **node_config['publish'], **node_config['report']})
                for field in ('review', 'report'):
                    resolve_directory(getattr(outputs, field).dir, Path(package['config_directory']))
                if outputs.blog.repo_path:
                    from inkflow.services.publish import resolve_blog_destination
                    resolve_blog_destination(resolve_directory(outputs.blog.repo_path, Path(package['config_directory'])), outputs.blog.content_dir, 'target-preview')
            except (ValidationError, TypeError, KeyError):
                invalid('node_config', '请填写有效的审阅稿、报告与博客输出配置。')
        if key and key in json.dumps(package, ensure_ascii=False):
            invalid('package', '内容含当前模型密钥，请移除；版本只保留凭据引用。')

    def runtime_config(self, version, no_publish=False):
        from inkflow.llm import LLMConfig
        package = version['payload']
        model = ModelSettings.model_validate({**package['model'], 'base_url': package['model']['base_url'] or '', 'max_tokens': package['model']['max_tokens'] or 0})
        llm = LLMConfig(**{**model.model_dump(), 'base_url': model.base_url or None, 'max_tokens': model.max_tokens or None})
        key = self.settings.key(SettingsData(llm=model)) or ''
        outputs = {key: value for config in package['node_config'].values() for key, value in config.items()}
        config = {**outputs, 'llm': asdict(llm), '_config_dir': package['config_directory'], 'publish': {'no_publish': no_publish}, 'node_config': deepcopy(package['node_config'])}
        return config, llm, key

    def instance_keys(self, package):
        return {identity: self.settings.key(SettingsData(llm=ModelSettings.model_validate(spec['model']))) or ''
                for identity, spec in package.get('compiled', {}).get('nodes', {}).items() if spec['type']['uses_model']}
