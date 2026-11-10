"""SQLite 业务历史，不保存 LangGraph checkpoint 或任何 API Key。"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from inkflow.services.file_permissions import private_directory, restrict_access
from inkflow.workflows import DEFAULT_WORKFLOW_ID, get_workflow


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def workflow_record(record: dict) -> dict:
    """旧运行在读取时归入内置流程，保留原始数据库与事件。"""
    record.setdefault("workflow_id", DEFAULT_WORKFLOW_ID)
    if "workflow" not in record:
        record["workflow"] = get_workflow(record["workflow_id"]).describe()
    return record


class HistoryStore:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        private_directory(self.directory)
        self.path = self.directory / "history.sqlite3"
        # 在 SQLite 打开前创建私有空文件；修复旧数据库及残留日志权限。
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        restrict_access(self.path)
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                restrict_access(Path(str(self.path) + suffix))
            except FileNotFoundError:
                pass
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, creation_id TEXT UNIQUE, name TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL REFERENCES runs(id), seq INTEGER NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(run_id, seq)
                );
                CREATE TABLE IF NOT EXISTS actions (
                    run_id TEXT NOT NULL REFERENCES runs(id), request_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL, result TEXT NOT NULL,
                    PRIMARY KEY(run_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS article_active (
                    article_key TEXT PRIMARY KEY, run_id TEXT UNIQUE NOT NULL REFERENCES runs(id)
                );
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, run_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return workflow_record(json.loads(row["payload"]))

    def by_creation_id(self, creation_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT payload FROM runs WHERE creation_id=?", (creation_id,)).fetchone()
        return workflow_record(json.loads(row["payload"])) if row else None

    def list(self, limit: int = 50, offset: int = 0, workspace=None, path=None, unlinked=False) -> list[dict]:
        with self.connection() as db:
            clauses, parameters = [], []
            if workspace is not None:
                clauses.append("json_extract(payload,'$.editor_source.workspace')=?")
                parameters.append(workspace)
            if path is not None:
                clauses.append("json_extract(payload,'$.editor_source.path')=?")
                parameters.append(path)
            if unlinked:
                clauses.append("json_extract(payload,'$.editor_source') IS NULL")
            where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
            rows = db.execute("SELECT id,name,status,created_at,updated_at,payload FROM runs" + where + " ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?",
                              (*parameters, limit, offset)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            payload = json.loads(item.pop("payload"))
            item["workflow_id"] = payload.get("workflow_id", DEFAULT_WORKFLOW_ID)
            item["workflow_name"] = payload.get("workflow", {}).get("name", "文章处理与审阅")
            item["editor_source"] = payload.get("editor_source")
            item["pending_count"] = sum(s['status'] in {'pending', 'stale'} for s in payload.get('annotations', payload['state'].get('suggestions', [])))
            result.append(item)
        return result

    def create(self, record: dict) -> None:
        with self.connection() as db:
            db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?)", (record["id"], record["creation_id"], record["name"],
                       record["status"], record["created_at"], record["updated_at"], encode(record)))
            if record.get('editor_source') and record['status'] in {'running', 'waiting', 'stopping'}:
                source = record['editor_source']
                key = encode([source['workspace'], source['path']])
                db.execute('INSERT INTO article_active VALUES (?,?)', (key, record['id']))

    def save_event(self, record: dict, event_type: str, summary: str, **details) -> dict:
        """同一事务保存状态和事件游标，刷新不会读到只写了一半的状态。"""

        record["event_cursor"] += 1
        record["updated_at"] = now()
        execution = next((x for x in reversed(record["executions"]) if x["node"] == record.get("current_node")), {})
        event = {"seq": record["event_cursor"], "time": record["updated_at"], "type": event_type,
                 "node": record.get("current_node"), "execution_id": execution.get("id"),
                 "iteration": execution.get("iteration"), "status": record["status"], "summary": summary, **details}
        with self.connection() as db:
            db.execute("UPDATE runs SET name=?,status=?,updated_at=?,payload=? WHERE id=?",
                       (record["name"], record["status"], record["updated_at"], encode(record), record["id"]))
            db.execute("INSERT INTO events VALUES (?,?,?)", (record["id"], event["seq"], encode(event)))
            if record['status'] in {'completed', 'failed', 'stopped', 'interrupted'}:
                db.execute('DELETE FROM article_active WHERE run_id=?', (record['id'],))
        return event

    def events(self, run_id: str, after: int = 0, limit: int = 250) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT payload FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?",
                              (run_id, after, limit)).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def action(self, run_id: str, request_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT request_hash,result FROM actions WHERE run_id=? AND request_id=?",
                             (run_id, request_id)).fetchone()
        return {"hash": row["request_hash"], "result": json.loads(row["result"])} if row else None

    def remember_action(self, run_id: str, request_id: str, request_hash: str, result: dict) -> None:
        with self.connection() as db:
            db.execute("INSERT INTO actions VALUES (?,?,?,?)", (run_id, request_id, request_hash, encode(result)))

    def recover(self) -> None:
        with self.connection() as db:
            rows = db.execute("SELECT payload FROM runs WHERE status IN ('running','waiting','stopping')").fetchall()
        for row in rows:
            record = workflow_record(json.loads(row["payload"]))
            record.update(status="interrupted", pending=None)
            for execution in record["executions"]:
                if execution["status"] in {"running", "waiting"}:
                    execution.update(status="interrupted", finished_at=now())
            self.save_event(record, "interrupted", "服务已重启，内存检查点失效；可使用原输入重新运行。")
        with self.connection() as db:
            db.execute('DELETE FROM article_active')
