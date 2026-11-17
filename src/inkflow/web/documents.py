"""稳定文档身份与精确文件授权；复用编辑服务的版本、候选与保存保护。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from inkflow.services.local_files import atomic_write
from inkflow.web.editor import EditorService, digest
from inkflow.web.runs import ConflictError
from inkflow.web.storage import encode, now


def checked_location(value: str | Path, *, directory=False, scope: Path | None = None) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('请提供完整绝对路径，不能包含上级目录跳转。')
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('不支持符号链接文章或目录。')
    parts = path.relative_to(scope).parts if scope is not None else path.parts
    if not directory and any(part.startswith('.') for part in parts):
        raise ValueError('不支持隐藏文章或目录。')
    path = Path(os.path.normcase(str(path.resolve())))
    if not directory and path.suffix.lower() not in {'.md', '.mdx', '.markdown'}:
        raise ValueError('仅支持 Markdown / MDX 文件。')
    return path


class DocumentService(EditorService):
    docs_table = 'document_copies'
    versions_table = 'document_versions'

    def __init__(self, settings):
        super().__init__(settings)
        with self.store.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, location TEXT UNIQUE, origin_id TEXT, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS document_copies (path TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS document_versions (id TEXT PRIMARY KEY, path TEXT NOT NULL,
                    created_at TEXT NOT NULL, operation TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS document_aliases (legacy_key TEXT PRIMARY KEY, document_id TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS document_versions_path ON document_versions(path);
                CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, root TEXT UNIQUE NOT NULL);
            ''')
        self.migrate()

    def resource(self, document_id):
        with self.store.connection() as db:
            row = db.execute('SELECT * FROM documents WHERE id=?', (document_id,)).fetchone()
        if not row:
            raise KeyError(document_id)
        return dict(row)

    def canonical(self, document_id):
        self.resource(document_id)
        return document_id

    identity = canonical

    def checked_file(self, location):
        # 显式授权的根目录本身可位于隐藏父目录；根目录以下仍禁止隐藏路径。
        path = Path(location).expanduser()
        with self.store.connection() as db:
            roots = [Path(r['root']) for r in db.execute('SELECT root FROM workspaces')]
        scopes = [root for root in roots if path.is_relative_to(root)]
        scope = max(scopes, key=lambda p: len(p.parts)) if scopes else None
        return checked_location(path, scope=scope)

    def _persist(self, doc, operation, db):
        # 内容相等不能证明本次恢复／候选已写回；保存感知独立于文本 diff。
        if operation in {'open', 'external_reload', 'reload', 'save', 'first_save', 'save_as'}:
            doc['draft_pending'] = False
        elif operation in {'new', 'restore', 'workflow_apply'} or not doc.get('location') or doc['text'] != doc['disk_text']:
            doc['draft_pending'] = True
        return super()._persist(doc, operation, db)

    def path(self, document_id):
        location = self.resource(document_id)['location']
        if not location:
            raise ValueError('未命名文章请先选择保存位置。')
        path = self.checked_file(location)
        if str(path) != location or not path.is_file():
            raise ValueError('原文件已失联或不再是普通文件；草稿仍保留，请核对原位置或另存为。')
        return path

    def get(self, document_id):
        resource = self.resource(document_id)
        with self.store.connection() as db:
            row = db.execute('SELECT payload FROM document_copies WHERE path=?', (document_id,)).fetchone()
        if not row:
            raise KeyError(document_id)
        doc = json.loads(row['payload'])
        doc.update(document_id=document_id, location=resource['location'], path=resource['location'] or '未命名文章')
        doc.setdefault('draft_pending', not resource['location'] or doc['text'] != doc['disk_text'])
        doc['file_error'] = None
        if resource['location']:
            try:
                self.path(document_id)
            except (ValueError, OSError) as error:
                doc['file_error'] = str(error)
        if doc.get('check') and doc['check']['status'] == 'running':
            doc['check'] = {**doc['check'], 'status': 'interrupted'}
        return doc

    def register(self, location):
        with self.lock:
            path = self.checked_file(location)
            if not path.is_file():
                raise ValueError('文章不存在或不是普通文件。')
            raw = self.read_file(path)
            self.check_secret(raw)
            with self.store.connection() as db:
                row = db.execute('SELECT id FROM documents WHERE location=?', (str(path),)).fetchone()
                if row:
                    document_id = row['id']
                else:
                    document_id = uuid4().hex
                    db.execute('INSERT INTO documents VALUES (?,?,NULL,?)', (document_id, str(path), now()))
                    self._persist(self._new(document_id, str(path), raw), 'open', db)
            return self.open(document_id)

    @staticmethod
    def _new(document_id, location, text=''):
        return {'document_id': document_id, 'workspace': '', 'path': location or '未命名文章',
                'location': location, 'text': text, 'disk_text': text if location else '',
                'disk_hash': digest(text) if location else None, 'suggestions': [], 'check': None}

    def new(self):
        with self.lock, self.store.connection() as db:
            document_id = uuid4().hex
            db.execute('INSERT INTO documents VALUES (?,NULL,NULL,?)', (document_id, now()))
            return self._persist(self._new(document_id, None), 'new', db)

    def open(self, document_id):
        with self.lock:
            doc = self.get(document_id)
            if not doc['location'] or doc['file_error']:
                return doc
            try:
                raw = self.read_file(self.path(document_id))
                self.check_secret(raw)
            except (ValueError, OSError) as error:
                return {**doc, 'file_error': str(error)}
            if digest(raw) != doc['disk_hash'] and not doc['draft_pending'] and doc['text'] == doc['disk_text']:
                doc.update(text=raw, disk_text=raw, disk_hash=digest(raw))
                for item in doc['suggestions']:
                    if item['status'] == 'pending':
                        item['status'] = 'stale'
                return self.persist(doc, 'external_reload')
            doc['disk_conflict'] = digest(raw) != doc['disk_hash']
            return doc

    def document_source(self, document_id, revision, text):
        with self.lock:
            self.check_secret(text)
            doc = self.get(document_id)
            if doc['revision'] != revision:
                raise ConflictError('文章版本已变化，请重新打开后启动工作流。')
            if doc['text'] != text:
                doc = self.mutate(document_id, revision, text, 'edit')
            return {'document_id': document_id, 'workspace': doc.get('workspace', ''), 'path': doc['path'],
                    'revision': doc['revision'], 'text_hash': digest(text)}

    def save_as(self, document_id, revision, text, location):
        with self.lock:
            # 即使创建目标失败，输入仍有持久草稿和版本可恢复。
            doc = self.mutate(document_id, revision, text, 'edit')
            path = self.checked_file(location)
            if not path.parent.is_dir():
                raise ValueError('目标目录不存在，请选择已有目录。')
            with self.store.connection() as db:
                known = db.execute('SELECT id FROM documents WHERE location=?', (str(path),)).fetchone()
            if path.exists() or known:
                raise ConflictError('目标名称已存在或已有文档登记，请选择其它文件名；不会覆盖。')
            # 完整临时文件原子发布，排除检查与创建之间的同名竞争。
            atomic_write(path, text, exclusive=True)
            target_id = document_id if not doc['location'] else uuid4().hex
            with self.store.connection() as db:
                if target_id == document_id:
                    db.execute('UPDATE documents SET location=? WHERE id=?', (str(path), target_id))
                else:
                    db.execute('INSERT INTO documents VALUES (?,?,?,?)', (target_id, str(path), document_id, now()))
                target = {**doc, 'document_id': target_id, 'path': str(path), 'location': str(path),
                          'disk_text': text, 'disk_hash': digest(text), 'file_error': None, 'disk_conflict': False,
                          'suggestions': []}
                if target_id != document_id:
                    target.pop('workflow_import', None)
                    target.pop('migration_conflicts', None)
                return self._persist(target, 'first_save' if target_id == document_id else 'save_as', db)

    def relocate(self, document_id, revision, text, location):
        """显式关联移动后的同一文件，不移动文件、不合并身份或覆盖正文。"""
        with self.lock:
            doc = self.get(document_id)
            if doc['revision'] != revision:
                raise ConflictError('文章版本已变化，请重新打开后再关联。')
            previous = Path(doc['location']) if doc['location'] else None
            if previous is None or previous.exists() or previous.is_symlink():
                raise ValueError('仅支持为原位置已不存在的文章重新定位。')
            path = self.checked_file(location)
            if not path.is_file():
                raise ValueError('请选择移动后存在的普通文章文件。')
            raw = self.read_file(path)
            self.check_secret(raw)
            if digest(raw) != doc['disk_hash']:
                raise ConflictError('目标内容与原磁盘版本不同；请另存为草稿或独立打开核对。')
            with self.store.connection() as db:
                if db.execute('SELECT id FROM documents WHERE location=?', (str(path),)).fetchone():
                    raise ConflictError('目标文件已有独立文档，请从侧栏或最近文章打开；不会合并历史。')
            doc = self.mutate(document_id, revision, text, 'edit')
            doc.update(location=str(path), path=str(path), file_error=None, disk_conflict=False)
            with self.store.connection() as db:
                db.execute('UPDATE documents SET location=? WHERE id=?', (str(path), document_id))
                return self._persist(doc, 'relocate', db)

    def list_documents(self):
        with self.store.connection() as db:
            rows = db.execute('SELECT d.id,d.location,c.payload FROM documents d JOIN document_copies c ON c.path=d.id ORDER BY d.created_at DESC').fetchall()
        return [{'document_id': r['id'], 'path': r['location'] or '未命名文章', 'location': r['location'],
                 'updated_at': (doc := json.loads(r['payload']))['updated_at'], 'revision': doc['revision'],
                 'draft': doc.get('draft_pending', False) or not r['location'] or doc['text'] != doc['disk_text']} for r in rows]

    def delete_draft(self, document_id, revision):
        """删除未命名工作副本；保留无正文的身份记录以读取已有运行审计。"""
        with self.lock, self.store.transaction() as db:
            doc = self.get(document_id)
            if doc['location']:
                raise ConflictError('已有文件的文章只能清除最近记录，不能删除草稿。')
            if doc['revision'] != revision:
                raise ConflictError('草稿已在其它页面更新，请刷新文章状态后重新确认删除。')
            if db.execute('SELECT 1 FROM article_active WHERE article_key=?', (document_id,)).fetchone():
                raise ConflictError('草稿仍有运行或待办，请先结束或停止任务后再删除。')
            tables = {r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'automations' in tables and db.execute(
                    "SELECT 1 FROM automations WHERE json_extract(payload,'$.document_id')=?", (document_id,)).fetchone():
                raise ConflictError('草稿已被持续关注任务引用，请先保存到文件，再清除最近记录。')
            if 'automation_changes' in tables and db.execute(
                    "SELECT 1 FROM automation_changes WHERE json_extract(payload,'$.document_id')=? "
                    "AND json_extract(payload,'$.state') IN ('pending','conflict','blocked')", (document_id,)).fetchone():
                raise ConflictError('草稿仍有来源候选待处理，请先处理候选后再删除。')
            db.execute('DELETE FROM document_versions WHERE path=?', (document_id,))
            db.execute('DELETE FROM document_copies WHERE path=?', (document_id,))

    def versions(self, document_id):
        versions = super().versions(document_id)
        # 旧冲突稿不能被最近 200 版窗口挤出，否则提示无法完成核对。
        with self.store.connection() as db:
            rows = db.execute("SELECT * FROM document_versions WHERE path=? AND operation='migration_conflict' ORDER BY created_at DESC",
                              (document_id,)).fetchall()
        present = {v['id'] for v in versions}
        return versions + [{**{k: r[k] for k in ('id', 'created_at', 'operation')}, 'text': json.loads(r['payload'])['text']}
                           for r in rows if r['id'] not in present]

    def migrate(self):
        """加法迁移，旧表原样保留。冲突稿永远可通过新版本表恢复。"""
        with self.lock, self.store.connection() as db:
            rows = db.execute('SELECT path,payload FROM editor_docs ORDER BY path').fetchall()
            for row in rows:
                key, old = row['path'], json.loads(row['payload'])
                if db.execute('SELECT 1 FROM document_aliases WHERE legacy_key=?', (key,)).fetchone():
                    continue
                root, separator, relative = key.rpartition('|')
                if not separator or Path(relative).is_absolute() or '..' in Path(relative).parts:
                    continue
                try:
                    location = str(checked_location(Path(root) / relative, scope=Path(root)))
                    workspace_root = str(checked_location(root, directory=True))
                except (ValueError, OSError):
                    continue  # 无法证明原文件边界的旧记录保持原样，不猜测身份。
                db.execute('INSERT OR IGNORE INTO workspaces VALUES (?,?)', (uuid4().hex, workspace_root))
                resource = db.execute('SELECT id FROM documents WHERE location=?', (location,)).fetchone()
                document_id = resource['id'] if resource else uuid4().hex
                if not resource:
                    db.execute('INSERT INTO documents VALUES (?,?,NULL,?)', (document_id, location, now()))
                doc = {**old, 'document_id': document_id, 'location': location, 'path': location}
                current = db.execute('SELECT payload FROM document_copies WHERE path=?', (document_id,)).fetchone()
                if current:
                    current = json.loads(current['payload'])
                    if current['text'] != doc['text']:
                        current.setdefault('migration_conflicts', []).append({'legacy_key': key, 'version': doc['revision']})
                        db.execute('UPDATE document_copies SET payload=? WHERE path=?', (encode(current), document_id))
                else:
                    db.execute('INSERT INTO document_copies VALUES (?,?)', (document_id, encode(doc)))
                versions = db.execute('SELECT * FROM editor_versions WHERE path=?', (key,)).fetchall()
                for version in versions:
                    db.execute('INSERT OR IGNORE INTO document_versions VALUES (?,?,?,?,?)',
                               (version['id'], document_id, version['created_at'], version['operation'], version['payload']))
                db.execute('INSERT OR IGNORE INTO document_versions VALUES (?,?,?,?,?)',
                           (doc['revision'], document_id, doc['updated_at'], 'migration_snapshot', encode(doc)))
                if current and current['text'] != doc['text']:
                    db.execute("UPDATE document_versions SET operation='migration_conflict' WHERE id=?", (doc['revision'],))
                db.execute('INSERT INTO document_aliases VALUES (?,?)', (key, document_id))
            # 历史运行只增补资源 ID，输入快照、旧路径、revision 与事件保持不变。
            for row in db.execute('SELECT id,payload FROM runs').fetchall():
                record, changed = json.loads(row['payload']), False
                for field in ('editor_source', 'editor_base'):
                    source = record.get(field)
                    if source and not source.get('document_id'):
                        key = source['workspace'] + '|' + source['path']
                        alias = db.execute('SELECT document_id FROM document_aliases WHERE legacy_key=?', (key,)).fetchone()
                        if alias:
                            source['document_id'] = alias['document_id']
                            changed = True
                if changed:
                    db.execute('UPDATE runs SET payload=? WHERE id=?', (encode(record), row['id']))


class WorkspaceService:
    def __init__(self, documents):
        self.documents = documents

    def register(self, location):
        root = checked_location(location, directory=True)
        if not root.is_dir():
            raise ValueError('工作区目录不存在，请选择已有目录。')
        with self.documents.lock, self.documents.store.connection() as db:
            db.execute('INSERT OR IGNORE INTO workspaces VALUES (?,?)', (uuid4().hex, str(root)))
            return dict(db.execute('SELECT * FROM workspaces WHERE root=?', (str(root),)).fetchone())

    def list(self):
        with self.documents.store.connection() as db:
            return [dict(r) for r in db.execute('SELECT * FROM workspaces ORDER BY root')]

    def editor(self, workspace_id):
        with self.documents.store.connection() as db:
            row = db.execute('SELECT root FROM workspaces WHERE id=?', (workspace_id,)).fetchone()
        if not row:
            raise KeyError(workspace_id)
        root = checked_location(row['root'], directory=True)
        return WorkspaceEditor(self.documents, root)

    def files(self, workspace_id):
        editor = self.editor(workspace_id)
        result = editor.files()
        known = {d['location']: d for d in self.documents.list_documents()}
        directories = []
        for directory, folders, _ in os.walk(editor.root, followlinks=False):
            folders[:] = sorted(f for f in folders if not f.startswith('.') and f not in {'node_modules', 'dist', '__pycache__'} and not (Path(directory) / f).is_symlink())
            directories.extend((Path(directory) / f).relative_to(editor.root).as_posix() for f in folders)
            if len(directories) > 10000:
                raise ValueError('工作区超过 10000 个目录，请选择更小的工作区。')
        entries = []
        for relative in result['files']:
            path = editor.path(relative)
            location = str(checked_location(path, scope=editor.root))
            meta = known.get(location, {})
            entries.append({'path': relative, 'location': location, 'modified_at': path.stat().st_mtime,
                            'document_id': meta.get('document_id'), 'draft': meta.get('draft', False)})
        return {**result, 'id': workspace_id, 'directories': directories, 'entries': entries}


class WorkspaceEditor(EditorService):
    """旧路径接口只在原工作区边界内适配为 document_id。"""
    def __init__(self, documents, root):
        self.documents, self.root = documents, root
        self.lock, self.store = documents.lock, documents.store

    def resolve(self, relative):
        return self.documents.register(str(self.path(relative)))['document_id']

    def open(self, relative):
        return self.view(self.documents.open(self.resolve(relative)), relative)

    def view(self, doc, relative):
        return {**doc, 'workspace': str(self.root), 'path': self.canonical(relative)}

    def mutate(self, relative, *args, **kwargs):
        return self.view(self.documents.mutate(self.resolve(relative), *args, **kwargs), relative)

    def versions(self, relative):
        return self.documents.versions(self.resolve(relative))
