"""私有诊断大字段独立保存，只通过所属运行／测试 ID 查询。"""
import json
from inkflow.web.storage import encode

LARGE = {'input', 'messages', 'raw_output', 'parsed_output', 'output'}


class DiagnosticStore:
    def __init__(self, store, recover=False):
        self.store = store
        with store.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS node_attempts(owner_kind TEXT NOT NULL, owner_id TEXT NOT NULL, id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS attempts_owner ON node_attempts(owner_kind,owner_id);
                CREATE TABLE IF NOT EXISTS model_tests(id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL);
            ''')
            if not recover:
                return
            # Like graph runs, tests cannot resume after restart.
            for row in db.execute('SELECT id,payload FROM model_tests').fetchall():
                test = json.loads(row['payload'])
                if test['status'] in {'running', 'stopping'}:
                    test.update(status='interrupted', error='服务重启，试运行已中断。')
                    db.execute('UPDATE model_tests SET payload=? WHERE id=?', (encode(test), row['id']))
            for row in db.execute('SELECT id,payload FROM node_attempts').fetchall():
                item = json.loads(row['payload'])
                if item['status'] == 'running':
                    item.update(status='interrupted', error={'category': 'interrupted', 'message': '服务重启，执行已中断。'})
                    db.execute('UPDATE node_attempts SET payload=? WHERE id=?', (encode(item), row['id']))

    def save(self, item):
        with self.store.connection() as db:
            db.execute('INSERT INTO node_attempts VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (item['owner_kind'], item['owner_id'], item['id'], encode(item)))

    def list(self, kind, owner):
        with self.store.connection() as db:
            rows = db.execute('SELECT payload FROM node_attempts WHERE owner_kind=? AND owner_id=? ORDER BY rowid', (kind, owner)).fetchall()
        result = []
        for row in rows:
            item = json.loads(row['payload'])
            result.append({**{k: v for k, v in item.items() if k not in LARGE},
                           'content_available': [key for key in LARGE if key in item], 'truncated': False})
        return result

    def get(self, kind, owner, identity):
        with self.store.connection() as db:
            row = db.execute('SELECT payload FROM node_attempts WHERE owner_kind=? AND owner_id=? AND id=?', (kind, owner, identity)).fetchone()
        if not row:
            raise KeyError(identity)
        return json.loads(row['payload'])
