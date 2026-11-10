"""文章业务操作。SQLite 快照与编辑器撤销栈、LangGraph checkpoint 独立。"""
from __future__ import annotations

import hashlib
import difflib
from itertools import islice
import json
import os
import threading
from pathlib import Path
from uuid import uuid4


from inkflow.services.local_files import atomic_write
from inkflow.web.storage import HistoryStore, now, encode
from inkflow.web.runs import ConflictError


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def units(text: str) -> int:
    return len(text.encode('utf-16-le')) // 2


def index(text: str, offset: int) -> int:
    # 严格拒绝落在 emoji 代理对内部的位置。
    if offset < 0 or offset > units(text):
        raise ValueError('位置不在正文范围内。')
    try:
        return len(text.encode('utf-16-le')[:offset * 2].decode('utf-16-le'))
    except UnicodeError:
        raise ValueError('位置必须位于完整字符边界。') from None


class EditorService:
    def __init__(self, settings, root: Path | None = None):
        self.settings = settings
        self.root = (root or settings.root).resolve()
        self.store = HistoryStore(settings.data_dir)
        self.lock = threading.RLock()
        with self.store.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS editor_docs (path TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS editor_versions (id TEXT PRIMARY KEY, path TEXT NOT NULL,
                    created_at TEXT NOT NULL, operation TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS editor_tasks (id TEXT PRIMARY KEY, path TEXT NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL);
            ''')

    def identity(self, relative):
        # 同一数据目录切换工作区时，不让同名文章共用版本与建议。
        return str(self.root) + '|' + self.canonical(relative)

    def canonical(self, relative):
        return self.path(relative).resolve().relative_to(self.root).as_posix()

    def path(self, relative: str) -> Path:
        p = Path(relative)
        if p.is_absolute() or not p.parts or any(x.startswith('.') for x in p.parts):
            raise ValueError('请选择工作区内部的文章。')
        candidate = self.root / p
        if any(x.is_symlink() for x in [candidate, *candidate.parents] if x != self.root.parent):
            raise ValueError('不支持符号链接文章或目录。')
        if not candidate.resolve().is_relative_to(self.root) or candidate.suffix.lower() not in {'.md', '.mdx', '.markdown'}:
            raise ValueError('路径越界或格式不支持，仅支持 Markdown / MDX。')
        if not candidate.is_file():
            raise ValueError('文章不存在或不是普通文件。')
        return candidate

    def files(self):
        if not self.root.is_dir():
            raise ValueError('工作区目录不存在。')
        files = []
        for directory, folders, names in os.walk(self.root, followlinks=False):
            folders[:] = sorted(x for x in folders if not x.startswith('.') and x not in {'node_modules', 'dist', '__pycache__'} and not (Path(directory) / x).is_symlink())
            for name in sorted(names):
                p = Path(directory) / name
                if not p.is_symlink() and not name.startswith('.') and p.suffix.lower() in {'.md', '.mdx', '.markdown'}:
                    files.append(p.relative_to(self.root).as_posix())
                    if len(files) > 5000:
                        raise ValueError('工作区超过 5000 篇文章，请使用更小的工作区。')
        return {'root': str(self.root), 'files': files}

    def read_file(self, path):
        if path.stat().st_size > 2_000_000:
            raise ValueError('文章超过 2 MB，当前编辑器暂不支持。')
        raw = path.read_bytes()
        try:
            text = raw.decode('utf-8')
        except UnicodeError:
            raise ValueError('仅支持 UTF-8 文章，请先转换编码。') from None
        return text

    def check_secret(self, text):
        key = self.settings.key(self.settings.read())
        if key and key in text:
            raise ValueError('文章包含当前模型密钥，请移除后再操作。')

    def get(self, path):
        with self.store.connection() as db:
            row = db.execute('SELECT payload FROM editor_docs WHERE path=?', (self.identity(path),)).fetchone()
        if not row:
            raise KeyError(path)
        doc = {**json.loads(row['payload']), 'workspace': str(self.root)}
        # 升级前独立检查的内存执行已经失效；只在读取时兼容，不伪造正式运行或批量改历史。
        if doc.get('check') and doc['check']['status'] == 'running':
            doc['check'] = {**doc['check'], 'status': 'interrupted'}
        return doc

    def persist(self, doc, operation):
        with self.store.connection() as db:
            return self._persist(doc, operation, db)

    def _persist(self, doc, operation, db):
        doc['revision'] = uuid4().hex
        doc['updated_at'] = now()
        db.execute('INSERT OR REPLACE INTO editor_docs VALUES (?,?)', (self.identity(doc['path']), encode(doc)))
        db.execute('INSERT INTO editor_versions VALUES (?,?,?,?,?)', (doc['revision'], self.identity(doc['path']), doc['updated_at'], operation, encode(doc)))
        return doc

    def document_source(self, relative, revision, text):
        with self.lock:
            relative = self.canonical(relative)
            self.check_secret(text)
            doc = self.get(relative)
            if doc['revision'] != revision:
                raise ConflictError('文章版本已变化，请重新打开后启动工作流。')
            if doc['text'] != text:
                doc = self.mutate(relative, revision, text, 'edit')
                revision = doc['revision']
            return {'workspace': str(self.root), 'path': relative, 'revision': revision, 'text_hash': digest(text)}

    def _result_target(self, relative, revision, text, source, result):
        self.path(relative)
        for value in (text, result['text']):
            self.check_secret(value)
            if len(value.encode('utf-8')) > 2_000_000:
                raise ValueError('文章超过 2 MB，无法载入编辑区。')
        doc = self.get(relative)
        if doc['revision'] != revision:
            raise ConflictError('文章版本已变化，请重新打开文章后对比。')
        if source and (source['workspace'] != str(self.root) or source['path'] != relative):
            raise ConflictError('请在原工作区打开工作流关联的文章，不能替换其它文档。')
        return doc, not source or digest(text) != source['text_hash']

    def preview_result(self, relative, revision, text, source, result):
        with self.lock:
            _, conflict = self._result_target(relative, revision, text, source, result)
            lines = list(islice(difflib.unified_diff(text.splitlines(keepends=True), result['text'].splitlines(keepends=True),
                                                   fromfile='当前编辑稿', tofile=result['label']), 4001))
            diff = ''.join(lines[:4000])
            truncated = len(lines) > 4000 or len(diff) > 120000
            return {'result_id': result['id'], 'conflict': conflict, 'linked': source is not None,
                    'diff': diff[:120000], 'diff_truncated': truncated}

    def apply_result(self, relative, revision, text, source, result, run_id, workflow_id, confirm_conflict=False, stale_ids=()):
        with self.lock:
            doc, conflict = self._result_target(relative, revision, text, source, result)
            if conflict and not confirm_conflict:
                raise ConflictError('当前编辑稿已变化或输入未关联文档，请对比并明确确认替换。')
            for suggestion in doc['suggestions']:
                if suggestion['id'] in stale_ids and suggestion['status'] == 'pending':
                    suggestion['status'] = 'stale'
            self.sync(doc, text)
            # 两个快照与当前草稿在同一事务提交，失败不会留下半次替换。
            with self.store.connection() as db:
                self._persist(doc, 'before_workflow_apply', db)
                self.sync(doc, result['text'])
                for suggestion in doc['suggestions']:
                    if suggestion['status'] == 'pending':
                        suggestion['status'] = 'stale'
                doc['workflow_import'] = {'run_id': run_id, 'workflow_id': workflow_id,
                                          'result_id': result['id'], 'kind': result['kind'], 'label': result['label']}
                return self._persist(doc, 'workflow_apply', db)

    def open(self, relative):
        with self.lock:
            relative = self.canonical(relative)
            raw = self.read_file(self.path(relative))
            self.check_secret(raw)
            try:
                doc = self.get(relative)
            except KeyError:
                doc = {'workspace': str(self.root), 'path': relative, 'text': raw, 'disk_text': raw, 'disk_hash': digest(raw), 'suggestions': [], 'check': None}
                return self.persist(doc, 'open')
            if digest(raw) != doc['disk_hash'] and doc['text'] == doc['disk_text']:
                doc.update(text=raw, disk_text=raw, disk_hash=digest(raw))
                for s in doc['suggestions']:
                    if s['status'] == 'pending':
                        s['status'] = 'stale'
                return self.persist(doc, 'external_reload')
            return doc

    def sync(self, doc, text):
        if text == doc['text']:
            return
        # 使用共同的 UTF-16 单位映射；触及建议范围即永久失效，不能仅凭位置重新启用。
        before = doc['text']
        a, b, c = 0, len(before), len(text)
        while a < b and a < c and before[a] == text[a]:
            a += 1
        while b > a and c > a and before[b - 1] == text[c - 1]:
            b -= 1
            c -= 1
        # 聚合多次编辑时使用保守包围范围，保持线性耗时，避免重复长文 diff 退化。
        edits = [(units(before[:a]), units(before[:b]), units(text[a:c]))]
        for s in doc['suggestions']:
            if s['status'] != 'pending':
                continue
            if s['start'] is None:
                s['status'] = 'stale'
                continue
            # 重复片段在服务端 diff 中可能产生歧义，宁可要求复查，避免错改另一处。
            if before.count(s['original']) > 1:
                s['status'] = 'stale'
                continue
            start, end = s['start'], s['end']
            if any((a < end and b > start) or (a == b and start < a < end) for a, b, _ in edits):
                s['status'] = 'stale'
                # 原范围已经被替换，旧偏移可能落到新正文的 emoji 内部。
                # 失效建议不再参与定位，不能让它阻止用户保存正常编辑。
                continue
            shift = sum(n - (b-a) for a, b, n in edits if b <= start)
            s['start'], s['end'] = start + shift, end + shift
            if text[index(text, s['start']):index(text, s['end'])] != s['original']:
                s['status'] = 'stale'
        doc['text'] = text

    def mutate(self, relative, revision, text, action, suggestion_id=None, version=None, stale_ids=(), suggestions=None):
        with self.lock:
            relative = self.canonical(relative)
            self.check_secret(text)
            if len(text.encode('utf-8')) > 2_000_000:
                raise ValueError('文章超过 2 MB，保存已停止，请缩短内容。')
            doc = self.get(relative)
            if revision != doc['revision']:
                raise ConflictError('文章版本已变化，请重新打开文章后操作。')
            if suggestions is not None:
                doc['suggestions'] = suggestions
            for item in doc['suggestions']:
                if item['id'] in stale_ids and item['status'] == 'pending':
                    item['status'] = 'stale'
            self.sync(doc, text)
            if action in {'accept', 'ignore', 'reject'}:
                s = next((s for s in doc['suggestions'] if s['id'] == suggestion_id), None)
                if not s or s['status'] not in {'pending', 'stale'}:
                    raise ConflictError('建议已处理或不存在，请刷新。')
                if action == 'accept':
                    if s['status'] != 'pending' or s['start'] is None:
                        raise ConflictError('建议已失效或属于全文结构建议，请手动修改后重新检查。')
                    a, b = index(text, s['start']), index(text, s['end'])
                    if text[a:b] != s['original']:
                        raise ConflictError('原文已改变，请重新检查。')
                    for other in doc['suggestions']:
                        if other['id'] != s['id'] and other['status'] == 'pending' and other['start'] is not None:
                            if other['start'] < s['end'] and other['end'] > s['start']:
                                other['status'] = 'stale'
                    s['status'] = 'accepted'
                    self.sync(doc, text[:a] + s['replacement'] + text[b:])
                else:
                    s['status'] = 'ignored' if action == 'ignore' else 'rejected'
            elif action == 'restore':
                with self.store.connection() as db:
                    row = db.execute('SELECT payload FROM editor_versions WHERE id=? AND path=?', (version, self.identity(relative))).fetchone()
                if not row:
                    raise KeyError(version)
                restored = json.loads(row['payload'])
                self.persist(doc, 'before_restore')
                self.sync(doc, restored['text'])
                doc.pop('workflow_import', None)
                if restored.get('workflow_import'):
                    doc['workflow_import'] = restored['workflow_import']
                for s in doc['suggestions']:
                    if s['status'] == 'pending':
                        s['status'] = 'stale'
            elif action == 'reload':
                raw = self.read_file(self.path(relative))
                self.check_secret(raw)
                self.persist(doc, 'before_reload')
                self.sync(doc, raw)
                doc.pop('workflow_import', None)
                doc.update(disk_text=raw, disk_hash=digest(raw))
                for item in doc['suggestions']:
                    if item['status'] == 'pending':
                        item['status'] = 'stale'
            elif action == 'save':
                path = self.path(relative)
                if digest(self.read_file(path)) != doc['disk_hash']:
                    raise ConflictError('文件已被外部修改，保存已停止。请读取磁盘版本核对；当前草稿可从历史恢复。')
                # 原文直接写入，不经过渲染器。atomic_write 保留字符串中的 CRLF。
                atomic_write(path, doc['text'])
                doc.update(disk_hash=digest(doc['text']), disk_text=doc['text'])
            elif action != 'edit':
                raise ValueError('不支持的文章操作。')
            return self.persist(doc, action)

    def versions(self, relative):
        self.path(relative)
        with self.store.connection() as db:
            rows = db.execute('SELECT id,created_at,operation,payload FROM editor_versions WHERE path=? ORDER BY created_at DESC, rowid DESC LIMIT 200', (self.identity(relative),)).fetchall()
        return [{**{k: r[k] for k in ('id', 'created_at', 'operation')}, 'text': json.loads(r['payload'])['text']} for r in rows]
