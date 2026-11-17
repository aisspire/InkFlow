"""P4 real process death, SQLite recovery and output evidence, isolated temp resources."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from inkflow.web.app import create_app


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='inkflow-p4-')
        self.root = Path(self.temp.name)
        self.process = None
        self.log = open(self.root / 'service.log', 'wb')
        self.addCleanup(self.cleanup)
        (self.root / 'config.toml').write_text('', encoding='utf-8')
        with TestClient(create_app(self.root / 'config.toml', self.root / 'data', self.root)) as client:
            manager = client.app.state.manager
            profile = manager.catalog.definitions.profiles.save({'name': 'P4 isolated', 'repo_path': str(self.root), 'content_dir': 'output'})
            view = manager.catalog.definitions.create('P4 recovery')
            package = deepcopy(view['draft']['payload'])
            package['definition']['nodes'][3]['config'].update(profile_id=profile['profile_id'], revision=profile['revision'])
            self.workflow = package['workflow_id']
            saved = manager.catalog.save(self.workflow, view['draft']['revision'], package)
            active = manager.catalog.activate(self.workflow, saved['draft']['revision'], saved['active']['generation'])
            self.version = active['active']['id']
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); self.port = s.getsockname()[1]
        self.url = f'http://127.0.0.1:{self.port}/api'
        self.start_service()

    def cleanup(self):
        self.kill()
        self.log.close()
        self.temp.cleanup()

    def kill(self):
        if self.process and self.process.poll() is None:
            self.process.kill(); self.process.wait(timeout=15)

    def start_service(self):
        self.process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('recovery_worker.py')), str(self.root), str(self.port)], stdout=self.log, stderr=self.log)
        for _ in range(150):
            if self.process.poll() is not None:
                self.fail((self.root / 'service.log').read_text(errors='replace'))
            try:
                if httpx.get(self.url + '/workflows', timeout=.3).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(.04)
        self.fail('service did not start')

    def req(self, method, path, body=None, expected=200, doc=None):
        response = httpx.request(method, self.url + path, json=body, headers={'X-InkFlow-Document': doc['document_id']} if doc else {}, timeout=15)
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def wait(self, identity, states=('waiting', 'completed', 'verification_required', 'recovery_blocked', 'failed', 'stopped')):
        for _ in range(200):
            run = self.req('GET', '/runs/' + identity, doc=self.doc)
            if run['status'] in states:
                return run
            time.sleep(.025)
        self.fail('run did not settle')

    def begin(self):
        doc = self.req('POST', '/documents', {})
        self.doc = self.req('POST', '/documents/' + doc['document_id'] + '/operation', {'action': 'edit', 'revision': doc['revision'], 'text': '# Original\n\n<X />\n'})
        run = self.req('POST', '/runs', {'request_id': uuid4().hex, 'workflow_id': self.workflow, 'raw_text': self.doc['text'], 'editor_source': {k: self.doc[k] for k in ('document_id', 'revision')}}, 201, self.doc)
        return self.wait(run['id'])

    def action(self, run, action, payload=None, request_id=None):
        return self.req('POST', '/runs/' + run['id'] + '/actions', {'request_id': request_id or uuid4().hex, 'pending_id': (run['pending'] or {}).get('id'), 'action': action, 'payload': payload or {}}, doc=self.doc)

    def review(self, run):
        self.action(run, 'approve_article', {'article': {**run['pending']['article'], 'title': 'P4', 'slug': 'p4', 'date': '2026-11-15'}, 'editor': {k: self.doc[k] for k in ('document_id', 'revision', 'text')}})
        return self.wait(run['id'])

    def arm(self, point, node=None):
        (self.root / 'fault.json').write_text(json.dumps({'point': point, 'node': node}), encoding='utf-8')

    def crashed(self):
        self.assertEqual(self.process.wait(timeout=15), 73)
        self.assertTrue((self.root / 'crashed.txt').exists())

    def publish_crash(self, point, external=False):
        run = self.review(self.begin())
        self.assertEqual(run['pending']['kind'], 'local_output')
        target = Path(run['pending']['publish_preview']['path'])
        self.arm(point, 'n5')
        self.action(run, 'confirm_output', {'target': run['pending']['publish_preview'], 'overwrite': False})
        self.crashed()
        stamp = target.stat().st_mtime_ns if target.exists() else None
        if external:
            target.write_text('EXTERNAL', encoding='utf-8')
        self.start_service()
        recovered = self.wait(run['id'])
        return recovered, target, stamp

    def test_waiting_restart_same_pending_and_fixed_version(self):
        run = self.begin(); original_pending = run['pending']
        self.kill(); self.start_service()
        recovered = self.wait(run['id'])
        self.assertEqual(recovered['pending'], original_pending)
        self.assertEqual(recovered['workflow_version'], self.version)
        self.assertEqual(recovered['state']['raw_text'], '# Original\n\n<X />\n')
        run = self.review(recovered)
        self.action(run, 'confirm_output', {'target': run['pending']['publish_preview'], 'overwrite': False})
        self.assertEqual(self.wait(run['id'])['status'], 'completed')

    def test_accepted_action_survives_death_once_with_working_copy(self):
        run = self.begin(); identity = uuid4().hex
        body = {'article': {**run['pending']['article'], 'title': 'P4', 'slug': 'p4', 'date': '2026-11-15'}, 'editor': {k: self.doc[k] for k in ('document_id', 'revision', 'text')}}
        self.arm('action_committed')
        receipt = self.action(run, 'approve_article', body, identity)
        self.crashed(); self.start_service()
        recovered = self.wait(run['id'])
        self.assertEqual(recovered['pending']['kind'], 'local_output')
        self.assertEqual(self.action(run, 'approve_article', body, identity), receipt)
        self.req('POST', '/runs/' + run['id'] + '/actions', {'request_id': uuid4().hex, 'pending_id': run['pending']['id'], 'action': 'approve_article', 'payload': body}, 409, self.doc)
        versions = self.req('GET', '/documents/' + self.doc['document_id'] + '/versions')
        self.assertEqual(sum(v['operation'] == 'workflow_apply' for v in versions), 1)

    def test_written_without_receipt_reconciles_without_write(self):
        run, target, stamp = self.publish_crash('effect_written')
        self.assertEqual(run['status'], 'completed', run.get('recovery_message'))
        self.assertEqual(target.stat().st_mtime_ns, stamp)
        self.assertTrue(all(e['state'] == 'succeeded' for e in run['effects']))
        self.kill(); self.start_service()
        self.assertEqual(self.wait(run['id'])['status'], 'completed')
        self.assertEqual(target.stat().st_mtime_ns, stamp)

    def test_external_edit_of_unknown_output_is_never_overwritten(self):
        run, target, _ = self.publish_crash('effect_written', external=True)
        self.assertEqual(run['status'], 'verification_required')
        self.assertEqual(target.read_text(), 'EXTERNAL')
        self.action(run, 'reconcile')
        self.assertEqual(self.wait(run['id'])['status'], 'verification_required')
        self.assertEqual(target.read_text(), 'EXTERNAL')
        self.action(run, 'stop')
        self.assertEqual(self.wait(run['id'])['status'], 'stopped')

    def test_before_write_is_unknown_and_requires_verification(self):
        run, target, _ = self.publish_crash('effect_before')
        self.assertEqual(run['status'], 'verification_required')
        self.assertFalse(target.exists())

    def test_node_complete_before_checkpoint_does_not_repeat_output(self):
        run, target, stamp = self.publish_crash('node_committed')
        self.assertEqual(run['status'], 'completed', run)
        self.assertEqual(target.stat().st_mtime_ns, stamp)

    def test_node_return_before_result_uses_confirmed_effect(self):
        run, target, stamp = self.publish_crash('node_returned')
        self.assertEqual(run['status'], 'completed', run)
        self.assertEqual(target.stat().st_mtime_ns, stamp)

    def test_downtime_document_revision_conflict_preserves_pending(self):
        run = self.begin(); self.kill()
        # The ordinary document service may edit while execution is offline.
        from inkflow.settings import SettingsStore
        from inkflow.web.documents import DocumentService
        documents = DocumentService(SettingsStore(self.root / 'config.toml', self.root / 'data'))
        changed = documents.mutate(self.doc['document_id'], self.doc['revision'], 'changed while offline', 'edit')
        self.start_service()
        self.req('POST', '/runs/' + run['id'] + '/actions', {'request_id': uuid4().hex, 'pending_id': run['pending']['id'], 'action': 'approve_article', 'payload': {'article': {**run['pending']['article'], 'title': 'P4', 'slug': 'p4', 'date': '2026-11-15'}, 'confirm_conflict': True, 'editor': {k: self.doc[k] for k in ('document_id', 'revision', 'text')}}}, 409, self.doc)
        self.assertEqual(self.wait(run['id'])['pending'], run['pending'])
        self.assertEqual(self.req('GET', '/documents/' + self.doc['document_id'])['text'], changed['text'])

    def test_duplicate_process_cannot_steal_expired_lease(self):
        run = self.begin()
        with closing(sqlite3.connect(self.root / 'data/history.sqlite3')) as db, db:
            db.execute("UPDATE execution_work SET lease=0 WHERE run_id=?", (run['id'],))
        duplicate = subprocess.run([sys.executable, str(Path(__file__).with_name('recovery_worker.py')), str(self.root), str(self.port+1)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertEqual(self.wait(run['id'])['pending'], run['pending'])

    def test_incompatible_and_legacy_runs_have_explicit_boundaries(self):
        run = self.begin(); self.kill()
        with closing(sqlite3.connect(self.root / 'data/history.sqlite3')) as db, db:
            row = json.loads(db.execute('SELECT payload FROM runs WHERE id=?', (run['id'],)).fetchone()[0])
            row['implementation_fingerprint'] = 'old-code'
            db.execute('UPDATE runs SET payload=? WHERE id=?', (json.dumps(row), run['id']))
        self.start_service()
        self.assertEqual(self.wait(run['id'])['status'], 'recovery_blocked')
        self.action(run, 'stop'); self.kill()
        with closing(sqlite3.connect(self.root / 'data/history.sqlite3')) as db, db:
            row = json.loads(db.execute('SELECT payload FROM runs WHERE id=?', (run['id'],)).fetchone()[0])
            row.pop('recovery_protocol'); row['status'] = 'waiting'
            db.execute("UPDATE runs SET payload=?,status='waiting' WHERE id=?", (json.dumps(row), run['id']))
        self.start_service()
        self.assertEqual(self.wait(run['id'], ('interrupted',))['status'], 'interrupted')

    def checkpoint_crash(self, point):
        run = self.begin()
        self.arm(point)
        self.review(run)
        self.crashed(); self.start_service()
        recovered = self.wait(run['id'])
        self.assertEqual(recovered['pending']['kind'], 'local_output')
        self.assertNotEqual(recovered['pending']['id'], run['pending']['id'])

    def test_checkpoint_commit_before_crash(self):
        # Action response commits before the worker reaches the checkpoint fault.
        run = self.begin(); self.arm('checkpoint_after')
        self.action(run, 'approve_article', {'article': {**run['pending']['article'], 'title': 'P4', 'slug': 'p4', 'date': '2026-11-15'}, 'editor': {k: self.doc[k] for k in ('document_id', 'revision', 'text')}})
        self.crashed(); self.start_service()
        self.assertEqual(self.wait(run['id'])['pending']['kind'], 'local_output')

    def test_checkpoint_before_commit_crash(self):
        run = self.begin(); self.arm('checkpoint_before')
        self.action(run, 'approve_article', {'article': {**run['pending']['article'], 'title': 'P4', 'slug': 'p4', 'date': '2026-11-15'}, 'editor': {k: self.doc[k] for k in ('document_id', 'revision', 'text')}})
        self.crashed(); self.start_service()
        self.assertEqual(self.wait(run['id'])['pending']['kind'], 'local_output')

    def test_new_version_and_profile_do_not_replace_recovered_plan(self):
        run = self.begin()
        route = '/workflow-assets/' + self.workflow
        view = self.req('POST', route + '/draft', {})
        package = view['draft']['payload']
        binding = package['definition']['nodes'][3]['config']
        profile = self.req('GET', '/publish-profiles')[0]
        newer = self.req('PUT', '/publish-profiles/' + profile['profile_id'], {'generation': profile['generation'], 'payload': {**profile['payload'], 'content_dir': 'new-output'}})
        binding['revision'] = newer['revision']
        saved = self.req('PUT', route + '/draft', {'revision': view['draft']['revision'], 'package': package})
        active = self.req('POST', route + '/activate', {'revision': saved['draft']['revision'], 'generation': saved['active']['generation']})
        self.assertNotEqual(active['active']['id'], run['workflow_version'])
        self.kill(); self.start_service()
        recovered = self.review(self.wait(run['id']))
        self.assertEqual(recovered['workflow_version'], run['workflow_version'])
        self.assertIn(str(self.root / 'output'), recovered['pending']['publish_preview']['path'])
        self.assertEqual(recovered['version_package']['compiled'], run['version_package']['compiled'])


class RecoveryBoundaryTests(unittest.TestCase):
    def setUp(self):
        from inkflow.web.storage import HistoryStore
        from inkflow.web.durable import DurableStore
        self.temp = tempfile.TemporaryDirectory(prefix='inkflow-p4-boundary-')
        self.store = HistoryStore(Path(self.temp.name))
        self.durable = DurableStore(self.store)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.durable.process_lock.close)

    def test_duplicate_claim_and_expiry_do_not_authorize_live_owner_takeover(self):
        self.durable.enqueue('test')
        self.assertTrue(self.durable.claim('test'))
        self.assertFalse(self.durable.claim('test'))
        with self.store.connection() as db:
            db.execute("UPDATE execution_work SET lease=0 WHERE run_id='test'")
        self.assertFalse(self.durable.claim('test'))
        self.durable.heartbeat()
        self.assertGreater(self.durable.work('test')['lease'], time.time())

    def test_model_unknown_is_not_reissued_and_completed_reply_is_cached(self):
        from inkflow.web import durable
        from unittest.mock import Mock
        context = {'store': self.durable, 'run': 'test', 'id': 'node-step', 'node': 'model', 'call': 0}
        token = durable._context.set(context)
        try:
            with self.store.connection() as db:
                db.execute("INSERT INTO execution_calls VALUES ('test','node-step:1','started',NULL)")
            transport = Mock(return_value='output')
            with self.assertRaises(durable.RecoveryHold):
                durable.model_call(transport, [])
            transport.assert_not_called()
            context['id'] = 'completed'; context['call'] = 0
            self.assertEqual(durable.model_call(transport, []), 'output')
            context['call'] = 0
            self.assertEqual(durable.model_call(transport, []), 'output')
            self.assertEqual(transport.call_count, 1)
        finally:
            durable._context.reset(token)

    def test_effect_evidence_retains_execution_order_for_repeated_target(self):
        self.durable.set_effect('run', 'z-first', 'succeeded', {'path': 'same', 'content': 'first'})
        self.durable.set_effect('run', 'a-last', 'succeeded', {'path': 'same', 'content': 'latest'})
        self.durable.set_effect('run', 'z-first', 'succeeded', {'path': 'same', 'content': 'first'})
        self.assertEqual(self.durable.effects('run')[-1]['content'], 'latest')

    def test_cross_service_transaction_rolls_back_document_and_action_together(self):
        from inkflow.web.storage import HistoryStore
        other = HistoryStore(self.store.directory)
        with self.assertRaises(RuntimeError):
            with self.store.transaction():
                with other.connection() as db:
                    db.execute("INSERT INTO execution_calls VALUES ('test','rollback','started',NULL)")
                raise RuntimeError('injected before commit')
        with self.store.connection() as db:
            self.assertIsNone(db.execute("SELECT * FROM execution_calls WHERE id='rollback'").fetchone())


class BuiltinRecoveryTests(unittest.TestCase):
    # Reuse existing integration fixture helpers without duplicating its test cases.
    from test_workflow_assets import AssetIntegrationTests as _Fixture
    setUp = _Fixture.setUp
    request = _Fixture.request
    doc = _Fixture.doc
    edit = _Fixture.edit
    start_run = _Fixture.start_run
    wait_run = _Fixture.wait_run
    act = _Fixture.act
    del _Fixture

    def test_three_builtin_waits_resume_same_identity_without_repeating_model(self):
        from unittest.mock import patch
        from inkflow.web.runs import RunManager
        with patch('inkflow.workflows.full_check.call_llm', return_value='{"suggestions":[]}') as full, patch('inkflow.services.redaction.call_llm', return_value='[]') as redact:
            for workflow in ('full-check', 'redaction-only', 'reviewed-publishing'):
                with self.subTest(workflow=workflow):
                    doc = self.doc('fixed builtin input')
                    run = self.wait_run(self.start_run(doc, workflow), doc)
                    pending = deepcopy(run['pending']); calls = (full.call_count, redact.call_count)
                    self.manager.close()
                    self.manager = RunManager(self.app.state.documents.settings, self.app.state.documents.document_source, self.app.state.documents)
                    self.app.state.manager = self.manager
                    self.addCleanup(self.manager.close)
                    restored = self.manager.detail(run['id'])
                    self.assertEqual(restored['pending'], pending)
                    self.assertEqual((full.call_count, redact.call_count), calls)
                    if workflow == 'full-check':
                        self.act(restored, doc, 'finish_check')
                        self.wait_run(run, doc, 'completed')
                    elif workflow == 'redaction-only':
                        self.act(restored, doc, 'review', {'decisions': []})
                        self.wait_run(run, doc, 'completed')
                    else:
                        self.act(restored, doc, 'stop')
                        self.wait_run(run, doc, 'stopped')


class StopOutputRaceTests(unittest.TestCase):
    from test_orchestration import DefinitionTests as _Fixture
    setUp = _Fixture.setUp
    request = _Fixture.request
    activate = _Fixture.activate
    doc = _Fixture.doc
    start = _Fixture.start
    wait = _Fixture.wait
    act = _Fixture.act
    article = _Fixture.article
    del _Fixture

    def test_stop_after_file_receipt_wins_completion_without_rewriting(self):
        import threading
        from unittest.mock import patch
        from inkflow.web import durable
        written, release = threading.Event(), threading.Event()
        self.activate()
        run, doc = self.start()
        run = self.act(run, doc, 'approve_article', {'article': self.article(run)})
        target = run['pending']['publish_preview']
        def pause(point):
            if point == 'node_returned' and (durable._context.get() or {}).get('node') == 'n5':
                written.set()
                if not release.wait(10):
                    raise RuntimeError('test release timeout')
        with patch.object(durable, 'fault_hook', pause):
            self.request('POST', '/runs/' + run['id'] + '/actions', {'request_id': uuid4().hex, 'pending_id': run['pending']['id'], 'action': 'confirm_output', 'payload': {'target': target, 'overwrite': False}}, doc=doc)
            self.assertTrue(written.wait(10))
            stamp = Path(target['path']).stat().st_mtime_ns
            try:
                self.request('POST', '/runs/' + run['id'] + '/actions', {'request_id': uuid4().hex, 'action': 'stop', 'payload': {}}, doc=doc)
            finally:
                release.set()
            result = self.wait(run['id'])
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(Path(target['path']).stat().st_mtime_ns, stamp)
        self.assertEqual(result['effects'][0]['state'], 'succeeded')
        self.assertEqual(len(result['artifacts']), 1)
        self.assertEqual(result['artifacts'][0]['node_id'], 'n5')
