"""Single-host recovery protocol. SQLite is authoritative; filesystem effects reconcile.

No pickle, extra dependency, distributed scheduler or cross-resource exactly-once claim.
RecoveryHold deliberately bypasses business-node Exception handlers.
"""
from contextvars import ContextVar
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple, WRITES_IDX_MAP
from langgraph.config import get_config, get_stream_writer
from inkflow.web.storage import encode

PROTOCOL = 1
_context = ContextVar('inkflow_durable_node', default=None)
# Tests inject callbacks here, never through production HTTP or environment variables.
fault_hook = None


def fault(point):
    if fault_hook:
        fault_hook(point)


def fingerprint():
    root = Path(__file__).parents[1]
    files = sorted(p for p in root.rglob('*.py') if '__pycache__' not in p.parts)
    digest = hashlib.sha256()
    for p in files:
        digest.update(p.relative_to(root).as_posix().encode())
        digest.update(p.read_bytes().replace(b'\r\n', b'\n'))
    for name in ('langgraph', 'langgraph-checkpoint'):
        digest.update(importlib.metadata.version(name).encode())
    return digest.hexdigest()


IMPLEMENTATION = fingerprint()


class RecoveryHold(BaseException):
    def __init__(self, message, status='verification_required'):
        self.status = status
        super().__init__(message)


class ServicePause(BaseException):
    """Quiesce at a node boundary without turning shutdown into a user stop."""


class ProcessLock:
    """OS releases this lock on process death; expiry alone never fences a live writer."""
    def __init__(self, path):
        self.stream = open(path, 'a+b')
        self.stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                if path.stat().st_size == 0:
                    self.stream.write(b'0'); self.stream.flush(); self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise ValueError('此数据目录已有运行服务，请先关闭该服务；不能同时启动两个执行器。') from None

    def close(self):
        if self.stream.closed:
            return
        if os.name == 'nt':
            import msvcrt
            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        self.stream.close()


class DurableStore:
    def __init__(self, store):
        self.store = store
        self.owner = uuid4().hex
        self.pausing = threading.Event()
        self.document_lock = threading.RLock()
        self.process_lock = ProcessLock(store.directory / 'execution.lock')
        with store.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS execution_work(run_id TEXT PRIMARY KEY, state TEXT NOT NULL,
                    owner TEXT, lease REAL, command TEXT, stop INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS execution_checkpoints(run_id TEXT, ns TEXT, id TEXT,
                    kind TEXT, data BLOB, meta_kind TEXT, meta BLOB, parent TEXT, PRIMARY KEY(run_id,ns,id));
                CREATE TABLE IF NOT EXISTS execution_writes(run_id TEXT, ns TEXT, checkpoint TEXT,
                    task TEXT, idx INTEGER, channel TEXT, kind TEXT, data BLOB,
                    PRIMARY KEY(run_id,ns,checkpoint,task,idx));
                CREATE TABLE IF NOT EXISTS execution_nodes(run_id TEXT, id TEXT, node TEXT, result TEXT,
                    PRIMARY KEY(run_id,id));
                CREATE TABLE IF NOT EXISTS execution_calls(run_id TEXT, id TEXT, state TEXT, result TEXT,
                    PRIMARY KEY(run_id,id));
                CREATE TABLE IF NOT EXISTS execution_effects(run_id TEXT, id TEXT, state TEXT, payload TEXT,
                    PRIMARY KEY(run_id,id));
            ''')
            # Acquiring the OS lock proves the preceding owner has exited, even if its lease is fresh.
            db.execute("UPDATE execution_work SET state='queued',owner=NULL,lease=NULL WHERE state='leased'")

    def enqueue(self, run_id, command=None):
        with self.store.connection() as db:
            db.execute("INSERT INTO execution_work(run_id,state,command) VALUES (?,'queued',?) ON CONFLICT(run_id) DO UPDATE SET state='queued',command=excluded.command", (run_id, encode(command)))

    def claim(self, run_id):
        with self.store.connection() as db:
            result = db.execute("UPDATE execution_work SET state='leased',owner=?,lease=? WHERE run_id=? AND state='queued'", (self.owner, time.time()+30, run_id))
            return result.rowcount == 1

    def heartbeat(self):
        with self.store.connection() as db:
            db.execute("UPDATE execution_work SET lease=? WHERE owner=? AND state='leased'", (time.time()+30, self.owner))

    def release(self, run_id, state):
        with self.store.connection() as db:
            db.execute('UPDATE execution_work SET state=?,owner=NULL,lease=NULL,command=NULL WHERE run_id=? AND owner=?', (state, run_id, self.owner))

    def work(self, run_id):
        with self.store.connection() as db:
            row = db.execute('SELECT * FROM execution_work WHERE run_id=?', (run_id,)).fetchone()
        return dict(row) if row else None

    def effects(self, run_id):
        with self.store.connection() as db:
            rows = db.execute('SELECT id,state,payload FROM execution_effects WHERE run_id=? ORDER BY rowid', (run_id,)).fetchall()
        return [{**json.loads(r['payload']), 'id': r['id'], 'state': r['state']} for r in rows]

    def set_effect(self, run_id, identity, state, payload):
        with self.store.connection() as db:
            db.execute('INSERT INTO execution_effects VALUES (?,?,?,?) ON CONFLICT(run_id,id) DO UPDATE SET state=excluded.state,payload=excluded.payload', (run_id, identity, state, encode(payload)))

    def saver(self, run_id):
        return SQLiteSaver(self.store, run_id)

    def wrap(self, runtime, name, function):
        def node(state):
            if self.pausing.is_set():
                raise ServicePause()
            config = get_config()
            step = config.get('metadata', {}).get('langgraph_step', 0)
            identity = hashlib.sha256(encode([name, step, state]).encode()).hexdigest()
            with self.store.connection() as db:
                row = db.execute('SELECT result FROM execution_nodes WHERE run_id=? AND id=?', (runtime.owner_id, identity)).fetchone()
            if row:
                get_stream_writer()({'type': 'node_started', 'node': name, 'input': {}, 'replayed': True})
                return json.loads(row['result'])
            context = {'store': self, 'run': runtime.owner_id, 'id': identity, 'node': name, 'call': 0}
            token = _context.set(context)
            try:
                result = runtime.scrub(function(state))
                fault('node_returned')
                with self.store.connection() as db:
                    db.execute('INSERT OR REPLACE INTO execution_nodes VALUES (?,?,?,?)', (runtime.owner_id, identity, name, encode(runtime.scrub(result))))
                fault('node_committed')
                return result
            finally:
                _context.reset(token)
        return node


class SQLiteSaver(BaseCheckpointSaver):
    """Public saver contract; full channel snapshots and task writes commit synchronously."""
    def __init__(self, store, run_id):
        super().__init__()
        self.store, self.run_id = store, run_id

    def get_tuple(self, config):
        c = config['configurable']; ns = c.get('checkpoint_ns', '')
        with self.store.connection() as db:
            if c.get('checkpoint_id'):
                row = db.execute('SELECT * FROM execution_checkpoints WHERE run_id=? AND ns=? AND id=?', (self.run_id, ns, c['checkpoint_id'])).fetchone()
            else:
                row = db.execute('SELECT * FROM execution_checkpoints WHERE run_id=? AND ns=? ORDER BY id DESC LIMIT 1', (self.run_id, ns)).fetchone()
            if row is None:
                return None
            writes = db.execute('SELECT * FROM execution_writes WHERE run_id=? AND ns=? AND checkpoint=? ORDER BY task,idx', (self.run_id, ns, row['id'])).fetchall()
        cfg = {'configurable': {'thread_id': self.run_id, 'checkpoint_ns': ns, 'checkpoint_id': row['id']}}
        parent = {'configurable': {**cfg['configurable'], 'checkpoint_id': row['parent']}} if row['parent'] else None
        return CheckpointTuple(cfg, self.serde.loads_typed((row['kind'], row['data'])), self.serde.loads_typed((row['meta_kind'], row['meta'])), parent,
                               [(w['task'], w['channel'], self.serde.loads_typed((w['kind'], w['data']))) for w in writes])

    def put(self, config, checkpoint, metadata, new_versions):
        c = config['configurable']; ns = c.get('checkpoint_ns', '')
        kind, data = self.serde.dumps_typed(checkpoint)
        mk, meta = self.serde.dumps_typed(metadata)
        fault('checkpoint_before')
        with self.store.connection() as db:
            db.execute('INSERT OR REPLACE INTO execution_checkpoints VALUES (?,?,?,?,?,?,?,?)', (self.run_id, ns, checkpoint['id'], kind, data, mk, meta, c.get('checkpoint_id')))
        fault('checkpoint_after')
        return {'configurable': {'thread_id': self.run_id, 'checkpoint_ns': ns, 'checkpoint_id': checkpoint['id']}}

    def put_writes(self, config, writes, task_id, task_path=''):
        c = config['configurable']
        with self.store.connection() as db:
            for i, (channel, value) in enumerate(writes):
                idx = WRITES_IDX_MAP.get(channel, i)
                kind, data = self.serde.dumps_typed(value)
                verb = 'REPLACE' if idx < 0 else 'IGNORE'
                db.execute(f'INSERT OR {verb} INTO execution_writes VALUES (?,?,?,?,?,?,?,?)', (self.run_id, c.get('checkpoint_ns', ''), c['checkpoint_id'], task_id, idx, channel, kind, data))

    def list(self, config, *, filter=None, before=None, limit=None):
        with self.store.connection() as db:
            rows = db.execute('SELECT ns,id FROM execution_checkpoints WHERE run_id=? ORDER BY id DESC', (self.run_id,)).fetchall()
        count = 0
        for row in rows:
            if before and row['id'] >= before['configurable']['checkpoint_id']:
                continue
            value = self.get_tuple({'configurable': {'checkpoint_ns': row['ns'], 'checkpoint_id': row['id']}})
            if filter and any(value.metadata.get(k) != v for k, v in filter.items()):
                continue
            if limit is not None and count >= limit:
                break
            count += 1
            yield value


def model_call(transport, messages, scrub=lambda value: value, **kwargs):
    context = _context.get()
    if context is None:
        return transport(messages, **kwargs)
    context['call'] += 1
    identity = context['id'] + ':' + str(context['call'])
    durable, run = context['store'], context['run']
    with durable.store.connection() as db:
        row = db.execute('SELECT state,result FROM execution_calls WHERE run_id=? AND id=?', (run, identity)).fetchone()
        if row:
            if row['state'] == 'succeeded':
                return json.loads(row['result'])
            raise RecoveryHold('模型请求可能已发送，不能自动重发。请停止此运行，核对调用记录后基于当前稿件新建运行。', 'recovery_blocked')
        db.execute("INSERT INTO execution_calls VALUES (?,?,'started',NULL)", (run, identity))
    result = transport(messages, **kwargs)
    fault('model_returned')
    with durable.store.connection() as db:
        db.execute("UPDATE execution_calls SET state='succeeded',result=? WHERE run_id=? AND id=?", (encode(scrub(str(result))), run, identity))
    return result


def evidence(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise RecoveryHold('原输出目标出现符号链接，请核验；未读取或写入新目标。')
    if not path.exists():
        return None
    if not path.is_file():
        raise RecoveryHold('原输出目标不再是普通文件，请核验。')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def has_effect(path):
    c = _context.get()
    return bool(c and any(e['path'] == str(Path(path).absolute()) and e['node_execution'] == c['id'] for e in c['store'].effects(c['run'])))


def write_effect(path, content, write):
    c = _context.get()
    if c is None:
        return write()
    with c['store'].document_lock:
        return _write_effect(c, path, content, write)


def _write_effect(c, path, content, write):
    path = Path(path).absolute()
    identity = hashlib.sha256(encode([c['id'], str(path)]).encode()).hexdigest()
    durable, run = c['store'], c['run']
    old = next((e for e in durable.effects(run) if e['id'] == identity), None)
    if old:
        if old['state'] == 'succeeded':
            return  # A confirmed effect is never rewritten, even after later external changes.
        try:
            actual = evidence(Path(old['path']))
        except RecoveryHold:
            durable.set_effect(run, identity, 'unknown', old)
            raise
        if actual == old['sha256']:
            durable.set_effect(run, identity, 'succeeded', old)
            return
        durable.set_effect(run, identity, 'unknown', old)
        raise RecoveryHold('输出结果待核验：原目标缺失或内容与记录不一致。不会重写；可重新核验，或停止后人工保留文件并新建运行。')
    record = durable.store.get(run)
    base = record.get('editor_base', {})
    with durable.store.connection() as db:
        doc = db.execute('SELECT payload FROM document_copies WHERE path=?', (record['editor_source']['document_id'],)).fetchone()
    kind = record.get('version_package', {}).get('compiled', {}).get('nodes', {}).get(c['node'], {}).get('type', {}).get('node_type_id')
    if c['node'] != 'report' and kind != 'write-report' and (not doc or json.loads(doc['payload'])['revision'] != base.get('revision')):
        raise RecoveryHold('文章在确认后发生变化，当前草稿已保留。请停止此运行，基于当前稿件重新审阅后输出。', 'recovery_blocked')
    payload = {'path': str(path), 'content': content, 'sha256': hashlib.sha256(content.encode('utf-8')).hexdigest(),
               'before': evidence(path), 'node_execution': c['id'], 'node': c['node']}
    durable.set_effect(run, identity, 'not_started', payload)
    fault('effect_before')
    durable.set_effect(run, identity, 'executing', payload)
    try:
        write()
        fault('effect_written')
    except Exception as error:
        durable.set_effect(run, identity, 'failed' if getattr(error, 'effect_not_written', False) else 'unknown', payload)
        raise RecoveryHold('文件写入未能确认，请核验原目标；不会自动再次覆盖。') from None
    durable.set_effect(run, identity, 'succeeded', payload)
    fault('effect_receipt')
