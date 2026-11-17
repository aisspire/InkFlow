"""P6 process death does not resend AI requests or claim interrupted tests passed."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from uuid import uuid4
import httpx


class AIRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='inkflow-p6-process-')
        self.root = Path(self.temp.name)
        (self.root / 'config.toml').write_text('', encoding='utf-8')
        (self.root / 'llm.toml').write_text('[llm]\nenabled=true\n', encoding='utf-8')
        self.log = (self.root / 'server.log').open('wb')
        self.process = None
        self.addCleanup(self.cleanup)
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); self.port = s.getsockname()[1]
        self.start()

    def cleanup(self):
        self.kill(); self.log.close(); self.temp.cleanup()

    def kill(self):
        if self.process and self.process.poll() is None:
            self.process.kill(); self.process.wait(timeout=15)

    def start(self):
        self.process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('ai_worker.py')), str(self.root), str(self.port)], stdout=self.log, stderr=self.log)
        self.until(lambda: self.ready())

    def ready(self):
        if self.process.poll() is not None:
            self.fail((self.root / 'server.log').read_text(errors='replace'))
        try: return httpx.get(f'http://127.0.0.1:{self.port}/api/workflows', timeout=.3).status_code == 200
        except httpx.HTTPError: return False

    def until(self, fn):
        for _ in range(150):
            value = fn()
            if value: return value
            time.sleep(.05)
        self.fail('timed out')

    def req(self, method, path, data=None, status=200, document_id=None):
        headers = {'X-InkFlow-Document': document_id} if document_id else {}
        r = httpx.request(method, f'http://127.0.0.1:{self.port}/api' + path, json=data, headers=headers, timeout=10)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def proposal(self, goal='保留正文'):
        return self.req('POST', '/ai/proposals', {'name': 'Process AI', 'goal': goal, 'sample': 'text', 'capabilities': ['human', 'candidate', 'model']})

    def settled(self, identity):
        return self.until(lambda: v if (v := self.req('GET', f'/ai/proposals/{identity}'))['status'] != 'generating' else None)

    def test_generation_crash_keeps_evidence_and_does_not_resend(self):
        p = self.proposal('slow')
        self.until(lambda: (self.root / 'ai-calls.jsonl').exists())
        self.kill(); self.start()
        p = self.req('GET', f"/ai/proposals/{p['id']}")
        self.assertEqual(p['status'], 'interrupted')
        self.assertIsNone(p['analysis']); self.assertIsNone(p['verification'])
        self.assertEqual(len((self.root / 'ai-calls.jsonl').read_text().splitlines()), 1)
        self.assertIsNotNone(self.req('GET', f"/workflow-assets/{p['workflow_id']}")['draft'])

    def test_verified_version_and_repair_link_survive_real_restart(self):
        doc = self.req('POST', '/documents', {})
        doc = self.req('POST', f"/documents/{doc['document_id']}/operation", {'action': 'edit', 'text': 'original text', 'revision': doc['revision']})
        run = self.req('POST', '/runs', {'request_id': uuid4().hex, 'workflow_id': 'full-check', 'raw_text': doc['text'], 'editor_source': {k: doc[k] for k in ('document_id', 'revision')}}, 201, doc['document_id'])
        self.until(lambda: self.req('GET', f"/runs/{run['id']}", document_id=doc['document_id'])['status'] == 'failed')
        attempts = self.req('GET', f"/runs/{run['id']}/attempts", document_id=doc['document_id'])
        a = next(a for a in attempts if a['status'] == 'failed')
        repair = {'run_id': run['id'], 'node_id': a['node_id'], 'attempt_id': a['id']}
        p = self.req('POST', '/ai/proposals', {'name': 'repair', 'goal': '修复提示词', 'sample': 'original text', 'capabilities': ['human', 'candidate', 'model'], 'repair_of': repair}, document_id=doc['document_id'])
        prefix = f"/ai/proposals/{p['id']}"
        def req(method, suffix='', data=None):
            return self.req(method, prefix + suffix, data, document_id=doc['document_id'])
        p = self.until(lambda: v if (v := req('GET'))['status'] != 'generating' else None)
        p = req('POST', '/apply', {'revision': p['revision'], 'generation': 0})
        samples = [{'name': 'fixed', 'text': 'original text', 'actions': [{'node_id': 'n2', 'kind': 'suggestion_review', 'action': 'finish_check'}]}]
        p = req('POST', '/verify', {'allow_model': True, 'samples': samples})
        p = self.until(lambda: v if (v := req('GET'))['verification']['status'] != 'running' else None)
        self.assertEqual(p['verification']['status'], 'passed', p['verification'])
        self.assertEqual(p['verification']['results'][0]['baseline']['status'], 'failed')
        p = req('POST', '/activate', {'revision': p['applied_revision'], 'generation': 0})
        self.kill(); self.start()
        self.assertEqual(req('GET')['activated_version'], p['activated_version'])
        new = req('POST', '/runs', {'request_id': uuid4().hex, 'document_id': doc['document_id'], 'revision': doc['revision'], 'text': doc['text']})
        new = self.until(lambda: v if (v := self.req('GET', f"/runs/{new['id']}", document_id=doc['document_id']))['status'] == 'waiting' else None)
        self.assertEqual(new['repair_of']['attempt_id'], a['id'])
        pending = new['pending']['id']
        self.kill(); self.start()
        restored = self.req('GET', f"/runs/{new['id']}", document_id=doc['document_id'])
        self.assertEqual(restored['pending']['id'], pending)
        self.assertEqual(restored['repair_of'], new['repair_of'])
        self.assertEqual(len((self.root / 'ai-calls.jsonl').read_text().splitlines()), 1)

    def test_waiting_verification_restarts_as_interrupted_and_cannot_enable(self):
        profile = self.req('POST', '/publish-profiles', {'payload': {'name': 'isolated', 'repo_path': str(self.root), 'content_dir': 'output'}})
        p = self.req('POST', '/ai/proposals', {'name': 'waiting baseline', 'goal': '保留正文', 'sample': 'text', 'capabilities': ['human', 'candidate', 'model', 'local-blog'], 'profile_refs': [{'profile_id': profile['profile_id'], 'revision': profile['revision']}]})
        p = self.settled(p['id'])
        prefix = f"/ai/proposals/{p['id']}"
        p = self.req('POST', prefix + '/apply', {'revision': p['revision'], 'generation': 0})
        p = self.req('POST', prefix + '/verify', {'samples': [{'name': 'fixed', 'text': 'text'}]})
        self.until(lambda: self.req('GET', prefix)['verification']['results'][0]['baseline']['status'] == 'waiting')
        self.kill(); self.start()
        restored = self.req('GET', prefix)
        self.assertEqual(restored['verification']['results'][0]['baseline']['status'], 'interrupted')
        self.assertEqual(restored['verification']['status'], 'failed')
        self.req('POST', prefix + '/activate', {'revision': p['applied_revision'], 'generation': 0}, 409)
        self.assertEqual(list((self.root / 'output').glob('**/*')), [])
