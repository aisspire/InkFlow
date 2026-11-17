"""P3 definitions, capability boundaries, revision pinning and dynamic execution."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4
from fastapi.testclient import TestClient
from inkflow.web.app import create_app
from inkflow.orchestration.definitions import new_instance
from inkflow.workflow_assets.prompts import AssetValidationError


class DefinitionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='inkflow-p3-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'config.toml').write_text('', encoding='utf-8')
        self.app = create_app(self.root / 'config.toml', self.root / 'data', self.root)
        self.client = self.enterContext(TestClient(self.app))
        self.manager = self.app.state.manager
        self.catalog = self.manager.catalog
        self.profiles = self.catalog.definitions.profiles
        self.profile = self.profiles.save({'name': '临时博客', 'repo_path': str(self.root), 'content_dir': 'output'})
        self.view = self.catalog.definitions.create('发布已有文章')
        self.id = self.view['draft']['workflow_id']
        self.package = deepcopy(self.view['draft']['payload'])
        self.text = '  # 原文\r\n\r\n<X data={{a: 1}} />\r\n{"x": "{{data}}"}\r\n  '

    def request(self, method, path, data=None, status=200, doc=None):
        r = self.client.request(method, '/api' + path, json=data, headers={'X-InkFlow-Document': doc['document_id']} if doc else {})
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def activate(self, package=None, view=None):
        v = view or self.view
        package = package or self.package
        v = self.catalog.save(package['workflow_id'], v['draft']['revision'], package)
        return self.catalog.activate(package['workflow_id'], v['draft']['revision'], v['active']['generation'])

    def doc(self, text=None):
        doc = self.request('POST', '/documents', {})
        return self.request('POST', f"/documents/{doc['document_id']}/operation", {'text': text or self.text, 'revision': doc['revision'], 'action': 'edit'})

    def start(self, doc=None, identity=None):
        doc = doc or self.doc()
        run = self.request('POST', '/runs', {'request_id': uuid4().hex, 'workflow_id': identity or self.id, 'raw_text': doc['text'], 'editor_source': {'document_id': doc['document_id'], 'revision': doc['revision']}}, 201, doc)
        return self.wait(run['id']), doc

    def wait(self, identity, test=False):
        for _ in range(200):
            value = self.manager.flow_tests.get(self.id, identity) if test else self.manager.detail(identity)
            if value['status'] not in {'running', 'stopping'}:
                return value
            time.sleep(.025)
        self.fail('execution did not settle')

    def act(self, run, doc, action, payload=None, status=200, request_id=None):
        if action == 'approve_article':
            payload = {**(payload or {}), 'editor': {k: doc[k] for k in ('document_id', 'revision', 'text')}}
        result = self.request('POST', f"/runs/{run['id']}/actions", {'request_id': request_id or uuid4().hex, 'pending_id': run['pending']['id'], 'action': action, 'payload': payload or {}}, status, doc)
        return self.wait(run['id']) if status == 200 else result

    def article(self, run, slug='existing'):
        return {**run['pending']['article'], 'title': '已有文章', 'slug': slug, 'date': '2026-11-14'}

    def test_dynamic_publication_without_model_exact_body_and_artifact(self):
        enabled = self.activate()
        self.assertIn(self.id, [w['id'] for w in self.request('GET', '/workflows')])
        source_path = self.root / 'existing.mdx'
        source_path.write_bytes(self.text.encode('utf-8'))
        doc = self.request('POST', '/documents/open', {'path': str(source_path)})
        with patch('inkflow.llm.core.call_llm', side_effect=AssertionError('no model')):
            run, doc = self.start(doc)
            self.assertEqual(run['pending']['kind'], 'article_metadata')
            self.assertEqual(run['pending']['article']['body'], self.text)
            run = self.act(run, doc, 'approve_article', {'article': self.article(run)})
            self.assertEqual(run['pending']['kind'], 'local_output')
            target, document = run['pending']['publish_preview'], run['pending']['document']
            self.assertTrue(document.endswith(self.text))
            run = self.act(run, doc, 'confirm_output', {'target': target, 'overwrite': False})
        self.assertEqual(run['status'], 'completed', run['state'])
        self.assertEqual(Path(target['path']).read_bytes(), document.encode())
        self.assertEqual(self.app.state.documents.get(doc['document_id'])['workflow_import']['run_id'], run['id'])
        self.assertTrue(self.app.state.documents.get(doc['document_id'])['text'].startswith('---\ntitle:'))
        self.assertEqual(Path(doc['location']).read_bytes(), self.text.encode('utf-8'))
        self.assertEqual(run['workflow'], enabled['active']['payload']['graph'])
        self.assertEqual(run['artifacts'][0]['source_path'], target['path'])
        self.assertEqual(run['state']['node_results']['n5']['outputs']['result']['status'], 'written')
        attempts = self.manager.diagnostics.list('run', run['id'])
        self.assertEqual({a['node_id'] for a in attempts}, {'n1', 'n2', 'n3', 'n4', 'n5', 'n6'})

    def test_article_confirmation_requires_exact_current_snapshot(self):
        self.activate()
        run, doc = self.start()
        pending = run['pending']['id']
        article = self.article(run)
        other = self.doc('other document')
        self.act(run, other, 'approve_article', {'article': article}, 404)
        forged = {**doc, 'text': 'forged baseline'}
        self.act(run, forged, 'approve_article', {'article': article, 'confirm_conflict': True}, 409)
        changed = self.request('POST', f"/documents/{doc['document_id']}/operation", {'revision': doc['revision'], 'text': 'new draft', 'action': 'edit'})
        self.act(run, doc, 'approve_article', {'article': article, 'confirm_conflict': True}, 409)
        self.assertEqual(self.app.state.documents.get(doc['document_id'])['text'], 'new draft')
        self.assertEqual(self.manager.detail(run['id'])['pending']['id'], pending)
        self.assertFalse((self.root / 'output').exists())
        self.act(run, changed, 'approve_article', {'article': article}, 409)
        resumed = self.act(run, changed, 'approve_article', {'article': article, 'confirm_conflict': True})
        self.assertEqual(resumed['pending']['kind'], 'local_output')
        self.assertTrue(any(v['text'] == 'new draft' for v in self.app.state.documents.versions(doc['document_id'])))

    def test_validation_locates_invalid_connections_types_and_loops(self):
        def check(change, field):
            p = deepcopy(self.package); change(p['definition'])
            with self.assertRaises(AssetValidationError) as raised:
                self.catalog.definitions.compile(p)
            self.assertTrue(any(field in e['field'] for e in raised.exception.errors), raised.exception.errors)
        check(lambda d: d['nodes'].append(deepcopy(d['nodes'][0])), 'node_id')
        check(lambda d: d['nodes'][0].update(node_type_id='shell'), 'node_type_id')
        check(lambda d: d['nodes'][0].update(type_version=99), 'type_version')
        check(lambda d: d['nodes'][1]['config'].update(title=3), 'config')
        check(lambda d: d['nodes'][1]['inputs']['text'].update(field='missing'), 'inputs')
        check(lambda d: d['nodes'][4]['inputs']['publication'].update(source='n2', field='article'), 'inputs')
        check(lambda d: d['connections'].pop(0), 'connections')
        check(lambda d: d.update(ends=['n2']), 'ends')
        check(lambda d: d['connections'].append({'edge_id': 'cycle', 'source': 'n3', 'target': 'n2', 'port': 'next', 'when': None}), 'connections')
        check(lambda d: d['connections'][0].update(when={'field': 'text', 'equals': True}), 'when')
        check(lambda d: d.update(capabilities=[]), 'capabilities')
        check(lambda d: d['nodes'][3]['config'].update(revision='missing'), 'config')
        check(lambda d: d.update(budget={'max_steps': 0}), 'budget')
        p = deepcopy(self.package); p['definition']['canvas']['n1'] = {'x': 9999, 'y': -42}
        self.assertEqual(self.catalog.definitions.compile(p)['compiled'], self.catalog.definitions.compile(self.package)['compiled'])
        p['definition']['nodes'].reverse()
        compiled = self.catalog.definitions.compile(p)
        self.assertEqual([n['id'] for n in compiled['graph']['nodes']], compiled['compiled']['order'])

    def test_incomplete_draft_cas_generation_and_nonreused_revision(self):
        active = self.activate()
        draft = self.catalog.create_draft(self.id)
        p = deepcopy(draft['draft']['payload']); p['definition']['connections'].pop()
        saved = self.catalog.save(self.id, draft['draft']['revision'], p)
        with self.assertRaises(AssetValidationError):
            self.catalog.activate(self.id, saved['draft']['revision'], saved['active']['generation'])
        self.assertEqual(self.catalog.active(self.id)['id'], active['active']['id'])
        from inkflow.web.runs import ConflictError
        with self.assertRaises(ConflictError):
            self.catalog.save(self.id, draft['draft']['revision'], self.package)
        saved = self.catalog.save(self.id, saved['draft']['revision'], self.package)
        with self.assertRaises(ConflictError):
            self.catalog.activate(self.id, saved['draft']['revision'], 0)
        self.catalog.activate(self.id, saved['draft']['revision'], saved['active']['generation'])
        self.assertGreater(self.catalog.create_draft(self.id)['draft']['revision'], saved['draft']['revision'])

    def test_profile_change_pins_old_run_new_run_and_rollback(self):
        original = self.activate()
        run, doc = self.start()
        new = self.profiles.save({**self.profile['payload'], 'content_dir': 'second'}, self.profile['profile_id'], self.profile['generation'])
        self.assertEqual(self.catalog.active(self.id)['payload'], original['active']['payload'])
        v = self.catalog.create_draft(self.id)
        p = deepcopy(v['draft']['payload']); p['definition']['nodes'][3]['config']['revision'] = new['revision']
        updated = self.activate(p, v)
        run = self.act(run, doc, 'approve_article', {'article': self.article(run)})
        self.assertIn('output', run['pending']['publish_preview']['path'])
        run = self.act(run, doc, 'confirm_output', {'target': run['pending']['publish_preview'], 'overwrite': False})
        self.assertEqual(run['status'], 'completed')
        second, doc2 = self.start()
        second = self.act(second, doc2, 'approve_article', {'article': self.article(second)})
        self.assertIn('second', second['pending']['publish_preview']['path'])
        second = self.act(second, doc2, 'confirm_output', {'target': second['pending']['publish_preview'], 'overwrite': False})
        self.assertEqual(second['status'], 'completed')
        self.catalog.rollback(self.id, original['active']['id'], updated['active']['generation'])
        self.assertEqual(self.catalog.active(self.id)['id'], original['active']['id'])
        self.assertTrue((self.root / 'output/existing/index.mdx').is_file())
        self.assertTrue((self.root / 'second/existing/index.mdx').is_file())

    def test_overwrite_fingerprint_invalid_slug_and_failure(self):
        self.activate()
        destination = self.root / 'output/existing/index.mdx'; destination.parent.mkdir(parents=True); destination.write_text('old')
        run, doc = self.start()
        self.act(run, doc, 'approve_article', {'article': self.article(run, '../escape')}, 422)
        run = self.act(run, doc, 'approve_article', {'article': self.article(run)})
        self.act(run, doc, 'confirm_output', {'target': run['pending']['publish_preview'], 'overwrite': False}, 422)
        destination.write_text('changed')
        run = self.act(run, doc, 'confirm_output', {'target': run['pending']['publish_preview'], 'overwrite': True})
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(destination.read_text(), 'changed')
        with self.assertRaises(ValueError):
            self.profiles.save({**self.profile['payload'], 'content_dir': '../escape'})
        run, doc = self.start()
        run = self.act(run, doc, 'approve_article', {'article': self.article(run, 'permission')})
        with patch('inkflow.services.publish.atomic_write', side_effect=PermissionError('denied')):
            run = self.act(run, doc, 'confirm_output', {'target': run['pending']['publish_preview'], 'overwrite': False})
        self.assertEqual(run['status'], 'failed')
        self.assertIn('denied', run['state']['failure'])

    def test_simulation_same_plan_no_output_document_or_formal_slot(self):
        self.activate()
        run, doc = self.start()
        test = self.manager.flow_tests.start(self.id, self.package, self.text)
        test = self.wait(test['id'], True)
        self.assertEqual(test['pending']['kind'], 'article_metadata')
        for action, payload in [('approve_article', {'article': self.article(test)}), ('confirm_output', None)]:
            if payload is None:
                payload = {'target': test['pending']['publish_preview'], 'overwrite': False}
            self.manager.flow_tests.action(self.id, test['id'], {'request_id': uuid4().hex, 'pending_id': test['pending']['id'], 'action': action, 'payload': payload})
            test = self.wait(test['id'], True)
        self.assertEqual(test['status'], 'simulated', test)
        self.assertFalse((self.root / 'output').exists())
        self.assertEqual(self.manager.detail(run['id'])['status'], 'waiting')
        self.assertEqual(self.app.state.documents.get(doc['document_id'])['text'], self.text)
        self.assertEqual(test['state']['values']['n5']['result']['status'], 'simulated')

    def test_same_type_instances_bind_independent_prompt_and_diagnostics(self):
        config, _, _ = self.manager.settings.snapshot()
        p = deepcopy(self.package); d = p['definition']
        a = new_instance('full-check', 'check_a', config['llm']); b = new_instance('full-check', 'check_b', config['llm'])
        for n in (a, b):
            n['model']['enabled'] = True
        a['model']['temperature'] = .1; b['model']['temperature'] = .9
        a['prompts']['full-check']['messages'][0]['content'] += '\nINSTANCE-A'
        b['prompts']['full-check']['messages'][0]['content'] += '\nINSTANCE-B'
        a['inputs']['text'] = {'source': 'n1', 'field': 'text'}; b['inputs']['text'] = {'source': 'check_a', 'field': 'text'}
        d['nodes'] = [d['nodes'][0], a, b, d['nodes'][-1]]
        d['connections'] = [{'edge_id': str(i), 'source': x, 'target': y, 'port': 'next', 'when': None} for i, (x, y) in enumerate(zip(['n1', 'check_a', 'check_b'], ['check_a', 'check_b', 'n6']))]
        d['capabilities'] = ['model', 'human', 'candidate']
        self.activate(p)
        seen = []
        def model(messages, **kw):
            seen.append((messages, kw['config'].temperature)); return '{"suggestions": []}'
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}), patch('inkflow.llm.core.call_llm', model):
            run, doc = self.start()
            self.assertEqual(run['pending']['node'], 'check_a')
            first_pending = run['pending']
            run = self.act(run, doc, 'finish_check')
            self.assertEqual(run['pending']['node'], 'check_b')
            self.assertNotEqual(first_pending['attempt_id'], run['pending']['attempt_id'])
            run = self.act(run, doc, 'finish_check')
        self.assertEqual(run['status'], 'completed', run)
        self.assertEqual([s[1] for s in seen], [.1, .9])
        self.assertIn('INSTANCE-A', seen[0][0][0]['content']); self.assertIn('INSTANCE-B', seen[1][0][0]['content'])
        attempts = self.manager.diagnostics.list('run', run['id'])
        model_attempts = [a for a in attempts if a['kind'] == 'model']
        self.assertEqual({a['node_id'] for a in model_attempts}, {'check_a', 'check_b'})

    def test_migration_copy_readonly_old_ids_and_registry_repeated(self):
        from inkflow.workflow_assets.catalog import WorkflowCatalog
        old = self.catalog.active('redaction-only')
        copy = self.catalog.definitions.create('独立脱敏', 'redaction-only')
        self.assertNotEqual(copy['draft']['workflow_id'], 'redaction-only')
        self.assertEqual(copy['draft']['payload']['definition']['source']['version_id'], old['id'])
        WorkflowCatalog(self.manager.store, self.manager.settings)
        self.assertEqual(self.catalog.active('redaction-only'), old)
        self.assertEqual(self.catalog.view(copy['draft']['workflow_id'])['draft'], copy['draft'])

    def test_controlled_redaction_loop_and_downstream_confirmed_text(self):
        p = deepcopy(self.package); d = p['definition']; config, _, _ = self.manager.settings.snapshot()
        redact = new_instance('redaction', 'sanitize', config['llm']); redact['model']['enabled'] = True
        redact['inputs']['text'] = {'source': 'n1', 'field': 'text'}
        d['nodes'].append(redact); d['connections'][0]['target'] = 'sanitize'
        d['connections'].append({'edge_id': 'after-sanitize', 'source': 'sanitize', 'target': 'n2', 'port': 'next', 'when': None})
        d['nodes'][1]['inputs']['text'] = {'source': 'sanitize', 'field': 'text'}
        d['capabilities'] += ['model', 'candidate']
        self.activate(p)
        finding = json.dumps([{'risk': 'high', 'line_start': 1, 'line_end': 1, 'issue': '风险', 'reason': '样例', 'suggestion': '删除', 'original_excerpt': '# 原文'}], ensure_ascii=False)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}), patch('inkflow.services.redaction.call_llm', side_effect=[finding, '已确认正文\n<X x={1}/>', '[]']):
            run, doc = self.start()
            self.assertEqual(run['pending']['node_id'], 'sanitize')
            f = run['pending']['findings'][0]
            run = self.act(run, doc, 'review', {'decisions': [{'finding_id': f['id'], 'action': 'apply'}]})
            self.assertEqual(run['pending']['kind'], 'diff_review')
            run = self.act(run, doc, 'accept', {'editor': {'document_id': doc['document_id'], 'revision': doc['revision'], 'text': doc['text']}})
            self.assertEqual(run['pending']['kind'], 'redaction_review')
            run = self.act(run, doc, 'review', {'decisions': []})
        self.assertEqual(run['pending']['kind'], 'article_metadata')
        self.assertEqual(run['pending']['article']['body'], '已确认正文\n<X x={1}/>')
        self.assertEqual(self.app.state.documents.get(doc['document_id'])['text'], '已确认正文\n<X x={1}/>')
        self.assertEqual(len([a for a in self.manager.diagnostics.list('run', run['id']) if a['kind'] == 'model']), 3)

    def test_branch_budget_and_instance_model_preview(self):
        config, _, _ = self.manager.settings.snapshot()
        p = deepcopy(self.package); d = p['definition']
        check = new_instance('full-check', 'check', config['llm']); check['model']['enabled'] = True
        check['inputs']['text'] = {'source': 'n1', 'field': 'text'}
        d['nodes'] = [d['nodes'][0], check, d['nodes'][-1], {**deepcopy(d['nodes'][-1]), 'node_id': 'other_end'}]
        d['ends'] = ['n6', 'other_end']; d['capabilities'] = ['model', 'human', 'candidate']
        d['connections'] = [{'edge_id': 'first', 'source': 'n1', 'target': 'check', 'port': 'next', 'when': None},
            {'edge_id': 'yes', 'source': 'check', 'target': 'other_end', 'port': 'next', 'when': {'field': 'has_suggestions', 'equals': True}},
            {'edge_id': 'fallback', 'source': 'check', 'target': 'n6', 'port': 'next', 'when': None}]
        self.activate(p)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}), patch('inkflow.llm.core.call_llm', return_value='{"suggestions": []}') as model:
            preview = self.manager.tests.prepare(self.id, 'check', p, {'payload': {'article': '{{literal}}', 'feedback': ''}}, selected_prompt='full-check')
            model.assert_not_called(); self.assertIn('{{literal}}', preview['messages'][1]['content'])
            run, doc = self.start(); run = self.act(run, doc, 'finish_check')
            self.assertEqual(run['status'], 'completed'); self.assertIn('n6', run['state']['values']); self.assertNotIn('other_end', run['state']['values'])
        draft = self.catalog.create_draft(self.id); p['definition']['budget']['max_steps'] = 1
        self.activate(p, draft)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}), patch('inkflow.llm.core.call_llm') as model:
            run, _ = self.start(); self.assertEqual(run['status'], 'failed'); model.assert_not_called()

    def test_pending_idempotency_scope_and_registered_run_query(self):
        self.activate(); run, doc = self.start()
        other = self.doc('other')
        self.request('GET', f"/runs/{run['id']}?document_id={other['document_id']}", status=404)
        self.request('POST', '/runs', {'request_id': uuid4().hex, 'workflow_id': self.id, 'raw_text': doc['text'], 'editor_source': {'document_id': doc['document_id'], 'revision': doc['revision']}}, 409, doc)
        request = {'request_id': uuid4().hex, 'pending_id': run['pending']['id'], 'action': 'approve_article', 'payload': {'article': self.article(run), 'editor': {k: doc[k] for k in ('document_id', 'revision', 'text')}}}
        first = self.request('POST', f"/runs/{run['id']}/actions", request, doc=doc)
        second = self.request('POST', f"/runs/{run['id']}/actions", request, doc=doc)
        self.assertEqual(first, second)
        request['request_id'] = uuid4().hex
        self.request('POST', f"/runs/{run['id']}/actions", request, 409, doc)
        run = self.wait(run['id'])
        rows = self.request('GET', f'/run-center?workflow_id={self.id}&status=waiting&document_id={doc["document_id"]}')
        self.assertEqual([r['id'] for r in rows], [run['id']])
        self.assertEqual(run['pending']['document_id'], doc['document_id'])

    def test_restart_interrupts_simulation_and_preserves_versions(self):
        from inkflow.orchestration.testing import DefinitionTests as Trials
        self.activate()
        trial = self.manager.flow_tests.start(self.id, self.package, self.text)
        trial = self.wait(trial['id'], True)
        previous = self.catalog.active(self.id)
        self.manager.flow_tests.close()
        self.manager.flow_tests = Trials(self.catalog, self.manager.diagnostics)
        self.assertEqual(self.manager.flow_tests.get(self.id, trial['id'])['status'], 'interrupted')
        self.assertEqual(self.catalog.active(self.id), previous)
        self.assertFalse((self.root / 'output').exists())

    def test_profile_cas_never_overwrite_and_symlink(self):
        from inkflow.web.runs import ConflictError
        first = self.profiles.save({**self.profile['payload'], 'overwrite': 'never'}, self.profile['profile_id'], self.profile['generation'])
        with self.assertRaises(ConflictError):
            self.profiles.save(self.profile['payload'], self.profile['profile_id'], self.profile['generation'])
        self.package['definition']['nodes'][3]['config']['revision'] = first['revision']; self.activate()
        destination = self.root / 'output/existing/index.mdx'; destination.parent.mkdir(parents=True); destination.write_text('old')
        run, doc = self.start(); run = self.act(run, doc, 'approve_article', {'article': self.article(run)})
        self.act(run, doc, 'confirm_output', {'target': run['pending']['publish_preview'], 'overwrite': True}, 422)
        from inkflow.services.publish import preview_blog_target
        with patch.object(Path, 'is_symlink', return_value=True), self.assertRaises(ValueError):
            preview_blog_target(self.root, 'output', 'existing')
