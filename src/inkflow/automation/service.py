"""Persistent checks/outbox. A committed change always precedes cursor advancement."""
import json
import logging
from functools import lru_cache
from pathlib import Path
import threading
import time
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from inkflow.web.storage import encode, now
from inkflow.web.runs import ConflictError, TERMINAL
from inkflow.web.durable import fault
from inkflow.automation.sources import read_source, digest, url_parts, origin, checked_project_file


class Automations:
    def __init__(self, manager, documents, *, start=True):
        self.manager, self.documents, self.store = manager, documents, manager.store
        self.lock, self.closing = threading.RLock(), threading.Event()
        with self.store.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS automations(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS source_snapshots(id TEXT PRIMARY KEY, automation_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS automation_changes(id TEXT PRIMARY KEY, automation_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS automation_checks(id TEXT PRIMARY KEY, automation_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS automation_source_limits(origin TEXT PRIMARY KEY, next_check REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS automation_change_task ON automation_changes(automation_id);
            ''')
            # The RunManager OS lock proves any preceding check owner has exited.
            for row in db.execute('SELECT id,payload FROM automations').fetchall():
                task = json.loads(row['payload'])
                if task['checking']:
                    task.update(checking=False, lease=None, next_check=time.time())
                    db.execute('UPDATE automations SET payload=? WHERE id=?', (encode(task), task['id']))
        self.thread = threading.Thread(target=self._loop, name='inkflow-sources', daemon=True)
        if start:
            self.thread.start()

    def close(self):
        self.closing.set()
        if self.thread.is_alive():
            self.thread.join()

    def _loop(self):
        while not self.closing.wait(.5):
            try:
                for task in self.list():
                    if self.closing.is_set():
                        return
                    if task['enabled']:
                        if task['next_check'] <= time.time():
                            self.check(task['id'])
                        self.dispatch(task['id'])
            except Exception:
                # Do not let an infrastructure error silently kill the scheduler or leak content.
                logging.getLogger(__name__).error('自动化调度暂不可用，将在下轮重试。')

    def _save(self, table, item, db=None):
        if db is None:
            with self.store.connection() as connection:
                self._save(table, item, connection)
            return
        if table == 'automations':
            db.execute('INSERT OR REPLACE INTO automations VALUES (?,?)', (item['id'], encode(item)))
        else:
            db.execute(f'INSERT OR REPLACE INTO {table} VALUES (?,?,?)', (item['id'], item['automation_id'], encode(item)))

    def get(self, identity):
        with self.store.connection() as db:
            row = db.execute('SELECT payload FROM automations WHERE id=?', (identity,)).fetchone()
        if not row:
            raise KeyError(identity)
        return json.loads(row['payload'])

    def list(self):
        with self.store.connection() as db:
            tasks = [json.loads(row['payload']) for row in db.execute('SELECT payload FROM automations ORDER BY rowid DESC')]
        for task in tasks:
            changes = self.changes(task['id'])
            task['backlog'] = sum(c['state'] in {'pending', 'conflict', 'blocked'} for c in changes)
            task['waiting'] = sum(c.get('run_status') in {'waiting', 'verification_required', 'recovery_blocked'} for c in changes)
        return tasks

    def changes(self, identity, limit=None):
        self.get(identity)
        with self.store.connection() as db:
            rows = db.execute('SELECT payload FROM automation_changes WHERE automation_id=? ORDER BY rowid DESC', (identity,)).fetchall()
        changes = [json.loads(row['payload']) for row in rows]
        for change in changes:
            if change.get('run_id'):
                change['run_status'] = self.store.get(change['run_id'])['status']
        return changes if limit is None else changes[:limit]

    def history(self, identity):
        task = self.get(identity)
        with self.store.connection() as db:
            checks = [json.loads(row['payload']) for row in db.execute('SELECT payload FROM automation_checks WHERE automation_id=? ORDER BY rowid DESC LIMIT 50', (identity,))]
        changes = self.changes(identity)
        visible = changes[:100]
        oldest = next((c for c in reversed(changes) if c['state'] in {'pending', 'conflict', 'blocked'}), None)
        if oldest and oldest not in visible:
            visible.append(oldest)
        # Keep an old blocking item reachable without sending entire article baselines on polling.
        summaries = [{k: v for k, v in c.items() if k != 'baseline'} for c in visible]
        return {'task': task, 'changes': summaries, 'checks': checks}

    def evidence(self, identity, snapshot_id):
        self.get(identity)
        with self.store.connection() as db:
            row = db.execute('SELECT payload FROM source_snapshots WHERE id=? AND automation_id=?', (snapshot_id, identity)).fetchone()
        if not row:
            raise KeyError(snapshot_id)
        return json.loads(row['payload'])

    def create(self, body):
        source = body['source']
        if source['kind'] in {'http', 'rss'}:
            url_parts(source['url'])
            if source['credential_ref'] and url_parts(source['url']).scheme != 'https':
                raise ValueError('带凭据的来源必须使用 HTTPS。')
        else:
            with self.store.connection() as db:
                row = db.execute('SELECT root FROM workspaces WHERE id=?', (source['workspace_id'],)).fetchone()
            if not row or not source['files']:
                raise ValueError('请选择已登记工作区与明确的来源文件。')
            for name in source['files']:
                checked_project_file(Path(row['root']), name)
        try:
            ZoneInfo(body['timezone'])
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('时区无效，请使用 Asia/Shanghai 等 IANA 时区。') from None
        if body['target_mode'] == 'existing':
            self.documents.get(body['document_id'])
        elif body['document_id']:
            raise ValueError('新文候选不能绑定已有文章。')
        version = self.manager.catalog.active(body['workflow_id'])
        self.validate_version(version)
        task = {**body, 'id': uuid4().hex, 'version_id': version['id'], 'created_at': now(), 'generation': 1,
                'checking': False, 'lease': None, 'next_check': time.time(), 'last_check': None, 'last_error': '',
                'failures': 0, 'cursor': {}, 'missed_policy': 'coalesce', 'update_policy': 'review'}
        self._save('automations', task)
        return task

    def validate_version(self, version):
        if not version['id'] or version['payload'].get('schema_version') != 2:
            raise ValueError('请选择已启用且包含来源资料节点的定义流程。')
        self.manager.catalog.validate(version['payload'])
        kinds = [n['node_type_id'] for n in version['payload']['definition']['nodes']]
        if 'source-material' not in kinds or 'review-article' not in kinds:
            raise ValueError('自动化流程必须包含来源资料与文章审阅节点。')
        if any(k in kinds for k in ('write-review', 'write-report', 'full-check', 'redaction')):
            raise ValueError('自动化先审阅完整候选；请使用来源资料、文章准备/生成、文章审阅及可选人工发布节点。')
        definition = version['payload']['definition']
        nodes = {n['node_id']: n for n in definition['nodes']}
        @lru_cache(maxsize=101)
        def carries_source(identity):
            if identity == '$input':
                return False
            node = nodes[identity]
            return node['node_type_id'] == 'source-material' or any(carries_source(b['source']) for b in node['inputs'].values())
        if any(not carries_source(n['node_id']) for n in nodes.values() if n['node_type_id'] == 'review-article'):
            raise ValueError('人工审阅必须接收来源资料产生的候选。')
        visited = set()
        def walk(identity, reviewed):
            if (identity, reviewed) in visited:
                return
            visited.add((identity, reviewed))
            reviewed = reviewed or nodes[identity]['node_type_id'] == 'review-article'
            if identity in definition['ends'] and not reviewed:
                raise ValueError('自动化每条结束路径必须经过人工文章审阅。')
            for edge in definition['connections']:
                if edge['source'] == identity:
                    walk(edge['target'], reviewed)
        walk(definition['entry'], False)

    def template(self):
        # Called only by an explicit user action; reuse the normal draft/validate/activate path.
        with self.manager.lock:
            view = self.manager.catalog.definitions.create('来源更新 · 人工审阅')
            from inkflow.orchestration.definitions import template
            p = view['draft']['payload']
            p['definition'] = template(p['workflow_id'], '来源更新 · 人工审阅', p['model'], variant='source-update')
            p = self.manager.catalog.definitions.package(p['definition'])
            saved = self.manager.catalog.save(p['workflow_id'], view['draft']['revision'], p)
            return self.manager.catalog.activate(p['workflow_id'], saved['draft']['revision'], saved['active']['generation'])

    def control(self, identity, generation, action):
        with self.lock, self.store.transaction():
            task = self.get(identity)
            if task['generation'] != generation:
                raise ConflictError('任务已在其它页面修改，请刷新。')
            if action == 'pause':
                task['enabled'] = False
            elif action == 'resume':
                task.update(enabled=True, next_check=time.time())
            elif action == 'check':
                if not task['enabled']:
                    raise ConflictError('请先恢复任务，再检查来源。')
                # A manual request cannot bypass retry backoff or start a parallel check.
                if task['checking'] or task['failures'] and task['next_check'] > time.time():
                    raise ConflictError('检查正在进行或等待退避，请稍后重试。')
                task['next_check'] = time.time()
            task['generation'] += 1
            self._save('automations', task)
            return task

    def check(self, identity):
        with self.lock, self.store.transaction() as db:
            task = self.get(identity)
            if not task['enabled'] or task['checking'] or task['next_check'] > time.time():
                return
            if task['source']['kind'] != 'project':
                key = encode(origin(task['source']['url']))
                limited = db.execute('SELECT next_check FROM automation_source_limits WHERE origin=?', (key,)).fetchone()
                if limited and limited['next_check'] > time.time():
                    task['next_check'] = limited['next_check']
                    self._save('automations', task)
                    return
                db.execute('INSERT OR REPLACE INTO automation_source_limits VALUES (?,?)', (key, time.time() + 5))
            token = uuid4().hex
            task.update(checking=True, lease={'token': token, 'expires': time.time() + 60})
            self._save('automations', task)
            # Capture the article baseline BEFORE fetching; concurrent edits become a conflict.
        result, error, baseline = None, '', None
        try:
            baseline = self.documents.open(task['document_id']) if task['target_mode'] == 'existing' else None
            excluded = set()
            with self.store.connection() as db:
                excluded.update(str(Path(r['location']).resolve()) for r in db.execute('SELECT location FROM documents WHERE location IS NOT NULL')
                                if baseline and r['location'] == baseline.get('location'))
                excluded.update(str(Path(json.loads(r['payload'])['path']).resolve()) for r in db.execute('SELECT payload FROM execution_effects'))
                # Exclude all automation target files, including newly saved generated articles.
                for row in db.execute('SELECT payload FROM automation_changes'):
                    doc_id = json.loads(row['payload']).get('document_id')
                    if doc_id:
                        location = self.documents.resource(doc_id).get('location')
                        if location:
                            excluded.add(str(Path(location).resolve()))
            result = read_source(task['source'], task['cursor'], self.documents, excluded)
        except Exception as exc:
            # Known validation errors are safe; network/OS exceptions may contain credentials/URLs.
            error = str(exc) if type(exc) is ValueError else '来源读取失败，请检查连接、格式和权限；稍后自动重试。'
        with self.lock, self.store.transaction() as db:
            current = self.get(identity)
            if not current['lease'] or current['lease']['token'] != token:
                return
            stamp = now()
            check = {'id': uuid4().hex, 'automation_id': identity, 'at': stamp, 'status': 'failed' if error else 'unchanged', 'error': error}
            current.update(checking=False, lease=None, last_check=stamp, last_error=error)
            if error:
                current['failures'] += 1
                current['next_check'] = time.time() + min(86400, current['interval_seconds'] * 2 ** min(current['failures'], 10))
            else:
                current.update(failures=0, next_check=time.time() + current['interval_seconds'])
                if result:
                    content_hash = digest(result['content'])
                    if content_hash != current['cursor'].get('hash'):
                        snapshot = {**result, 'id': uuid4().hex, 'automation_id': identity, 'fetched_at': stamp, 'sha256': content_hash}
                        self._save('source_snapshots', snapshot, db)
                        change = {'id': uuid4().hex, 'automation_id': identity, 'snapshot_id': snapshot['id'], 'created_at': stamp,
                                  'state': 'pending', 'document_id': task['document_id'], 'baseline': baseline,
                                  'version_id': None, 'run_id': None, 'error': ''}
                        self._save('automation_changes', change, db)
                        check.update(status='changed', snapshot_id=snapshot['id'])
                    current['cursor'] = {'hash': content_hash, 'etag': result.get('etag', ''), 'modified': result.get('modified', '')}
                fault('automation_before_cursor')
            self._save('automation_checks', check, db)
            self._save('automations', current, db)
        fault('automation_after_cursor')

    def dispatch(self, identity):
        with self.lock, self.manager.lock, self.documents.lock:
            task = self.get(identity)
            if not task['enabled']:
                return
            changes = list(reversed(self.changes(identity)))
            if any(c.get('run_id') and self.store.get(c['run_id'])['status'] not in TERMINAL for c in changes):
                return
            change = next((c for c in changes if c['state'] in {'pending', 'conflict', 'blocked'}), None)
            if not change or change['state'] != 'pending':
                return
            try:
                # Durable request identity bridges a crash after run commit and before outbox ack.
                previous = self.store.by_creation_id('automation-' + change['id'])
                if previous:
                    change.update(state='running', run_id=previous['id'], error='')
                    self._save('automation_changes', change)
                    return
                if not change['baseline']:
                    with self.store.transaction():
                        doc = self.documents.new()
                        doc = self.documents.mutate(doc['document_id'], doc['revision'], '# 来源候选\n', 'edit')
                        change.update(document_id=doc['document_id'], baseline=doc)
                        self._save('automation_changes', change)
                baseline = change['baseline']
                doc = self.documents.open(change['document_id'])
                if doc['revision'] != baseline['revision'] or doc.get('disk_conflict') or doc.get('file_error'):
                    change.update(state='conflict', error='文章或磁盘已变化，请打开当前稿核对，再按当前稿重新审阅。')
                    self._save('automation_changes', change)
                    return
                if any(self.store.get(run)['editor_source']['document_id'] == doc['document_id'] for run in self.manager.live):
                    return
                version = self.manager.catalog.version(task['workflow_id'], change['version_id'] or task['version_id']) if task['version_policy'] == 'fixed' or change['version_id'] else self.manager.catalog.active(task['workflow_id'])
                self.validate_version(version)
                change['version_id'] = version['id']
                self._save('automation_changes', change)
                snapshot = self.evidence(identity, change['snapshot_id'])
                context = {'automation_id': identity, 'change_id': change['id'], 'snapshot_id': snapshot['id'],
                           'sha256': snapshot['sha256'], 'url': snapshot['url'], 'fetched_at': snapshot['fetched_at'], 'content': snapshot['content']}
                run = self.manager.create(name=task['name'], raw_text=baseline['text'], source_name=doc['path'], no_publish=False,
                    request_id='automation-' + change['id'], workflow_id=task['workflow_id'], workflow_version=version['id'],
                    editor_source={'document_id': doc['document_id'], 'revision': baseline['revision']}, automation=context)
                fault('automation_run_created')
                change.update(state='running', run_id=run['id'], error='')
                self._save('automation_changes', change)
            except (ValueError, KeyError) as error:
                change.update(state='blocked', error=str(error) if isinstance(error, ValueError) else '流程版本或文档不可用，请核对后重试。')
                self._save('automation_changes', change)

    def retry(self, identity, change_id, revision):
        with self.lock, self.manager.lock, self.documents.lock, self.store.transaction():
            change = next((c for c in self.changes(identity) if c['id'] == change_id), None)
            if not change:
                raise KeyError(change_id)
            if change['state'] not in {'conflict', 'blocked'} or change['run_id']:
                raise ConflictError('变化项已处理，请刷新状态。')
            doc = self.documents.open(change['document_id']) if change['document_id'] else None
            if doc and (doc['revision'] != revision or doc.get('disk_conflict') or doc.get('file_error')):
                raise ConflictError('请先在文章页处理磁盘冲突，并重新核对当前版本。')
            change.update(baseline=doc, state='pending', error='')
            self._save('automation_changes', change)
            return change
