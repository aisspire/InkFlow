"""P1 身份、增量迁移、文件写入与运行一致性回归；不调用远端模型。"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from starlette.requests import Request

from inkflow.settings import SettingsStore
from inkflow.web.app import create_app
from inkflow.web.documents import DocumentService
from inkflow.web.editor import EditorService
from inkflow.web.storage import encode, now
from inkflow.workflows import get_workflow


class DocumentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='inkflow-p1-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.a, self.b = self.root / 'a', self.root / 'b'
        self.a.mkdir(); self.b.mkdir()
        self.file = self.b / 'same.mdx'
        self.original = '\ufeff---\r\ntitle: 原文\r\n---\n\r\n# 正文 😀\r\n<Component />\n'
        self.file.write_bytes(self.original.encode('utf-8'))
        (self.a / 'same.mdx').write_text('different', encoding='utf-8')
        (self.a / 'empty').mkdir()
        (self.a / 'nested').mkdir()
        (self.a / 'nested' / 'unopened.md').write_text('unopened', encoding='utf-8')
        (self.root / 'config.toml').write_text('', encoding='utf-8')
        (self.root / 'llm.toml').write_text('[llm]\nenabled = true\n', encoding='utf-8')
        self.enterContext(patch.dict(os.environ, {'OPENAI_API_KEY': 'p1-isolated-model-key'}))
        self.app = create_app(self.root / 'config.toml', self.root / 'data', self.a)
        self.client = self.enterContext(TestClient(self.app))
        self.documents = self.app.state.documents

    def post(self, url, data, expected=200):
        response = self.client.post('/api' + url, json=data)
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def open(self, path=None):
        return self.post('/documents/open', {'path': str(path or self.file)})

    def mutate(self, doc, action='edit', text=None, expected=200, **extra):
        return self.post(f"/documents/{doc['document_id']}/operation", {
            'revision': doc['revision'], 'text': text if text is not None else doc['text'], 'action': action, **extra}, expected)

    def get(self, doc):
        response = self.client.get('/api/documents/' + doc['document_id'])
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_external_file_exact_scope_and_format_save(self):
        before = self.client.get('/api/workspaces').json()
        doc = self.open()
        self.assertEqual(self.client.get('/api/workspaces').json(), before)
        self.assertEqual(doc['text'], self.original)
        changed = self.mutate(doc, text=self.original + 'edit\r\n')
        self.assertEqual(self.file.read_bytes(), self.original.encode())
        saved = self.mutate(changed, 'save')
        self.assertEqual(self.file.read_bytes(), saved['text'].encode())
        self.assertEqual(saved['disk_text'], saved['text'])
        self.assertEqual(self.client.get('/api/documents/' + str(self.file)).status_code, 404)
        unknown = self.client.get('/api/documents/' + uuid4().hex)
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(self.client.get('/api/editor/document', params={'path': '../b/same.mdx'}).status_code, 422)

    def delete_draft(self, doc, expected=200):
        response = self.client.request('DELETE', '/api/documents/' + doc['document_id'], json={'revision': doc['revision']})
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def test_delete_untitled_draft_removes_copy_versions_and_survives_restart(self):
        doc = self.post('/documents', {})
        changed = self.mutate(doc, text='draft to delete')
        rows = self.client.get('/api/documents').json()
        self.assertEqual(next(r for r in rows if r['document_id'] == doc['document_id'])['revision'], changed['revision'])
        self.delete_draft(doc, 409)
        self.assertEqual(self.get(changed)['text'], 'draft to delete')
        self.delete_draft(changed)
        self.assertEqual(self.client.get('/api/documents/' + doc['document_id']).status_code, 404)
        self.delete_draft(changed, 404)
        with self.documents.store.connection() as db:
            for table in ('document_copies', 'document_versions'):
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table} WHERE path=?', (doc['document_id'],)).fetchone()[0], 0)
        restarted = DocumentService(SettingsStore(self.root / 'config.toml', self.root / 'data'))
        self.assertNotIn(doc['document_id'], [r['document_id'] for r in restarted.list_documents()])
        self.assertEqual(self.file.read_bytes(), self.original.encode())

    def test_delete_rejects_file_backed_and_missing_file_drafts(self):
        doc = self.open()
        draft = self.mutate(doc, text='keep file draft')
        self.delete_draft(draft, 409)
        self.file.unlink()
        self.delete_draft(draft, 409)
        self.assertEqual(self.get(draft)['text'], 'keep file draft')
        self.assertGreater(len(self.documents.versions(doc['document_id'])), 0)

    def test_delete_blocks_active_run_and_keeps_completed_run_audit(self):
        doc = self.mutate(self.post('/documents', {}), text='# Check this draft')
        with patch('inkflow.workflows.full_check.call_llm', return_value='{"suggestions":[]}'):
            run = self.start_check(doc)
            self.delete_draft(doc, 409)
            self.assertEqual(self.get(doc)['text'], doc['text'])
            self.post(f"/runs/{run['id']}/actions?document_id={doc['document_id']}", {
                'request_id': uuid4().hex, 'pending_id': run['pending']['id'], 'action': 'finish_check', 'payload': {}})
            for _ in range(150):
                if self.app.state.manager.store.get(run['id'])['status'] == 'completed':
                    break
                time.sleep(.02)
            self.assertEqual(self.app.state.manager.store.get(run['id'])['status'], 'completed')
        self.delete_draft(self.get(doc))
        response = self.client.get(f"/api/runs/{run['id']}?document_id={doc['document_id']}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['annotation_snapshot'], doc['text'])
        self.assertEqual(self.client.get('/api/runs', params={'document_id': doc['document_id']}).json()[0]['id'], run['id'])

    def test_delete_blocks_automation_references(self):
        doc = self.post('/documents', {})
        with self.documents.store.connection() as db:
            db.execute('INSERT INTO automations VALUES (?,?)', ('delete-test', encode({'document_id': doc['document_id'], 'enabled': False})))
        self.delete_draft(doc, 409)
        with self.documents.store.connection() as db:
            db.execute('DELETE FROM automations WHERE id=?', ('delete-test',))
            db.execute('INSERT INTO automation_changes VALUES (?,?,?)', ('delete-change', 'delete-test', encode({'document_id': doc['document_id'], 'state': 'conflict'})))
        self.delete_draft(doc, 409)
        with self.documents.store.connection() as db:
            db.execute('DELETE FROM automation_changes WHERE id=?', ('delete-change',))
        self.delete_draft(doc)

    def test_same_file_multi_entry_and_same_name_isolation(self):
        doc = self.open()
        draft = self.mutate(doc, text='shared draft')
        for root, relative in [(self.b, 'same.mdx'), (self.root, 'b/same.mdx')]:
            workspace = self.post('/workspaces', {'path': str(root)})
            other = self.post(f"/workspaces/{workspace['id']}/open", {'path': relative})
            self.assertEqual(other['document_id'], doc['document_id'])
            self.assertEqual(other['revision'], draft['revision'])
        different = self.open(self.a / 'same.mdx')
        self.assertNotEqual(different['document_id'], doc['document_id'])
        self.assertEqual(different['text'], 'different')

    def test_untitled_restart_first_save_save_as_and_collision(self):
        doc = self.post('/documents', {})
        draft = self.mutate(doc, text='untitled draft\r\n')
        restarted = DocumentService(self.documents.settings)
        self.assertEqual(restarted.get(doc['document_id'])['text'], draft['text'])
        target = self.a / 'first.md'
        saved = self.post(f"/documents/{doc['document_id']}/save-as", {'revision': draft['revision'], 'text': draft['text'], 'path': str(target)})
        self.assertEqual(saved['document_id'], doc['document_id'])
        copied = self.post(f"/documents/{saved['document_id']}/save-as", {'revision': saved['revision'], 'text': 'copy', 'path': str(self.b / 'copy.md')})
        self.assertNotEqual(copied['document_id'], saved['document_id'])
        self.assertEqual(target.read_bytes(), draft['text'].encode())
        self.mutate(copied, 'save', 'copy changed')
        self.assertEqual(target.read_bytes(), draft['text'].encode())
        current = self.get(saved)
        self.post(f"/documents/{current['document_id']}/save-as", {'revision': current['revision'], 'text': 'preserved on failure', 'path': str(self.file)}, 409)
        self.assertEqual(self.get(saved)['text'], 'preserved on failure')
        self.assertEqual(self.file.read_bytes(), self.original.encode())

    def test_default_directory_validation_does_not_register_or_scan(self):
        before = self.client.get('/api/workspaces').json()
        with patch.object(EditorService, 'files', side_effect=AssertionError('No scanning')):
            value = self.post('/local-directory', {'path': str(self.b)})
        self.assertEqual(value['path'], os.path.normcase(str(self.b)))
        self.assertEqual(self.client.get('/api/workspaces').json(), before)
        self.assertEqual(self.client.get('/api/documents').json(), [])
        self.post('/local-directory', {'path': str(self.b / 'absent')}, 422)
        self.post('/local-directory', {'path': str(self.file)}, 422)
        self.post('/local-directory', {'path': 'relative/path'}, 422)

    def test_missing_default_directory_preserves_untitled_draft(self):
        doc = self.post('/documents', {})
        folder = self.b / 'default'
        folder.mkdir()
        self.post('/local-directory', {'path': str(folder)})
        folder.rmdir()
        self.post(f"/documents/{doc['document_id']}/save-as", {
            'revision': doc['revision'], 'text': 'keep this draft', 'path': str(folder / 'new.md')}, 422)
        current = self.get(doc)
        self.assertEqual(current['text'], 'keep this draft')
        self.assertIsNone(current['location'])
        self.assertTrue(current['draft_pending'])

    def test_relocate_keeps_identity_draft_versions_and_disk_content(self):
        doc = self.open()
        draft = self.mutate(doc, text='unsaved local draft')
        target = self.a / 'moved.mdx'
        self.file.rename(target)
        value = self.post(f"/documents/{doc['document_id']}/relocate", {
            'revision': draft['revision'], 'text': 'latest local input', 'path': str(target)})
        self.assertEqual(value['document_id'], doc['document_id'])
        self.assertEqual(value['location'], os.path.normcase(str(target)))
        self.assertEqual(value['text'], 'latest local input')
        self.assertTrue(value['draft_pending'])
        self.assertIsNone(value['file_error'])
        self.assertEqual(target.read_bytes(), self.original.encode())
        self.assertEqual(self.open(target)['document_id'], doc['document_id'])
        versions = self.client.get(f"/api/documents/{doc['document_id']}/versions").json()
        self.assertTrue(any(v['text'] == draft['text'] for v in versions))
        self.mutate(value, 'save')
        self.assertEqual(target.read_text(encoding='utf-8'), 'latest local input')

    def test_relocate_rejects_stale_mismatched_and_registered_targets(self):
        doc = self.open()
        body = {'revision': doc['revision'], 'text': doc['text'], 'path': str(self.a / 'same.mdx')}
        self.post(f"/documents/{doc['document_id']}/relocate", body, 422)
        self.file.unlink()
        self.post(f"/documents/{doc['document_id']}/relocate", body, 409)
        target = self.a / 'moved.mdx'
        target.write_bytes(self.original.encode())
        body['path'] = str(target)
        body['revision'] = 'stale'
        self.post(f"/documents/{doc['document_id']}/relocate", body, 409)
        body['revision'] = doc['revision']
        independent = self.open(target)
        self.post(f"/documents/{doc['document_id']}/relocate", body, 409)
        self.assertEqual(self.get(doc)['location'], doc['location'])
        self.assertEqual(self.get(independent)['location'], independent['location'])

    def test_relocate_keeps_active_run_and_pending_identity(self):
        doc = self.open()
        with patch('inkflow.workflows.full_check.call_llm', return_value='{"suggestions":[]}'):
            run = self.start_check(doc)
            current = self.get(doc)
            target = self.b / 'moved-with-run.mdx'
            self.file.rename(target)
            moved = self.post(f"/documents/{doc['document_id']}/relocate", {
                'revision': current['revision'], 'text': current['text'], 'path': str(target)})
            url = f"/runs/{run['id']}?document_id={doc['document_id']}"
            detail = self.client.get('/api' + url).json()
            self.assertEqual(detail['status'], 'waiting')
            self.assertEqual(detail['pending']['id'], run['pending']['id'])
            self.assertEqual(detail['editor_source'], run['editor_source'])
            self.post(f"/runs/{run['id']}/actions?document_id={doc['document_id']}", {
                'request_id': uuid4().hex, 'pending_id': detail['pending']['id'], 'action': 'finish_check', 'payload': {}})
            self.assertEqual(self.get(moved)['document_id'], doc['document_id'])

    def test_permission_failure_and_atomic_create_race(self):
        doc = self.post('/documents', {})
        target = self.b / 'denied.md'
        with patch('inkflow.web.documents.atomic_write', side_effect=PermissionError('denied')):
            self.post(f"/documents/{doc['document_id']}/save-as", {'revision': doc['revision'], 'text': 'keep me', 'path': str(target)}, 400)
        self.assertFalse(target.exists())
        current = self.get(doc)
        self.assertEqual(current['text'], 'keep me')
        from inkflow.services.local_files import atomic_write
        target.write_text('winner', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            atomic_write(target, 'loser', exclusive=True)
        self.assertEqual(target.read_text(), 'winner')
        self.assertFalse(list(target.parent.glob('.denied.md.*')))

    def test_tree_includes_unopened_files_and_empty_directories(self):
        space = self.client.get('/api/workspaces').json()['default_id']
        data = self.client.get(f'/api/workspaces/{space}/files').json()
        self.assertIn('nested/unopened.md', data['files'])
        self.assertIn('empty', data['directories'])
        self.assertTrue(all(e['document_id'] is None for e in data['entries']))
        self.assertEqual(self.client.get('/api/documents').json(), [])

    def test_explicit_workspace_keeps_existing_hidden_parent_semantics(self):
        folder = self.root / '.authorized-root'
        folder.mkdir()
        (folder / 'visible.md').write_text('visible', encoding='utf-8')
        (folder / '.hidden.md').write_text('hidden', encoding='utf-8')
        space = self.post('/workspaces', {'path': str(folder)})
        doc = self.post(f"/workspaces/{space['id']}/open", {'path': 'visible.md'})
        self.assertEqual(doc['text'], 'visible')
        self.post(f"/workspaces/{space['id']}/open", {'path': '.hidden.md'}, 422)
        self.assertEqual(self.client.get(f"/api/workspaces/{space['id']}/files").json()['files'], ['visible.md'])

    def test_old_tab_revision_and_resource_cannot_cross_write(self):
        first, second = self.open(), self.open(self.a / 'same.mdx')
        newest = self.mutate(first, text='tab one')
        self.mutate(first, 'save', 'stale tab', expected=409)
        self.assertEqual(self.get(first)['text'], newest['text'])
        self.assertEqual(self.get(second)['text'], 'different')
        # 即使附带文档头，旧路径接口仍校验工作区头。
        response = self.client.post('/api/editor/operation', json={'path': 'same.mdx', 'revision': second['revision'], 'text': 'bad', 'action': 'save'},
                                    headers={'X-InkFlow-Workspace': str(self.b), 'X-InkFlow-Document': first['document_id']})
        self.assertEqual(response.status_code, 409)

    def test_external_conflict_missing_file_restore_and_candidate_baseline(self):
        doc = self.open()
        draft = self.mutate(doc, text='draft')
        self.file.write_text('external', encoding='utf-8')
        self.mutate(draft, 'save', expected=409)
        self.assertTrue(self.get(doc)['disk_conflict'])
        restored = self.mutate(draft, 'restore', version=doc['revision'])
        self.assertEqual(restored['text'], self.original)
        self.assertEqual(self.file.read_text(), 'external')
        source = self.documents.document_source(doc['document_id'], restored['revision'], restored['text'])
        edited = self.mutate(restored, text='new baseline')
        result = {'id': 'candidate', 'text': 'candidate text', 'kind': 'redaction', 'label': 'candidate'}
        from inkflow.web.runs import ConflictError
        with self.assertRaises(ConflictError):
            self.documents.apply_result(doc['document_id'], edited['revision'], edited['text'], source, result, 'run', 'full-check')
        applied = self.documents.apply_result(doc['document_id'], edited['revision'], edited['text'], source, result, 'run', 'full-check', True)
        self.assertEqual(applied['text'], result['text'])
        self.assertEqual(self.file.read_text(), 'external')
        self.file.unlink()
        missing = self.get(doc)
        self.assertTrue(missing['file_error'])
        self.assertEqual(missing['text'], result['text'])
        self.mutate(missing, 'save', expected=422)
        self.assertFalse(self.file.exists())

    def start_check(self, doc):
        response = self.post('/runs', {'request_id': uuid4().hex, 'name': 'check', 'raw_text': doc['text'],
                                      'source_name': doc['path'], 'no_publish': True, 'workflow_id': 'full-check',
                                      'editor_source': {'document_id': doc['document_id'], 'revision': doc['revision']}}, 201)
        url = f"/api/runs/{response['id']}?document_id={doc['document_id']}"
        for _ in range(150):
            detail = self.client.get(url).json()
            if detail['status'] != 'running':
                self.assertEqual(detail['status'], 'waiting', detail)
                return detail
            time.sleep(.02)
        self.fail('check did not reach wait')

    def test_active_run_cross_workspace_sse_actions_and_single_active(self):
        doc = self.open()
        suggestions = {'suggestions': [{'kind': 'expression', 'title': 'title', 'reason': 'reason', 'original': '正文', 'replacement': '修改'}]}
        with patch('inkflow.workflows.full_check.call_llm', return_value=json.dumps(suggestions)):
            run = self.start_check(doc)
            b = self.post('/workspaces', {'path': str(self.b)})
            self.client.get(f"/api/workspaces/{b['id']}/files")
            other = self.open(self.a / 'same.mdx')
            self.mutate(other, 'save', 'B edit during A wait')
            self.post('/runs', {'request_id': uuid4().hex, 'raw_text': doc['text'], 'editor_source': {'document_id': doc['document_id'], 'revision': doc['revision']}}, 409)
            self.assertEqual(self.client.get(f"/api/runs/{run['id']}?document_id={other['document_id']}").status_code, 404)
            # 新 SSE 固定 ID，旧全局根目录变化不改变资源归属。
            async def stream_check():
                endpoint = next(r.endpoint for r in self.app.routes if getattr(r, 'path', '') == '/api/runs/{run_id}/events')
                async def receive():
                    return {'type': 'http.request', 'body': b'', 'more_body': False}
                request = Request({'type': 'http', 'app': self.app, 'path': f"/api/runs/{run['id']}/events", 'headers': [],
                                   'query_string': f"document_id={doc['document_id']}".encode()}, receive)
                response = await endpoint(run['id'], request, after=0)
                iterator = response.body_iterator
                self.assertIn('retry:', await anext(iterator))
                self.app.state.editor.root = self.b
                self.assertIn('run_event', await anext(iterator))
                await iterator.aclose()
            asyncio.run(stream_check())
            action = {'request_id': uuid4().hex, 'pending_id': run['pending']['id'], 'action': 'accept',
                      'payload': {'suggestion_id': run['annotations'][0]['id'], 'editor': {
                          'document_id': doc['document_id'], 'revision': doc['revision'], 'text': doc['text']}}}
            url = f"/runs/{run['id']}/actions?document_id={doc['document_id']}"
            first = self.post(url, action)
            self.assertEqual(self.post(url, action), first)
            self.assertIn('修改', self.get(doc)['text'])
            self.assertEqual(self.file.read_bytes(), self.original.encode())
            self.post(url, {'request_id': uuid4().hex, 'pending_id': run['pending']['id'], 'action': 'finish_check'})

    def test_reviewed_workflow_candidate_conflict_bridge_and_completion(self):
        from inkflow.services.article import build_placeholder_article
        doc = self.open()
        finding = {'id': 'S1', 'risk': 'high', 'line_start': 5, 'line_end': 5, 'original_excerpt': '正文',
                   'issue': 'test', 'reason': 'test', 'suggestion': 'replace'}
        with patch('inkflow.graphs.minimal.generate_redaction_findings', side_effect=[[finding], []]), \
             patch('inkflow.graphs.minimal.apply_redaction_with_llm', return_value='safe candidate'), \
             patch('inkflow.graphs.minimal.generate_article_data', return_value={'article': build_placeholder_article('generated body'), 'degraded': False}):
            run = self.post('/runs', {'request_id': uuid4().hex, 'raw_text': doc['text'], 'source_name': doc['path'],
                'workflow_id': 'reviewed-publishing', 'no_publish': True,
                'editor_source': {'document_id': doc['document_id'], 'revision': doc['revision']}}, 201)
            base = f"/runs/{run['id']}"
            scope = '?document_id=' + doc['document_id']

            def wait(kind=None):
                for _ in range(150):
                    current = self.client.get('/api' + base + scope).json()
                    reached = (current.get('pending') or {}).get('kind') == kind if kind else current['status'] == 'completed'
                    if reached:
                        return current
                    self.assertNotEqual(current['status'], 'failed', current)
                    time.sleep(.02)
                self.fail('workflow did not reach ' + str(kind))

            def act(current, action, payload=None, expected=200):
                return self.post(base + '/actions' + scope, {'request_id': uuid4().hex, 'pending_id': current['pending']['id'],
                    'action': action, 'payload': payload or {}}, expected)

            run = wait('redaction_review')
            act(run, 'review', {'decisions': [{'finding_id': 'S1', 'action': 'apply'}]})
            run = wait('diff_review')
            edited = self.mutate(doc, text='user edit while candidate waiting')
            payload = {'editor': {'document_id': doc['document_id'], 'revision': edited['revision'], 'text': edited['text']}}
            act(run, 'accept', payload, 409)
            act(run, 'accept', {**payload, 'confirm_conflict': True})
            run = wait('redaction_review')
            self.assertEqual(self.get(doc)['text'], 'safe candidate')
            self.assertEqual(self.file.read_bytes(), self.original.encode())
            act(run, 'review', {'decisions': []})
            run = wait('final_review')
            current = self.get(doc)
            body = {'document_id': doc['document_id'], 'revision': current['revision'], 'text': current['text'],
                    'result_id': run['document_result']['id']}
            self.post(base + '/document-preview' + scope, body)
            imported = self.post(base + '/document-apply' + scope, body)
            self.assertIn('generated body', imported['text'])
            # Unified main-editor submissions authorize only the exact adopted document snapshot.
            edited = self.mutate(imported, text=imported['text'] + '\nmain editor revision')
            target = {key: edited[key] for key in ('document_id', 'revision', 'text')}
            submission = {'revision': run['pending']['revision'], 'document': edited['text'], 'editor': target}
            act(run, 'save', {**submission, 'editor': {**target, 'revision': imported['revision']}}, 409)
            act(run, 'save', {**submission, 'editor': {**target, 'document_id': self.open(self.a / 'same.mdx')['document_id']}}, 409)
            act(run, 'save', {**submission, 'document': imported['text']}, 409)
            self.assertEqual(self.app.state.manager.detail(run['id'])['pending']['id'], run['pending']['id'])
            identity = uuid4().hex
            request = {'request_id': identity, 'pending_id': run['pending']['id'], 'action': 'save', 'payload': submission}
            first = self.post(base + '/actions' + scope, request)
            self.assertEqual(self.post(base + '/actions' + scope, request), first)
            run = wait('final_review')
            self.assertIn('main editor revision', run['pending']['document'])
            current = self.get(doc)
            self.assertEqual(current['text'], edited['text'])
            self.assertEqual(self.file.read_bytes(), self.original.encode())
            self.assertEqual(len([e for e in run['events'] if e['type'] == 'working_copy_submitted']), 1)
            body = {'document_id': doc['document_id'], 'revision': current['revision'], 'text': current['text'],
                    'result_id': run['document_result']['id']}
            imported = self.post(base + '/document-apply' + scope, body)
            act(run, 'finish', {'revision': run['pending']['revision'], 'document': imported['text']})
            final = wait()
            self.assertEqual(final['status'], 'completed')
            for artifact in final['artifacts']:
                response = self.client.get('/api' + base + '/artifacts/' + artifact['id'] + scope)
                self.assertEqual(response.status_code, 200)

    def test_path_format_and_symlink_replacement_are_rejected(self):
        self.post('/documents/open', {'path': str(self.root / 'config.toml')}, 422)
        hidden = self.b / '.hidden.md'
        hidden.write_text('hidden', encoding='utf-8')
        self.post('/documents/open', {'path': str(hidden)}, 422)
        invalid = self.b / 'nonutf.md'; invalid.write_bytes(b'\xff')
        self.post('/documents/open', {'path': str(invalid)}, 422)
        doc = self.open()
        # 模拟登记后目标变成链接，写入前必须再次校验。
        original = Path.is_symlink
        with patch.object(Path, 'is_symlink', lambda p: p == self.file or original(p)):
            self.mutate(doc, 'save', expected=422)
        self.assertEqual(self.file.read_bytes(), self.original.encode())

    def test_restored_or_adopted_identical_text_is_still_a_draft(self):
        doc = self.open()
        restored = self.mutate(doc, 'restore', version=doc['revision'])
        self.assertEqual(restored['text'], restored['disk_text'])
        self.assertTrue(restored['draft_pending'])
        saved = self.mutate(restored, 'save')
        self.assertFalse(saved['draft_pending'])
        source = self.documents.document_source(saved['document_id'], saved['revision'], saved['text'])
        candidate = {'id': 'same', 'text': saved['text'], 'kind': 'redaction', 'label': 'same'}
        applied = self.documents.apply_result(saved['document_id'], saved['revision'], saved['text'], source, candidate, 'run', 'full-check')
        self.assertTrue(applied['draft_pending'])

    def test_legacy_request_hash_and_action_replay_survive_identity_adapter(self):
        doc = self.client.get('/api/editor/document', params={'path': 'same.mdx'}).json()
        source = {'path': 'same.mdx', 'revision': doc['revision']}
        with patch('inkflow.workflows.full_check.call_llm', return_value='{"suggestions":[]}'):
            run = self.post('/runs', {'request_id': uuid4().hex, 'raw_text': doc['text'], 'workflow_id': 'full-check',
                                      'editor_source': source}, 201)
            expected_hash = hashlib.sha256(encode(['', doc['text'], '粘贴文本', False, None, 'full-check', '', source]).encode()).hexdigest()
            self.assertEqual(self.app.state.manager.store.get(run['id'])['creation_hash'], expected_hash)
            action = {'request_id': uuid4().hex, 'pending_id': 'old-pending', 'action': 'accept', 'payload': {
                'suggestion_id': 'old-suggestion', 'editor': {'path': 'same.mdx', 'revision': doc['revision'], 'text': doc['text']}}}
            signature = hashlib.sha256(json.dumps(action, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            result = {'accepted': True, 'run_id': run['id'], 'event_cursor': 1}
            self.app.state.manager.store.remember_action(run['id'], action['request_id'], signature, result)
            self.assertEqual(self.post(f"/runs/{run['id']}/actions", action), result)
            # Keep the model replacement installed until asynchronous execution reaches its wait.
            for _ in range(200):
                if self.app.state.manager.detail(run['id'])['status'] != 'running':
                    break
                time.sleep(.025)
            self.assertEqual(self.app.state.manager.detail(run['id'])['status'], 'waiting')


class MigrationTests(unittest.TestCase):
    def test_repeatable_migration_keeps_conflicting_drafts_versions_and_runs(self):
        with tempfile.TemporaryDirectory(prefix='inkflow-migration-') as temporary:
            root = Path(temporary).resolve()
            nested = root / 'nested'; nested.mkdir()
            path = nested / 'same.md'; path.write_text('disk', encoding='utf-8')
            settings = SettingsStore(root / 'config.toml', root / 'data')
            a, b = EditorService(settings, root), EditorService(settings, nested)
            old_a, old_b = a.open('nested/same.md'), b.open('same.md')
            old_a = a.mutate('nested/same.md', old_a['revision'], 'draft A', 'edit')
            old_b = b.mutate('same.md', old_b['revision'], 'draft B', 'edit')
            record = {'id': uuid4().hex, 'creation_id': uuid4().hex, 'name': 'legacy', 'status': 'completed',
                      'created_at': now(), 'updated_at': now(), 'workflow_id': 'full-check', 'workflow': get_workflow('full-check').describe(),
                      'state': {'raw_text': 'draft A'}, 'editor_source': {'workspace': str(root), 'path': 'nested/same.md', 'revision': old_a['revision']}}
            a.store.create(record)
            with a.store.connection() as db:
                originals = [tuple(r) for r in db.execute('SELECT * FROM editor_docs ORDER BY path')]
            documents = DocumentService(settings)
            doc = documents.register(str(path))
            self.assertEqual(len(doc['migration_conflicts']), 1)
            self.assertEqual({v['text'] for v in documents.versions(doc['document_id'])}, {'disk', 'draft A', 'draft B'})
            self.assertEqual(documents.store.get(record['id'])['editor_source']['document_id'], doc['document_id'])
            documents.mutate(doc['document_id'], doc['revision'], 'post migration', 'edit')
            again = DocumentService(settings)
            self.assertEqual(again.register(str(path))['text'], 'post migration')
            with a.store.connection() as db:
                self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM editor_docs ORDER BY path')], originals)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM document_aliases').fetchone()[0], 2)
            self.assertEqual(path.read_text(), 'disk')


if __name__ == '__main__':
    unittest.main()
