"""本地隔离回归：不调用模型服务、不读取用户文章与历史。"""

import asyncio
import contextlib
import io
import json
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote
from uuid import uuid4

from fastapi.testclient import TestClient
from starlette.requests import Request

from inkflow.main import main
from inkflow.services.local_files import atomic_write
from inkflow.services.publish import preview_blog_target, write_final_blog_article
from inkflow.settings import SettingsStore
from inkflow.web.app import create_app
from inkflow.web.storage import HistoryStore, now
from inkflow.workflows import get_workflow


class IsolatedTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="inkflow-regression-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()


class CliTests(IsolatedTest):
    def run_cli(self, response='{"suggestions":[]}', action="", enabled=True):
        config = self.root / "config.toml"
        config.write_text("", encoding="utf-8")
        (self.root / "llm.toml").write_text(f"[llm]\nenabled = {str(enabled).lower()}\n", encoding="utf-8")
        text = self.root / "input.md"
        text.write_text("原文", encoding="utf-8")
        # 实际 main、注册表、图和终端 resume；只替代远端模型与终端输入。
        output = io.StringIO()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "isolated-test-key"}), \
             patch("sys.argv", ["inkflow", "--config", str(config), "--input", str(text), "--workflow", "full-check"]), \
             patch("inkflow.main.configure_utf8_stdio"), \
             patch("inkflow.workflows.full_check.call_llm", return_value=response), \
             patch("inkflow.services.terminal.read_user_input", return_value=action), \
             contextlib.redirect_stdout(output):
            main()
        return output.getvalue()

    def test_full_check_completes_without_article_state(self):
        self.assertIn("completed", self.run_cli())

    def test_full_check_with_suggestions(self):
        response = json.dumps({"suggestions": [{"kind": "expression", "title": "改进", "reason": "更清楚",
                              "original": "原文", "replacement": "新文"}]}, ensure_ascii=False)
        self.assertIn("completed", self.run_cli(response))

    def test_full_check_stop(self):
        self.assertIn("stopped", self.run_cli(action="s"))

    def test_full_check_failure_exits_one(self):
        with self.assertRaises(SystemExit) as error:
            self.run_cli(enabled=False)
        self.assertEqual(error.exception.code, 1)


class AtomicWriteTests(IsolatedTest):
    def test_failed_replace_preserves_content_and_cleans_temporary(self):
        path = self.root / "article.md"
        path.write_text("before", encoding="utf-8")
        with patch("inkflow.services.local_files.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                atomic_write(path, "after")
        self.assertEqual(path.read_text(encoding="utf-8"), "before")
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_private_permissions_failure_never_writes_secret(self):
        path = self.root / "secrets.json"
        path.write_text("old", encoding="utf-8")
        observed = []

        def denied(temporary):
            observed.append(temporary.read_bytes())
            raise OSError("permission denied")

        with patch("inkflow.services.local_files.restrict_access", side_effect=denied):
            with self.assertRaises(OSError):
                atomic_write(path, "secret", private=True)
        self.assertEqual(observed, [b""])
        self.assertEqual(path.read_text(encoding="utf-8"), "old")
        self.assertEqual(list(self.root.iterdir()), [path])

    @unittest.skipUnless(os.name == "posix", "需要真实 POSIX 权限语义")
    def test_new_articles_follow_umask_and_overwrites_preserve_mode(self):
        repo = self.root / "blog"
        repo.mkdir()
        for mask, expected_mode in [(0o022, 0o644), (0o027, 0o640), (0o077, 0o600)]:
            with self.subTest(umask=oct(mask)):
                previous = os.umask(mask)
                try:
                    path = write_final_blog_article("first\r\n", "title", repo, "content", slug_source=str(mask))
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), expected_mode)
                    path.chmod(0o664)
                    expected = preview_blog_target(repo, "content", str(mask))
                    write_final_blog_article("second\r\n", "title", repo, "content", slug_source=str(mask), expected=expected)
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o664)
                    self.assertEqual(path.read_bytes(), b"second\r\n")
                finally:
                    os.umask(previous)

    @unittest.skipUnless(os.name == "posix", "需要真实 POSIX 权限语义")
    def test_private_key_overwrite_remains_private(self):
        store = SettingsStore(self.root / "config.toml")
        previous = os.umask(0o022)
        try:
            store.save_key("key-one")
            store.key_path.chmod(0o644)
            store.save_key("key-two")
            self.assertEqual(stat.S_IMODE(store.data_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(store.key_path.stat().st_mode), 0o600)
        finally:
            os.umask(previous)


class StorageTests(IsolatedTest):
    @unittest.skipUnless(os.name == "posix", "需要真实 POSIX 权限语义")
    def test_database_and_live_sidecars_are_private_and_legacy_modes_repaired(self):
        directory = self.root / "data"
        previous = os.umask(0o022)
        try:
            store = HistoryStore(directory)
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(store.path.stat().st_mode), 0o600)
            with store.connection() as db:
                db.execute("INSERT INTO runs VALUES ('id','creation','test','completed','','','{}')")
                db.commit()
                sidecars = [Path(str(store.path) + suffix) for suffix in ("-wal", "-shm")]
                for path in sidecars:
                    self.assertTrue(path.exists())
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                    path.chmod(0o644)
                directory.chmod(0o755)
                store.path.chmod(0o644)
                HistoryStore(directory)
                for path in [store.path, *sidecars]:
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
                self.assertEqual(db.execute("SELECT name FROM runs").fetchone()[0], "test")
            journal = Path(str(store.path) + "-journal")
            journal.touch(mode=0o644)
            HistoryStore(directory)
            if journal.exists():
                self.assertEqual(stat.S_IMODE(journal.stat().st_mode), 0o600)
        finally:
            os.umask(previous)


@unittest.skipUnless(os.name == "nt", "需要真实 Windows ACL")
class WindowsPermissionsTests(IsolatedTest):
    def acl(self, path):
        command = ("[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
                   "$p=$env:INKFLOW_TEST_ACL_PATH; "
                   "if ([System.IO.Directory]::Exists($p)) {$acl=[System.IO.Directory]::GetAccessControl($p)} "
                   "else {$acl=[System.IO.File]::GetAccessControl($p)}; "
                   "$acl.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::Access)")
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                                env={**os.environ, "INKFLOW_TEST_ACL_PATH": str(path)},
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode("oem", errors="replace"))
        return result.stdout.decode("oem").strip().splitlines()

    def assert_private(self, path):
        sid, sddl = self.acl(path)
        dacl = sddl.split("D:", 1)[1].split("S:", 1)[0]
        self.assertTrue(dacl.startswith("P"), sddl)
        trustees = re.findall(r"\([^)]*;;;([^;)]+)\)", dacl)
        self.assertEqual(trustees, [sid], sddl)

    def test_shared_parent_does_not_expose_new_or_overwritten_secret(self):
        subprocess.run(["icacls.exe", str(self.root), "/grant", "*S-1-1-0:(OI)(CI)R"],
                       capture_output=True, check=True)
        self.assertIn(";;;WD)", self.acl(self.root)[1])
        store = SettingsStore(self.root / "config.toml")
        store.save_key("first-key")
        self.assert_private(store.key_path)
        self.assert_private(store.data_dir)
        # 模拟旧版密钥显式允许 Everyone，覆盖时必须移除。
        subprocess.run(["icacls.exe", str(store.key_path), "/grant", "*S-1-1-0:R"],
                       capture_output=True, check=True)
        store.save_key("second-key")
        self.assert_private(store.key_path)
        self.assertEqual(store.key(store.read()), "second-key")
        self.assertIn(";;;WD)", self.acl(self.root)[1])

    def test_history_wal_and_shm_inherit_private_directory_acl(self):
        store = HistoryStore(self.root / "data")
        self.assert_private(store.directory)
        self.assert_private(store.path)
        with store.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS private_test (text TEXT)")
            db.execute("INSERT INTO private_test VALUES ('article')")
            db.commit()
            for suffix in ("-wal", "-shm"):
                path = Path(str(store.path) + suffix)
                self.assertTrue(path.exists())
                # 继承当前用户 ACE（无其它账户授权）。
                sid, sddl = self.acl(path)
                self.assertEqual(re.findall(r"\([^)]*;;;([^;)]+)\)", sddl.split("D:", 1)[1]), [sid])


class WorkspaceTests(IsolatedTest):
    def setUp(self):
        super().setUp()
        self.a, self.b = self.root / "a", self.root / "b"
        self.a.mkdir()
        self.b.mkdir()
        self.app = create_app(self.root / "config.toml", workspace_root=self.a)
        self.client = self.enterContext(TestClient(self.app))
        self.manager = self.app.state.manager
        self.old = self.record(self.a)
        self.other = self.record(self.b)
        self.legacy = self.record(None)

    def record(self, workspace):
        run_id = uuid4().hex
        record = {"id": run_id, "creation_id": uuid4().hex, "name": "test", "status": "completed",
                  "created_at": now(), "updated_at": now(), "state": {"raw_text": "private article", "suggestions": []},
                  "workflow_id": "full-check", "workflow": get_workflow("full-check").describe(),
                  "event_cursor": 0, "executions": [], "pending": None,
                  "artifacts": [{"id": "review", "filename": "review.md"}]}
        if workspace:
            record["editor_source"] = {"workspace": str(workspace), "path": "article.md", "revision": "test"}
        self.manager.store.create(record)
        self.manager.store.save_event(record, "test", "private event")
        artifact = self.manager.store.directory / "artifacts" / run_id / "review.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("private artifact", encoding="utf-8")
        return run_id

    def switch(self, workspace):
        response = self.client.post("/api/editor/workspace", json={"path": str(workspace)})
        self.assertEqual(response.status_code, 200, response.text)

    def assert_readable(self, run_id, expected=200, headers=None):
        for suffix in ("", "/events", "/artifacts/review", "/artifacts/review?download=true"):
            with self.subTest(route=suffix):
                response = self.client.get(f"/api/runs/{run_id}{suffix}", headers=headers)
                self.assertEqual(response.status_code, expected, response.text)
                if expected != 200:
                    self.assertNotIn("private", response.text)

    def test_switch_blocks_old_ids_with_or_without_workspace_header(self):
        self.assert_readable(self.old)
        self.assert_readable(self.other, 404)
        self.switch(self.b)
        self.assert_readable(self.old, 404)
        self.assert_readable(self.old, 409, {"X-InkFlow-Workspace": quote(str(self.a), safe="")})
        self.assert_readable(self.old, 404, {"X-InkFlow-Workspace": quote(str(self.b), safe="")})
        self.assert_readable(self.other)
        self.assert_readable(self.legacy)
        self.assertEqual([r['id'] for r in self.client.get('/api/runs').json()], [self.other])
        self.assertEqual([r['id'] for r in self.client.get('/api/runs?unlinked=true').json()], [self.legacy])
        self.switch(self.a)
        self.assert_readable(self.old)

    def test_existing_sse_rechecks_before_next_event(self):
        self.manager.store.save_event(self.manager.store.get(self.old), "test", "another private event")
        async def check():
            endpoint = next(route.endpoint for route in self.app.routes if getattr(route, "path", "") == "/api/runs/{run_id}/events")
            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}
            request = Request({"type": "http", "app": self.app, "headers": []}, receive=receive)
            response = await endpoint(self.old, request, after=0)
            stream = response.body_iterator
            self.assertIn("retry:", await anext(stream))
            self.assertIn("private event", await anext(stream))
            # 在网络背压让出执行权时模拟另一个标签页切换工作区。
            with self.manager.lock:
                self.app.state.editor.root = self.b
            with self.assertRaises(StopAsyncIteration):
                await anext(stream)

        asyncio.run(check())

    def test_unrelated_run_mutations_are_rejected(self):
        self.switch(self.b)
        response = self.client.post(f"/api/runs/{self.old}/actions", json={"request_id": uuid4().hex, "action": "stop"})
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
