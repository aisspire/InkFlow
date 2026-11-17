"""P2 版本、固定样例、节点诊断和仅脱敏。全部使用模型替身。"""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from inkflow.llm import LLMConfig
from inkflow.llm.core import LLMReply
from inkflow.runtime import WorkflowRuntime
from inkflow.web.app import create_app
from inkflow.workflow_assets.catalog import WorkflowCatalog
from inkflow.workflow_assets.prompts import BUILTINS, AssetValidationError, render


class PromptBaselineTests(unittest.TestCase):
    def test_all_original_messages_and_braces_match_frozen_baseline(self):
        from inkflow.prompts import build_article_messages, build_redaction_messages, build_draft_messages
        from inkflow.services.redaction import _build_apply_redaction_messages
        baseline = json.loads(Path(__file__).with_name('prompt_baseline.json').read_text(encoding='utf-8'))
        self.assertEqual(build_article_messages('正文 {x} {{data}} <X value={1}/>', '2026-11-14', [{'question': '问 {q}', 'answer': '答 {a}'}], '  反馈 {f}  '), baseline['article'])
        self.assertEqual(build_redaction_messages('1: {x}'), baseline['risk'])
        self.assertEqual(build_draft_messages('正文 {x}'), baseline['draft'])
        self.assertEqual(_build_apply_redaction_messages('正文 {x}', [{'action': 'apply', 'user_instruction': '{keep}'}], '  重试  '), baseline['rewrite'])
        text = '# md\n{"x": "{{secret}}"}\n<X code={1}/>\n${file.read()}'
        messages = render(BUILTINS['full-check'], {'payload': {'article': text, 'feedback': '{feedback}'}})
        self.assertEqual(messages[0]['content'], baseline['full'])
        self.assertEqual(json.loads(messages[1]['content']), {'article': text, 'feedback': '{feedback}'})

    def test_template_validation_has_fields_and_no_expression_execution(self):
        for value in ['{{text.upper()}}', '{{__import__("os")}}', '{{unknown}}', '{{text']:
            prompt = deepcopy(BUILTINS['redaction-rewrite'])
            prompt['messages'][1]['content'] += value
            with self.subTest(value=value), self.assertRaises(AssetValidationError) as raised:
                render(prompt, {})
            self.assertIn('messages.1.content', raised.exception.errors[0]['field'])
        with self.assertRaises(AssetValidationError) as raised:
            render(BUILTINS['risk-check'], {})
        self.assertEqual(raised.exception.errors[0]['field'], 'variables.numbered_text')
        with self.assertRaises(AssetValidationError):
            render(BUILTINS['risk-check'], {'numbered_text': 123})
        prompt = deepcopy(BUILTINS['full-check'])
        prompt['output_schema'] = {}
        with self.assertRaises(AssetValidationError):
            render(prompt, {'payload': {}})


class AssetIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='inkflow-p2-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / 'config.toml').write_text('', encoding='utf-8')
        (self.root / 'llm.toml').write_text('[llm]\nenabled = true\n', encoding='utf-8')
        self.key = 'p2-private-credential'
        self.enterContext(patch.dict(os.environ, {'OPENAI_API_KEY': self.key}))
        self.app = create_app(self.root / 'config.toml', self.root / 'data', self.root)
        self.client = self.enterContext(TestClient(self.app))
        self.manager = self.app.state.manager
        self.workflow = 'redaction-only'

    def request(self, method, path, data=None, expected=200, doc=None):
        response = self.client.request(method, '/api' + path, json=data, headers={'X-InkFlow-Document': doc['document_id']} if doc else {})
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def catalog(self, workflow=None):
        return self.request('GET', '/workflow-assets/' + (workflow or self.workflow))

    def draft(self, workflow=None, version=None):
        return self.request('POST', '/workflow-assets/' + (workflow or self.workflow) + '/draft', {'version_id': version})

    def save(self, catalog, package, workflow=None, expected=200):
        return self.request('PUT', '/workflow-assets/' + (workflow or self.workflow) + '/draft', {'revision': catalog['draft']['revision'], 'package': package}, expected)

    def enable(self, catalog, workflow=None, expected=200):
        return self.request('POST', '/workflow-assets/' + (workflow or self.workflow) + '/activate', {'revision': catalog['draft']['revision'], 'generation': catalog['active']['generation']}, expected)

    def doc(self, text='公开笔记\n秘密', path=None):
        doc = self.request('POST', '/documents/open', {'path': str(path)}) if path else self.request('POST', '/documents', {})
        return self.edit(doc, text)

    def edit(self, doc, text):
        return self.request('POST', f"/documents/{doc['document_id']}/operation", {'action': 'edit', 'revision': doc['revision'], 'text': text})

    def start_run(self, doc, workflow=None, expected=201):
        return self.request('POST', '/runs', {'request_id': uuid4().hex, 'raw_text': doc['text'], 'workflow_id': workflow or self.workflow,
                    'editor_source': {'document_id': doc['document_id'], 'revision': doc['revision']}}, expected, doc)

    def wait_run(self, run, doc, status='waiting'):
        for _ in range(300):
            result = self.request('GET', '/runs/' + run['id'], doc=doc)
            if result['status'] in {status, 'failed', 'stopped', 'completed'}:
                self.assertEqual(result['status'], status, result['state'].get('failure'))
                return result
            time.sleep(.01)
        self.fail('run timeout')

    def act(self, run, doc, action, payload=None, expected=200, request_id=None):
        return self.request('POST', f"/runs/{run['id']}/actions", {'request_id': request_id or uuid4().hex, 'pending_id': run['pending']['id'] if run['pending'] else None, 'action': action, 'payload': payload or {}}, expected, doc)

    def finding(self):
        return {'id': 'S001', 'risk': 'high', 'line_start': 2, 'line_end': 2, 'original_excerpt': '秘密', 'issue': '内部信息', 'reason': '不公开', 'suggestion': '移除秘密'}

    def test_migration_repeatable_cas_immutable_versions_rollback_and_shared_prompts(self):
        original = self.catalog()
        first = original['active']['id']
        catalog = self.draft()
        package = deepcopy(catalog['draft']['payload'])
        package['prompts']['risk-check']['messages'][0]['content'] += '\n新修订'
        saved = self.save(catalog, package)
        self.assertEqual(self.catalog()['active']['id'], first)
        self.save(catalog, package, expected=409)
        self.assertEqual(WorkflowCatalog(self.manager.store, self.manager.settings).view(self.workflow)['draft']['revision'], saved['draft']['revision'])
        enabled = self.enable(saved)
        self.assertNotEqual(enabled['active']['id'], first)
        self.assertIsNone(enabled['draft'])
        self.assertNotIn('新修订', self.manager.catalog.version(self.workflow, first)['payload']['prompts']['risk-check']['messages'][0]['content'])
        self.assertNotIn('新修订', self.catalog('reviewed-publishing')['active']['payload']['prompts']['risk-check']['messages'][0]['content'])
        self.assertEqual(set(enabled['shared_prompt_users']['risk-check']), {'reviewed-publishing', 'redaction-only'})
        self.enable(saved, expected=409)
        rollback = self.request('POST', f'/workflow-assets/{self.workflow}/rollback', {'version_id': first, 'generation': enabled['active']['generation']})
        self.assertEqual(rollback['active']['id'], first)
        self.assertEqual(len(rollback['versions']), 2)
        WorkflowCatalog(self.manager.store, self.manager.settings)
        self.assertEqual(self.catalog()['active']['id'], first)

    def test_invalid_package_does_not_replace_last_usable_version(self):
        draft = self.draft()
        package = deepcopy(draft['draft']['payload'])
        package['prompts']['risk-check']['messages'][1]['content'] += '{{missing}}'
        invalid = self.save(draft, package, expected=422)
        self.assertIn('messages.1.content', invalid['errors'][0]['field'])
        self.assertEqual(self.catalog()['active']['id'], draft['active']['id'])
        for key, value in [('model', {'api_key': self.key}), ('nodes', {}), ('prompts', {})]:
            p = deepcopy(draft['draft']['payload']); p[key] = value
            self.save(draft, p, expected=422)
        p = deepcopy(draft['draft']['payload']); p['prompts']['risk-check']['messages'][0]['content'] += self.key
        value = self.save(draft, p, expected=422)
        self.assertNotIn(self.key, json.dumps(value))

    def test_preview_never_calls_model_and_test_never_changes_document(self):
        doc = self.doc('body {data}')
        p = self.catalog('full-check')['active']['payload']
        data = {'node_id': 'full_check', 'package': p, 'variables': {'payload': {'article': 'fixed {x}', 'feedback': ''}}}
        with patch('inkflow.llm.core.call_llm', return_value='{"suggestions": []}') as model:
            preview = self.request('POST', '/workflow-assets/full-check/preview', data)
            model.assert_not_called()
            self.assertIn('fixed {x}', preview['messages'][1]['content'])
            test = self.request('POST', '/workflow-assets/full-check/tests', data)
            result = self.wait_test('full-check', test)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(model.call_count, 1)
        self.assertEqual(self.request('GET', f"/documents/{doc['document_id']}")['revision'], doc['revision'])
        self.assertFalse(self.manager.store.list())
        self.assertFalse((self.root / 'reports').exists())
        self.assertFalse((self.root / 'reviews').exists())
        test_details = self.request('GET', f"/workflow-assets/full-check/tests/{test['id']}/attempts")
        self.assertTrue(all('input' not in attempt and 'messages' not in attempt for attempt in test_details))
        model_attempt = next(a for a in test_details if a['kind'] == 'model')
        detail = self.request('GET', f"/workflow-assets/full-check/tests/{test['id']}/attempts/{model_attempt['id']}")
        self.assertEqual(detail['messages'], preview['messages'])
        self.assertIsNone(detail['usage'])
        self.assertFalse(detail['truncated'])
        self.request('GET', f"/workflow-assets/redaction-only/tests/{test['id']}", expected=404)
        self.request('POST', '/workflow-assets/full-check/preview', {**data, 'node_id': 'freeze'}, expected=422)

    def wait_test(self, workflow, test):
        for _ in range(300):
            value = self.request('GET', f"/workflow-assets/{workflow}/tests/{test['id']}")
            if value['status'] not in {'running', 'stopping'}:
                return value
            time.sleep(.01)
        self.fail('test timeout')

    def test_diagnostics_errors_credentials_usage_and_stop(self):
        p = self.catalog('full-check')['active']['payload']
        data = {'node_id': 'full_check', 'package': p, 'variables': {'payload': {'article': 'sample', 'feedback': ''}}}
        for effect, category in [(TimeoutError('timeout ' + self.key), 'timeout'), (ConnectionError('Bearer remote-secret'), 'connection'), ('not-json', 'output_format'), (RuntimeError('bug ' + self.key), 'code')]:
            with self.subTest(category=category), patch('inkflow.llm.core.call_llm', **({'side_effect': effect} if isinstance(effect, Exception) else {'return_value': effect})):
                result = self.wait_test('full-check', self.request('POST', '/workflow-assets/full-check/tests', data))
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['error']['category'], category)
                attempts = self.manager.diagnostics.list('test', result['id'])
                detail = [self.manager.diagnostics.get('test', result['id'], a['id']) for a in attempts]
                self.assertNotIn(self.key, json.dumps(detail))
                self.assertNotIn('remote-secret', json.dumps(detail))
        with patch('inkflow.llm.core.call_llm', return_value=LLMReply('{"suggestions":[]}', {'total_tokens': 7})):
            result = self.wait_test('full-check', self.request('POST', '/workflow-assets/full-check/tests', data))
        self.assertEqual(next(a for a in self.manager.diagnostics.list('test', result['id']) if a['kind'] == 'model')['usage'], {'total_tokens': 7})
        release, entered = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            entered.set(); release.wait(5); return '{"suggestions":[]}'
        with patch('inkflow.llm.core.call_llm', side_effect=blocked):
            test = self.request('POST', '/workflow-assets/full-check/tests', data)
            self.assertTrue(entered.wait(3))
            self.request('POST', f"/workflow-assets/full-check/tests/{test['id']}/stop", {})
            release.set()
            self.assertEqual(self.wait_test('full-check', test)['status'], 'stopped')
        p = deepcopy(p); p['model']['enabled'] = False
        test = self.request('POST', '/workflow-assets/full-check/tests', {**data, 'package': p})
        self.assertEqual(self.wait_test('full-check', test)['error']['category'], 'disabled')
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}):
            test = self.request('POST', '/workflow-assets/full-check/tests', data)
            self.assertEqual(self.wait_test('full-check', test)['error']['category'], 'credentials')

    def test_inflight_redaction_keeps_version_after_activation_and_new_run_uses_new(self):
        doc = self.doc()
        calls = []
        def model(messages, **kwargs):
            calls.append((deepcopy(messages), kwargs['config']))
            return '公开笔记' if '改写助手' in messages[0]['content'] else json.dumps([self.finding()], ensure_ascii=False) if len(calls) == 1 else '[]'
        with patch('inkflow.services.redaction.call_llm', side_effect=model):
            old = self.wait_run(self.start_run(doc), doc)
            draft = self.draft()
            p = draft['draft']['payload']
            for prompt in p['prompts'].values():
                prompt['messages'][0]['content'] += '\nNEW-PROMPT'
            p['model']['temperature'] = .15
            enabled = self.enable(self.save(draft, p))
            self.act(old, doc, 'review', {'decisions': [{'finding_id': 'S001', 'action': 'apply'}]})
            diff = self.wait_run(old, doc)
            self.assertEqual(diff['pending']['kind'], 'diff_review')
            self.act(diff, doc, 'accept', {'editor': {'document_id': doc['document_id'], 'revision': doc['revision'], 'text': doc['text']}})
            checked = self.wait_run(old, doc)
            self.act(checked, doc, 'review', {'decisions': []})
            ended = self.wait_run(old, doc, 'completed')
            self.assertEqual(ended['workflow_version'], old['workflow_version'])
            self.assertTrue(all('NEW-PROMPT' not in message[0]['content'] for message, _ in calls))
            self.assertTrue(all(config.temperature == .7 for _, config in calls))
            updated = self.request('GET', f"/documents/{doc['document_id']}")
            self.assertEqual(updated['text'], '公开笔记')
            self.assertTrue(updated['draft_pending'])
            new = self.wait_run(self.start_run(updated), updated)
            self.assertEqual(new['workflow_version'], enabled['active']['id'])
            self.assertIn('NEW-PROMPT', calls[-1][0][0]['content'])
            self.assertEqual(calls[-1][1].temperature, .15)
            self.act(new, updated, 'stop')
            self.wait_run(new, updated, 'stopped')
        self.assertFalse(ended['artifacts'])
        self.assertFalse(set(e['node'] for e in ended['executions']) & {'article_generation', 'article_question', 'publish', 'report', 'write_review'})
        attempts = self.request('GET', f"/runs/{old['id']}/attempts", doc=doc)
        self.assertEqual(len({a['id'] for a in attempts}), len(attempts))
        for attempt in attempts:
            self.assertEqual(attempt['workflow_version'], old['workflow_version'])
        other = self.doc('different')
        self.request('GET', f"/runs/{old['id']}/attempts", doc=other, expected=404)
        self.request('GET', f"/runs/{old['id']}/attempts/{attempts[0]['id']}", doc=other, expected=404)

    def test_redaction_no_risk_failure_remaining_risks_and_single_active(self):
        doc = self.doc()
        with patch('inkflow.services.redaction.call_llm', return_value='[]'):
            run = self.wait_run(self.start_run(doc), doc)
            self.start_run(doc, expected=409)
            self.assertTrue(run['pending']['redaction_only'])
            self.act(run, doc, 'review', {'decisions': []})
            final = self.wait_run(run, doc, 'completed')
            self.assertEqual(final['state']['review_status'], 'checked_no_risks')
        with patch('inkflow.services.redaction.call_llm', return_value='broken'):
            failed = self.wait_run(self.start_run(doc), doc, 'failed')
            self.assertEqual(failed['state']['review_status'], 'risk_check_failed')
        with patch('inkflow.services.redaction.call_llm', return_value=json.dumps([self.finding()])):
            run = self.wait_run(self.start_run(doc), doc)
            self.act(run, doc, 'review', {'decisions': [{'finding_id': 'S001', 'action': 'ignore'}]})
            final = self.wait_run(run, doc, 'completed')
            self.assertEqual(final['state']['review_status'], 'ended_with_remaining_risks')
        draft = self.draft(); p = draft['draft']['payload']; p['model']['enabled'] = False
        self.enable(self.save(draft, p))
        self.start_run(doc, expected=422)

    def test_redaction_candidate_conflict_idempotency_and_file_not_written(self):
        path = self.root / 'source.md'; path.write_text('公开笔记\n秘密', encoding='utf-8')
        doc = self.doc(path=path)
        with patch('inkflow.services.redaction.call_llm', side_effect=[json.dumps([self.finding()]), '公开笔记', '[]']):
            run = self.wait_run(self.start_run(doc), doc)
            self.act(run, doc, 'review', {'decisions': [{'finding_id': 'S001', 'action': 'apply'}]})
            pending = self.wait_run(run, doc)
            changed = self.edit(doc, doc['text'] + '\nlocal')
            payload = {'editor': {'document_id': changed['document_id'], 'revision': changed['revision'], 'text': changed['text']}}
            self.act(pending, changed, 'accept', payload, expected=409)
            identity = uuid4().hex
            payload['confirm_conflict'] = True
            first = self.act(pending, changed, 'accept', payload, request_id=identity)
            repeated = self.act(pending, changed, 'accept', payload, request_id=identity)
            self.assertEqual(first, repeated)
            reviewed = self.wait_run(run, doc)
            self.act(reviewed, doc, 'review', {'decisions': []})
            self.wait_run(run, doc, 'completed')
        self.assertEqual(path.read_text(encoding='utf-8'), doc['text'])
        self.assertEqual(self.request('GET', f"/documents/{doc['document_id']}")['text'], '公开笔记')

    def test_article_questions_and_output_directories_remain_fixed(self):
        doc = self.doc('公开内容')
        generation_messages = []
        def article_model(messages, **kwargs):
            generation_messages.append(messages)
            return '{"needs_user_input":true,"question":"写给谁？"}' if len(generation_messages) == 1 else '{"needs_user_input":false,"article":{"title":"Title","body":"Body","slug":"title"}}'
        with patch('inkflow.services.redaction.call_llm', return_value='[]'), patch('inkflow.services.article.call_llm', side_effect=article_model):
            run = self.wait_run(self.start_run(doc, 'reviewed-publishing'), doc)
            draft = self.draft('reviewed-publishing'); p = draft['draft']['payload']
            p['prompts']['article-generation']['messages'][0]['content'] += 'NEW'
            p['node_config']['write_review']['review']['dir'] = 'new-reviews'
            p['node_config']['report']['report']['dir'] = 'new-reports'
            self.enable(self.save(draft, p, 'reviewed-publishing'), 'reviewed-publishing')
            self.act(run, doc, 'review', {'decisions': []})
            question = self.wait_run(run, doc)
            self.assertEqual(question['pending']['kind'], 'article_question')
            self.act(question, doc, 'answer', {'answer': '开发者 {data}'})
            review = self.wait_run(run, doc)
            self.assertEqual(review['pending']['kind'], 'final_review')
            self.assertNotIn('NEW', generation_messages[1][0]['content'])
            self.assertIn('开发者 {data}', generation_messages[1][1]['content'])
            self.assertEqual(Path(review['state']['review_path']).parent, self.root / 'reviews')
            self.act(review, doc, 'finish', {'revision': review['pending']['revision']})
            final = self.wait_run(run, doc, 'completed')
            self.assertEqual(Path(final['state']['report_path']).parent, self.root / 'reports')
        self.assertFalse((self.root / 'new-reviews').exists())

    def test_old_run_unknown_and_restart_preserves_versions_and_drafts(self):
        doc = self.doc()
        with patch('inkflow.services.redaction.call_llm', return_value='[]'):
            run = self.wait_run(self.start_run(doc), doc)
            self.act(run, doc, 'stop'); self.wait_run(run, doc, 'stopped')
        with self.manager.store.connection() as db:
            old = self.manager.store.get(run['id'])
            old.pop('workflow_version'); old.pop('version_package'); old.pop('version_label', None)
            db.execute('UPDATE runs SET payload=? WHERE id=?', (json.dumps(old), run['id']))
        old = self.request('GET', '/runs/' + run['id'], doc=doc)
        self.assertIsNone(old['workflow_version'])
        self.assertEqual(old['version_label'], '旧版，提示词版本未知')
        self.assertNotIn('version_package', old)

    def test_revision_never_reuses_after_activate_and_new_draft(self):
        stale = self.draft()
        self.enable(stale)
        fresh = self.draft()
        self.assertGreater(fresh['draft']['revision'], stale['draft']['revision'])
        self.save(stale, stale['draft']['payload'], expected=409)
        self.assertEqual(self.catalog()['draft']['revision'], fresh['draft']['revision'])

    def test_illegal_stored_draft_activation_is_atomic_and_rollback_only_new_runs(self):
        doc = self.doc()
        draft = self.draft()
        old = draft['active']['id']
        p = deepcopy(draft['draft']['payload']); p['prompts']['risk-check']['messages'][0]['content'] += 'V2'
        enabled = self.enable(self.save(draft, p))
        with patch('inkflow.services.redaction.call_llm', return_value='[]'):
            run = self.wait_run(self.start_run(doc), doc)
            self.request('POST', f'/workflow-assets/{self.workflow}/rollback', {'version_id': old, 'generation': enabled['active']['generation']})
            self.act(run, doc, 'review', {'decisions': []})
            ended = self.wait_run(run, doc, 'completed')
            self.assertEqual(ended['workflow_version'], enabled['active']['id'])
            newer = self.wait_run(self.start_run(doc), doc)
            self.assertEqual(newer['workflow_version'], old)
            self.act(newer, doc, 'stop'); self.wait_run(newer, doc, 'stopped')
        invalid = self.draft()
        p = invalid['draft']['payload']; p['prompts'] = {}
        with self.manager.store.connection() as db:
            db.execute('UPDATE workflow_drafts SET payload=? WHERE workflow_id=?', (json.dumps(p), self.workflow))
        self.enable(invalid, expected=422)
        self.assertEqual(self.catalog()['active']['id'], old)

    def test_real_node_diagnostic_business_validation_and_code_failure(self):
        doc = self.doc('article')
        proposal = json.dumps({'suggestions': [{'kind': 'expression', 'title': 'x', 'reason': 'x', 'original': 'absent', 'replacement': 'x'}]})
        with patch('inkflow.workflows.full_check.call_llm', return_value=proposal):
            run = self.wait_run(self.start_run(doc, 'full-check'), doc, 'failed')
        attempts = self.manager.diagnostics.list('run', run['id'])
        locate = next(a for a in attempts if a['node_id'] == 'locate')
        self.assertEqual(locate['error']['category'], 'business_validation')
        with patch('inkflow.workflows.full_check.call_llm', return_value='{"suggestions":[]}'), patch('inkflow.workflows.full_check.locate', side_effect=RuntimeError('bug ' + self.key)):
            run = self.wait_run(self.start_run(doc, 'full-check'), doc, 'failed')
        locate = next(a for a in self.manager.diagnostics.list('run', run['id']) if a['node_id'] == 'locate')
        self.assertEqual(locate['error']['category'], 'code')
        self.assertNotIn(self.key, str(locate))

    def test_invalid_field_types_and_source_change_report_validation(self):
        draft = self.draft()
        for field, bad in [('role', {}), ('content', 42)]:
            p = deepcopy(draft['draft']['payload'])
            p['prompts']['risk-check']['messages'][0][field] = bad
            response = self.save(draft, p, expected=422)
            self.assertIn('messages.0.' + field, response['errors'][0]['field'])
        p = deepcopy(draft['draft']['payload']); p['prompts']['risk-check']['revision'] = 1
        self.save(draft, p, expected=422)
        with patch('inkflow.workflow_assets.catalog.source_fingerprint', return_value='source-changed'):
            response = self.enable(draft, expected=422)
            self.assertIn('重启', response['detail'])
        self.assertEqual(self.catalog()['active']['id'], draft['active']['id'])

    def test_stop_during_model_call_is_stopped_without_adopting_output(self):
        for workflow, module, response in [('redaction-only', 'inkflow.services.redaction.call_llm', '[]'), ('full-check', 'inkflow.workflows.full_check.call_llm', '{"suggestions":[]}')]:
            doc = self.doc('unchanged')
            entered, release = threading.Event(), threading.Event()
            def blocked(*args, **kwargs):
                entered.set(); release.wait(5); return response
            with self.subTest(workflow=workflow), patch(module, side_effect=blocked):
                run = self.start_run(doc, workflow)
                self.assertTrue(entered.wait(3))
                self.act(run, doc, 'stop')
                release.set()
                final = self.wait_run(run, doc, 'stopped')
                self.assertFalse(final['state'].get('failure'))
                self.assertEqual(self.request('GET', f"/documents/{doc['document_id']}")['revision'], doc['revision'])

    def test_restart_interrupts_tests_without_replaying_and_keeps_history(self):
        from inkflow.workflow_assets.diagnostics import DiagnosticStore
        identity = uuid4().hex
        with self.manager.store.connection() as db:
            db.execute('INSERT INTO model_tests VALUES (?,?,?,?)', (identity, self.workflow, 'test-time', json.dumps({'id': identity, 'status': 'running'})))
        attempt = {'id': uuid4().hex, 'owner_kind': 'test', 'owner_id': identity, 'status': 'running'}
        self.manager.diagnostics.save(attempt)
        with patch('inkflow.llm.core.call_llm') as model:
            recovered = DiagnosticStore(self.manager.store, recover=True)
            self.assertEqual(self.manager.tests.get(self.workflow, identity)['status'], 'interrupted')
            self.assertEqual(recovered.get('test', identity, attempt['id'])['status'], 'interrupted')
            model.assert_not_called()

    def test_credential_filter_never_changes_business_input_or_rendered_messages(self):
        text = '# 示例\nBearer example-token\n{"api_key":"article-example"}\n<X value={{keep}} />'
        doc = self.doc(text)
        with patch('inkflow.services.redaction.call_llm', return_value='[]') as model:
            run = self.wait_run(self.start_run(doc), doc)
            self.assertEqual(run['state']['raw_text'], text)
            self.assertEqual(run['state']['checked_text'], text)
            self.assertIn('Bearer example-token', model.call_args.args[0][1]['content'])
            self.act(run, doc, 'review', {'decisions': []})
            self.wait_run(run, doc, 'completed')
        self.assertEqual(self.request('GET', f"/documents/{doc['document_id']}")['text'], text)
        attempt = next(a for a in self.manager.diagnostics.list('run', run['id']) if a['kind'] == 'model')
        private_copy = self.manager.diagnostics.get('run', run['id'], attempt['id'])
        self.assertNotIn('example-token', json.dumps(private_copy))
        self.assertTrue(private_copy['credentials_filtered'])
        p = self.catalog('full-check')['active']['payload']
        data = {'node_id': 'full_check', 'package': p, 'variables': {'payload': {'article': text, 'feedback': ''}}}
        with patch('inkflow.llm.core.call_llm', return_value='{"suggestions":[]}') as model:
            test = self.wait_test('full-check', self.request('POST', '/workflow-assets/full-check/tests', data))
            self.assertEqual(test['status'], 'completed')
            self.assertEqual(json.loads(model.call_args.args[0][1]['content'])['article'], text)
            self.assertNotIn('example-token', json.dumps(test))
