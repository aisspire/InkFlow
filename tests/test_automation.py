"""P5 source isolation, durable outbox, candidate conflicts and real process recovery."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from inkflow.automation.api import Create
from inkflow.automation.sources import addresses, read_http, rss_content, LIMIT
from inkflow.web.app import create_app
import tempfile
import test_recovery as recovery


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='inkflow-p5-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'config.toml').write_text('', encoding='utf-8')
        self.app = create_app(self.root / 'config.toml', self.root / 'data', self.root)
        self.client = self.enterContext(TestClient(self.app))
        self.service = self.app.state.automations
        self.service.close()  # Deterministic unit scheduling; process tests exercise real scheduler.
        self.manager, self.documents = self.app.state.manager, self.app.state.documents
        self.version = self.service.template()['active']
        self.doc = self.documents.new()
        self.doc = self.documents.mutate(self.doc['document_id'], self.doc['revision'], '# Original\n', 'edit')
        self.path = self.root / 'source.md'
        self.path.write_text('source one', encoding='utf-8')
        self.source = {'kind': 'project', 'workspace_id': self.app.state.default_workspace, 'files': ['source.md']}

    def task(self, **changes):
        body = Create.model_validate({'name': '持续关注', 'source': self.source, 'workflow_id': self.version['workflow_id'],
                                     'document_id': self.doc['document_id'], **changes}).model_dump()
        return self.service.create(body)

    def due(self, task):
        with self.service.store.transaction():
            task = self.service.get(task['id']); task['next_check'] = 0
            self.service._save('automations', task)
        self.service.check(task['id'])
        return self.service.history(task['id'])

    def wait(self, run_id):
        for _ in range(150):
            record = self.manager.detail(run_id)
            if record['status'] not in {'running', 'stopping', 'recovering'}:
                return record
            time.sleep(.02)
        self.fail('run did not settle')

    def test_no_change_dedup_backlog_and_review_candidate(self):
        task = self.task()
        self.service.check(task['id']); self.service.dispatch(task['id'])
        history = self.service.history(task['id']); change = history['changes'][0]
        run = self.wait(change['run_id'])
        self.assertEqual(run['pending']['kind'], 'article_metadata')
        self.assertIn('source one', run['pending']['article']['body'])
        self.assertIn('来源资料', run['pending']['article']['body'])
        self.assertEqual(self.documents.get(self.doc['document_id'])['text'], self.doc['text'])
        self.assertEqual(len(self.due(task)['changes']), 1)
        self.path.write_text('source two', encoding='utf-8')
        self.due(task); self.service.dispatch(task['id'])
        self.assertEqual(len(self.service.changes(task['id'])), 2)
        self.assertEqual(len(self.manager.live), 1)
        self.assertEqual(self.service.list()[0]['backlog'], 1)
        self.assertEqual(self.service.list()[0]['waiting'], 1)
        rows = self.client.get('/api/run-center', params={'automation_id': task['id']}).json()
        self.assertEqual([r['id'] for r in rows], [run['id']])
        self.assertEqual(self.client.get('/api/run-center', params={'automation_id': 'other'}).json(), [])
        self.assertEqual(self.client.get(f"/api/automations/other/snapshots/{change['snapshot_id']}").status_code, 404)

    def test_conflict_does_not_replace_draft_and_retry_cas(self):
        task = self.task(); self.service.check(task['id'])
        edited = self.documents.mutate(self.doc['document_id'], self.doc['revision'], 'human edit', 'edit')
        self.service.dispatch(task['id'])
        c = self.service.changes(task['id'])[0]
        self.assertEqual(c['state'], 'conflict')
        with self.assertRaises(ValueError):
            self.service.retry(task['id'], c['id'], self.doc['revision'])
        self.service.retry(task['id'], c['id'], edited['revision']); self.service.dispatch(task['id'])
        run = self.wait(self.service.changes(task['id'])[0]['run_id'])
        self.assertTrue(run['pending']['article']['body'].startswith('human edit'))
        self.assertEqual(self.documents.get(self.doc['document_id'])['text'], 'human edit')

    def test_cursor_and_change_rollback_together(self):
        task = self.task()
        def fail(point):
            if point == 'automation_before_cursor':
                raise RuntimeError('injected transaction failure')
        with patch('inkflow.web.durable.fault_hook', fail), self.assertRaises(RuntimeError):
            self.service.check(task['id'])
        self.assertEqual(self.service.get(task['id'])['cursor'], {})
        self.assertEqual(self.service.changes(task['id']), [])
        from inkflow.automation.service import Automations
        service = Automations(self.manager, self.documents, start=False)
        service.check(task['id'])
        self.assertEqual(len(service.changes(task['id'])), 1)

    def test_run_commit_before_outbox_ack_reuses_run(self):
        task = self.task(); self.service.check(task['id'])
        def fail(point):
            if point == 'automation_run_created':
                raise RuntimeError('crash before ack')
        with patch('inkflow.web.durable.fault_hook', fail), self.assertRaises(RuntimeError):
            self.service.dispatch(task['id'])
        self.assertIsNone(self.service.changes(task['id'])[0]['run_id'])
        self.service.dispatch(task['id'])
        c = self.service.changes(task['id'])[0]
        self.assertEqual(len(self.manager.live), 1)
        self.assertEqual(self.wait(c['run_id'])['automation_id'], task['id'])

    def test_pause_coalesces_missed_checks_and_does_not_stop_run(self):
        task = self.task(); self.service.check(task['id']); self.service.dispatch(task['id'])
        run_id = self.service.changes(task['id'])[0]['run_id']; self.wait(run_id)
        paused = self.service.control(task['id'], 1, 'pause')
        self.path.write_text('offline change', encoding='utf-8')
        self.assertEqual(len(self.due(task)['changes']), 1)
        self.assertIn(run_id, self.manager.live)
        with self.assertRaises(ValueError):
            self.service.control(task['id'], 1, 'resume')
        resumed = self.service.control(task['id'], paused['generation'], 'resume')
        self.service.check(task['id']); self.service.check(task['id'])
        self.assertEqual(len(self.service.changes(task['id'])), 2)
        self.assertGreater(self.service.get(task['id'])['next_check'], time.time())
        self.assertTrue(resumed['enabled'])

    def test_failure_backoff_and_duplicate_check_claim(self):
        task = self.task()
        calls = []
        def fetch(*args):
            calls.append(1)
            self.service.check(task['id'])
            raise OSError('secret-from-transport')
        with patch('inkflow.automation.service.read_source', fetch):
            self.service.check(task['id'])
        failed = self.service.get(task['id'])
        self.assertEqual(calls, [1]); self.assertEqual(failed['failures'], 1)
        self.assertNotIn('secret', failed['last_error'])
        self.assertGreater(failed['next_check'], time.time() + 100)
        self.assertEqual(failed['cursor'], {})
        self.assertFalse(failed['checking'])
        with self.assertRaises(ValueError):
            self.service.control(task['id'], failed['generation'], 'check')

    def test_fixed_and_latest_version_and_new_document(self):
        fixed = self.task(target_mode='new', document_id='')
        latest = self.task(target_mode='new', document_id='', version_policy='latest')
        draft = self.manager.catalog.create_draft(self.version['workflow_id'])
        package = deepcopy(draft['draft']['payload'])
        package['definition']['nodes'][1]['config']['title'] = 'new version'
        saved = self.manager.catalog.save(self.version['workflow_id'], draft['draft']['revision'], package)
        new = self.manager.catalog.activate(self.version['workflow_id'], saved['draft']['revision'], saved['active']['generation'])['active']
        for task, expected in [(fixed, self.version), (latest, new)]:
            self.service.check(task['id']); self.service.dispatch(task['id'])
            change = self.service.changes(task['id'])[0]
            run = self.wait(change['run_id'])
            self.assertEqual(run['workflow_version'], expected['id'])
            self.assertIsNone(self.documents.get(change['document_id'])['location'])
            self.assertNotEqual(change['document_id'], self.doc['document_id'])

    def test_project_permissions_and_self_output_exclusion(self):
        for path in ['../outside.md', '.env', 'C:\\secret.md', '/tmp/secret.md', 'config.toml']:
            with self.assertRaises(ValueError):
                self.task(source={**self.source, 'files': [path]})
        saved = self.documents.save_as(self.doc['document_id'], self.doc['revision'], self.doc['text'], str(self.root / 'target.md'))
        task = self.task(source={**self.source, 'files': ['source.md', 'target.md', 'output.md']})
        output = self.root / 'output.md'; output.write_text('own output', encoding='utf-8')
        self.manager.durable.set_effect('fixture', 'fixture', 'succeeded', {'path': str(output)})
        self.service.check(task['id'])
        self.path.write_text('external update', encoding='utf-8'); self.due(task)
        self.assertEqual(len(self.service.changes(task['id'])), 2)
        self.documents.mutate(saved['document_id'], saved['revision'], 'own saved update', 'save')
        output.write_text('own second output', encoding='utf-8')
        self.assertEqual(len(self.due(task)['changes']), 2)
        evidence = self.service.evidence(task['id'], self.service.changes(task['id'])[0]['snapshot_id'])
        self.assertNotIn('target.md', evidence['content']); self.assertNotIn('output.md', evidence['content'])

    def test_project_text_and_rst_sources_are_collected_without_article_registration(self):
        names = ['source.md', 'component.mdx', 'notes.txt', 'guide.rst']
        for name in names[1:]:
            (self.root / name).write_text('source ' + name, encoding='utf-8')
        before = self.documents.list_documents()
        response = self.client.post('/api/automations', json={
            'name': 'Text sources', 'source': {**self.source, 'files': names},
            'workflow_id': self.version['workflow_id'], 'document_id': self.doc['document_id'],
        })
        self.assertEqual(response.status_code, 200, response.text)
        task = response.json()
        self.service.check(task['id'])
        change = self.service.changes(task['id'])[0]
        evidence = self.service.evidence(task['id'], change['snapshot_id'])
        self.assertEqual({entry['path'] for entry in json.loads(evidence['content'])}, set(names))
        (self.root / 'notes.txt').write_text('updated text source', encoding='utf-8')
        self.assertEqual(len(self.due(task)['changes']), 2)
        self.assertEqual(self.documents.list_documents(), before)
        for name in names[2:]:
            self.assertEqual(self.client.post('/api/documents/open', json={'path': str(self.root / name)}).status_code, 422)

    def test_source_candidate_confirmation_protects_new_edits(self):
        task = self.task(); self.service.check(task['id']); self.service.dispatch(task['id'])
        c = self.service.changes(task['id'])[0]; run = self.wait(c['run_id'])
        edited = self.documents.mutate(self.doc['document_id'], self.doc['revision'], 'later edit', 'edit')
        body = {'request_id': uuid4().hex, 'pending_id': run['pending']['id'], 'action': 'approve_article', 'payload': {'article': {**run['pending']['article'], 'title': '候选', 'slug': 'candidate', 'date': '2026-11-15'}, 'editor': {k: self.doc[k] for k in ('document_id', 'revision', 'text')}}}
        response = self.client.post(f"/api/runs/{run['id']}/actions?document_id={self.doc['document_id']}", json=body)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.documents.get(edited['document_id'])['text'], 'later edit')
        body['payload']['editor'] = {k: edited[k] for k in ('document_id', 'revision', 'text')}
        body['payload']['confirm_conflict'] = True
        response = self.client.post(f"/api/runs/{run['id']}/actions?document_id={self.doc['document_id']}", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.wait(run['id'])['status'], 'completed')
        self.assertIn('source one', self.documents.get(edited['document_id'])['text'])

    def test_network_policy_rss_normalization_and_limits(self):
        with self.assertRaises(ValueError):
            addresses('http://127.0.0.1/source', 'public')
        with patch('socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('169.254.169.254', 80))]), self.assertRaises(ValueError):
            addresses('http://metadata/source', 'private')
        a = '<rss><channel><lastBuildDate>yesterday</lastBuildDate><item><guid>a</guid><title>A</title></item></channel></rss>'
        self.assertEqual(rss_content(a, 'https://example.org'), rss_content(a.replace('yesterday', 'today'), 'https://example.org'))
        with self.assertRaises(ValueError):
            rss_content('<!DOCTYPE x><rss/>', 'https://example.org')
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                if self.path == '/invalid-length':
                    self.send_response(200); self.send_header('Content-Length', 'secret-response'); self.end_headers(); return
                if self.path == '/redirect':
                    self.send_response(302); self.send_header('Location', 'http://localhost:1/private'); self.end_headers(); return
                if self.path == '/large':
                    self.send_response(200); self.send_header('Content-Length', str(LIMIT + 1)); self.end_headers(); return
                if self.headers.get('If-None-Match') == 'v1':
                    self.send_response(304); self.end_headers(); return
                owner.assertEqual(self.headers.get('Accept-Encoding'), 'identity')
                self.send_response(200); self.send_header('ETag', 'v1'); self.end_headers(); self.wfile.write(b'content')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        url = f'http://127.0.0.1:{server.server_port}'
        source = {'url': url, 'network': 'private'}
        value = read_http(source, {}); self.assertEqual(value['content'], 'content')
        self.assertIsNone(read_http(source, {'etag': value['etag']}))
        for path in ['/redirect', '/large']:
            with self.assertRaises(ValueError):
                read_http({**source, 'url': url + path}, {})
        with self.assertRaisesRegex(ValueError, '^来源响应长度无效。$'):
            read_http({**source, 'url': url + '/invalid-length'}, {})

    def test_same_origin_rate_limit_survives_new_task(self):
        source = {'kind': 'http', 'url': 'https://example.org/one'}
        first, second = self.task(source=source), self.task(source={**source, 'url': 'https://example.org/two'})
        with patch('inkflow.automation.service.read_source', return_value={'url': source['url'], 'content': 'one'}) as fetch:
            self.service.check(first['id']); self.service.check(second['id'])
            self.assertEqual(fetch.call_count, 1)
        self.assertFalse(self.service.get(second['id'])['checking'])
        self.assertGreater(self.service.get(second['id'])['next_check'], time.time())

    def test_invalid_credential_never_appears_in_error_or_snapshot(self):
        task = self.task(source={'kind': 'http', 'url': 'https://example.org', 'credential_ref': 'INKFLOW_SOURCE_TEST'})
        with patch.dict('os.environ', {'INKFLOW_SOURCE_TEST': 'secret\ninvalid'}):
            self.service.check(task['id'])
        failure = self.service.get(task['id'])
        self.assertIn('凭据格式无效', failure['last_error'])
        self.assertNotIn('secret', json.dumps(self.service.history(task['id'])))
        self.assertEqual(self.service.changes(task['id']), [])

    def test_old_blocking_change_remains_visible_without_article_baselines(self):
        task = self.task()
        with self.service.store.transaction() as db:
            for index in range(105):
                self.service._save('automation_changes', {'id': str(index), 'automation_id': task['id'],
                    'state': 'conflict' if index == 0 else 'pending', 'run_id': None, 'baseline': self.doc}, db)
        history = self.service.history(task['id'])
        self.assertEqual(len(history['changes']), 101)
        self.assertEqual(history['changes'][-1]['id'], '0')
        self.assertFalse(any('baseline' in c for c in history['changes']))


class AutomationRecoveryTests(unittest.TestCase):
    setUp = recovery.RecoveryTests.setUp
    cleanup = recovery.RecoveryTests.cleanup
    kill = recovery.RecoveryTests.kill
    start_service = recovery.RecoveryTests.start_service
    req = recovery.RecoveryTests.req
    arm = recovery.RecoveryTests.arm

    def scenario(self, point):
        active = self.req('POST', '/automations/template', {})['active']
        source = self.root / 'source.md'; source.write_text('one', encoding='utf-8')
        spaces = self.req('GET', '/workspaces')['workspaces']
        task = self.req('POST', '/automations', {'name': 'Recovery', 'source': {'kind': 'project', 'workspace_id': spaces[0]['id'], 'files': ['source.md']}, 'workflow_id': active['workflow_id'], 'target_mode': 'new', 'enabled': False, 'interval_seconds': 60})
        self.arm(point)
        self.req('POST', f"/automations/{task['id']}/control", {'generation': 1, 'action': 'resume'})
        self.process.wait(timeout=20)
        self.start_service()
        for _ in range(100):
            detail = self.req('GET', '/automations/' + task['id'])
            if detail['changes'] and detail['changes'][0]['run_id']:
                break
            time.sleep(.1)
        self.assertEqual(len(detail['changes']), 1)
        change = detail['changes'][0]
        self.assertIsNotNone(change['run_id'])
        runs = self.req('GET', '/run-center?automation_id=' + task['id'])
        self.assertEqual(len(runs), 1)
        for _ in range(100):
            run = self.req('GET', f"/runs/{change['run_id']}?document_id={change['document_id']}")
            if run['status'] == 'waiting': break
            time.sleep(.05)
        self.assertEqual(run['status'], 'waiting', run)
        self.assertIn('one', run['pending']['article']['body'])
        pending = run['pending']['id']
        self.kill(); source.write_text('two while offline', encoding='utf-8')
        self.start_service()
        recovered = self.req('GET', f"/runs/{change['run_id']}?document_id={change['document_id']}")
        self.assertEqual(recovered['pending']['id'], pending)
        self.assertEqual(recovered['workflow_version'], active['id'])

    def test_restart_before_cursor_commit(self):
        self.scenario('automation_before_cursor')

    def test_restart_after_cursor_commit(self):
        self.scenario('automation_after_cursor')

    def test_restart_after_run_commit_before_ack(self):
        self.scenario('automation_run_created')
