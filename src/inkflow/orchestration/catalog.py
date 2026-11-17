"""P3 catalog extension; P2 packages are never rewritten in place."""
from copy import deepcopy
import json
from uuid import uuid4
from inkflow.orchestration.definitions import compile_definition, graph_description, template
from inkflow.web.storage import encode


class DefinitionCatalog:
    def __init__(self, catalog):
        from inkflow.orchestration.profiles import PublishProfiles
        self.catalog = catalog
        self.profiles = PublishProfiles(catalog.store, catalog.settings)
        with catalog.store.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS workflow_registry(id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS definition_tests(id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL)')

    def package(self, definition):
        config, _, _ = self.catalog.settings.snapshot()
        return {'schema_version': 2, 'workflow_id': definition['workflow_id'], 'definition': deepcopy(definition),
                'compiled': None, 'graph': graph_description(definition), 'nodes': {}, 'prompts': {},
                'model': deepcopy(config['llm']), 'node_config': {}, 'config_directory': config['_config_dir'],
                'input_contract': 'document-snapshot-v1', 'output_contract': 'node-results-v1'}

    def compile(self, package):
        from inkflow.workflow_assets.catalog import digest
        if package['workflow_id'] != package['definition']['workflow_id']:
            raise ValueError('定义与流程身份不一致。')
        plan = compile_definition(package['definition'], self.profiles)
        # Output directories are resolved once at compilation; no page/workspace context.
        from inkflow.settings import resolve_directory
        from pathlib import Path
        for spec in plan['nodes'].values():
            if 'directory' in spec['config']:
                spec['config']['directory'] = str(resolve_directory(spec['config']['directory'], Path(package['config_directory'])))
        plan['plan_hash'] = digest(plan)
        result = deepcopy(package)
        result.update(compiled=plan, graph=graph_description(package['definition']),
                      nodes={n: {'implementation': spec['type']['implementation']} for n, spec in plan['nodes'].items()})
        return result

    def validate(self, package):
        from inkflow.workflow_assets.prompts import invalid
        if not isinstance(package, dict) or not isinstance(package.get('definition'), dict):
            invalid('definition', '流程包缺少有效定义，请重新加载草稿或从模板创建。')
        if package['definition'].get('schema_version') != 1:
            invalid('definition.schema_version', '定义版本不受支持；请重新加载或迁移到当前定义版本。')
        if set(package) != set(self.package(package['definition'])):
            invalid('package', '流程包字段不完整或包含未知字段。')
        _, _, key = self.catalog.settings.snapshot()
        if key and key in encode(package):
            invalid('package', '定义不能包含当前模型密钥。')
        compiled = self.compile(package)
        if any(secret and secret in encode(package) for secret in self.catalog.instance_keys(compiled).values()):
            invalid('definition', '定义包含节点模型凭据，请改用受控凭据引用。')
        if package.get('compiled') is not None and package != compiled:
            invalid('compiled', '编译包不匹配当前定义或已安装实现；请创建并启用新草稿。')
        return compiled

    def create(self, name, source_id=None, version_id=None):
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError('请填写流程名称，最多 200 字符。')
        identity = 'workflow-' + uuid4().hex
        config, _, _ = self.catalog.settings.snapshot()
        source = self.catalog.version(source_id, version_id) if source_id and version_id else self.catalog.active(source_id) if source_id else None
        profiles = self.profiles.list()
        profile = profiles[0] if profiles else None
        if source and source['payload']['schema_version'] == 2:
            definition = deepcopy(source['payload']['definition'])
            definition.update(workflow_id=identity, name=name.strip())
        else:
            definition = template(identity, name.strip(), source['payload']['model'] if source else config['llm'], profile,
                                  source_id or 'publish-existing')
            if source:
                # Each migrated instance copies prompts and model settings from this exact P2 version.
                for node in definition['nodes']:
                    for pid in node['prompts']:
                        node['prompts'][pid] = deepcopy(source['payload']['prompts'][pid])
                # Preserve P2 output destination explicitly as an immutable profile revision.
                old = source['payload'].get('node_config', {}).get('publish', {}).get('blog')
                if old and old.get('repo_path'):
                    from inkflow.settings import resolve_directory
                    from pathlib import Path
                    migrated = self.profiles.save({'name': name + ' · 原输出配置', 'repo_path': str(resolve_directory(old['repo_path'], Path(source['payload']['config_directory']))), 'content_dir': old['content_dir']})
                    for node in definition['nodes']:
                        if node['node_type_id'] == 'confirm-local-output':
                            node['config'] = {'profile_id': migrated['profile_id'], 'revision': migrated['revision']}
                if source_id == 'reviewed-publishing':
                    from inkflow.orchestration.definitions import new_instance
                    from inkflow.settings import resolve_directory
                    from pathlib import Path
                    for kind, old_id, group, before in [('write-review', 'write_review', 'review', 'n5'), ('write-report', 'report', 'report', 'n7')]:
                        node = new_instance(kind, old_id, config['llm'])
                        directory = source['payload']['node_config'][old_id][group]['dir']
                        node['config']['directory'] = str(resolve_directory(directory, Path(source['payload']['config_directory'])))
                        edge = next(e for e in definition['connections'] if e['target'] == before)
                        upstream = edge['source']
                        node['inputs'] = {'article' if group == 'review' else 'result': {'source': upstream, 'field': 'article' if group == 'review' else 'result'}}
                        edge['target'] = old_id
                        definition['connections'].append({'edge_id': 'migrate-' + old_id, 'source': old_id, 'target': before, 'port': 'next', 'when': None})
                        definition['nodes'].append(node)
                        definition['capabilities'].append('local-review' if group == 'review' else 'local-report')
        definition['source'] = {'workflow_id': source_id, 'version_id': source['id'], 'format': source['payload']['schema_version']} if source else {'template': 'publish-existing-v1'}
        package = self.package(definition)
        with self.catalog.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            revision = self.catalog._next_revision(db)
            db.execute('INSERT INTO workflow_registry VALUES (?,?,?)', (identity, definition['name'], definition['description']))
            db.execute('INSERT INTO workflow_heads VALUES (?, ?, 0)', (identity, ''))
            db.execute('INSERT INTO workflow_drafts VALUES (?,?,?,?)', (identity, revision, source['id'] if source else '', encode(package)))
        return self.catalog.view(identity)

    def list(self, include_drafts=False):
        from inkflow.workflows import WORKFLOWS
        result = [w.summary() for w in WORKFLOWS.values()]
        with self.catalog.store.connection() as db:
            rows = list(db.execute('SELECT r.*,h.version_id FROM workflow_registry r JOIN workflow_heads h ON h.workflow_id=r.id ORDER BY r.rowid'))
        for row in rows:
            if row['version_id'] or include_drafts:
                active = self.catalog.active(row['id'])
                d = active['payload']['definition']
                result.append({'id': row['id'], 'name': d['name'], 'description': d['description'], 'enabled': bool(row['version_id'])})
        return result
