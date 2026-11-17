"""FastAPI 同源 API 与构建后页面；默认只允许本机 Host 和同源写请求。"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from inkflow.settings import SettingsData, SettingsStore, resolve_directory
from inkflow.services.file_permissions import private_directory
from inkflow.web.runs import ConflictError, RunManager, TERMINAL
from inkflow.web.documents import DocumentService, WorkspaceService, WorkspaceEditor, checked_location
from inkflow.web.file_picker import NativeFilePicker, PickerUnavailable
from inkflow.web.storage import workflow_record
from inkflow.workflows import WORKFLOWS


class EditorOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(max_length=2000)
    revision: str = Field(max_length=100)
    text: str = Field(max_length=2000000)
    action: str = Field(max_length=40)
    stale_ids: list[str] = Field(default_factory=list, max_length=60)
    suggestion_id: str | None = None
    version: str | None = None
    feedback: str = Field(default="", max_length=4000)


class EditorSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str | None = Field(default=None, min_length=1, max_length=2000)
    document_id: str | None = Field(default=None, min_length=1, max_length=100)
    revision: str = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def require_identity(self):
        if not self.document_id and not self.path:
            raise ValueError('请提供文档 ID 或旧工作区文章路径。')
        return self


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=100)
    name: str = Field(default="", max_length=200)
    raw_text: str = Field(default="", max_length=1000000)
    source_name: str = Field(default="粘贴文本", max_length=2000)
    no_publish: bool = False
    rerun_of: str | None = Field(default=None, max_length=100)
    workflow_id: str | None = Field(default=None, min_length=1, max_length=100)
    editor_source: EditorSource | None = None
    feedback: str = Field(default='', max_length=4000)


class DocumentResultRequest(EditorSource):
    text: str = Field(max_length=2000000)
    result_id: str = Field(min_length=1, max_length=100)
    confirm_conflict: bool = False
    stale_ids: list[str] = Field(default_factory=list, max_length=60)


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=100)
    pending_id: str | None = Field(default=None, max_length=100)
    action: str = Field(max_length=40)
    payload: dict = Field(default_factory=dict)


class KeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: SecretStr


class PickerRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    purpose: Literal['article', 'workspace', 'blog_repository', 'blog_content', 'review', 'report']
    initial_path: str = Field(default='', max_length=2000)
    repo_path: str = Field(default='', max_length=2000)


class WorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(min_length=1, max_length=2000)


class DocumentOperation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str = Field(max_length=100)
    text: str = Field(max_length=2000000)
    action: Literal['edit', 'save', 'restore', 'reload']
    version: str | None = None
    stale_ids: list[str] = Field(default_factory=list, max_length=60)


class DeleteDraftRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str = Field(min_length=1, max_length=100)


class SaveAsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str = Field(max_length=100)
    text: str = Field(max_length=2000000)
    path: str = Field(min_length=1, max_length=2000)


class ProcessLock:
    """避免两个服务进程同时把对方的运行任务误标为中断。"""

    def __init__(self, directory: Path):
        private_directory(directory)
        self.stream = (directory / "server.lock").open("a+b")
        self.stream.seek(0)
        self.stream.write(b"0")
        self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise RuntimeError("此数据目录已有 InkFlow 服务运行，请使用单个服务进程。") from None

    def close(self):
        self.stream.close()


def create_app(config_path: Path | None = None, data_dir: Path | None = None, workspace_root: Path | None = None) -> FastAPI:
    settings = SettingsStore(config_path or Path("config.toml"), data_dir)
    picker = NativeFilePicker()

    def resource_context(request):
        if not request.scope.get('path', '').startswith(('/api/runs', '/api/ai/')):
            return None
        return request.headers.get('x-inkflow-document') or request.query_params.get('document_id')

    @contextmanager
    def workspace_context(request: Request):
        # 与创建/推进运行、工作区切换共享锁，校验后不能被其它标签页换根目录。
        with request.app.state.manager.lock:
            editor = request.app.state.editor
            document_id = resource_context(request)
            if document_id:
                request.app.state.documents.resource(document_id)
                yield editor
                return
            expected = request.headers.get('x-inkflow-workspace')
            if expected and unquote(expected) != str(editor.root):
                raise ConflictError('工作区已在其它页面切换，请刷新页面后重试；尚未保存的正文请先复制保留。')
            yield editor

    def workspace_run(run_id: str, request: Request) -> dict:
        # 调用者持有 workspace_context 的锁；不能仅信任客户端请求头。
        record = request.app.state.manager.store.get(run_id)
        source = record.get('editor_source')
        document_id = resource_context(request)
        if document_id:
            if not source or source.get('document_id') != document_id:
                raise KeyError(run_id)
            request.app.state.documents.resource(document_id)
            return record
        if source and source['workspace'] != str(request.app.state.editor.root):
            raise KeyError(run_id)
        return record

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        process_lock = ProcessLock(settings.data_dir)
        try:
            documents = DocumentService(settings)
            app.state.documents = documents
            app.state.workspaces = WorkspaceService(documents)
            default = app.state.workspaces.register(str((workspace_root or settings.root).absolute()))
            app.state.default_workspace = default['id']
            app.state.editor = WorkspaceEditor(documents, (workspace_root or settings.root).resolve())
            manager = RunManager(settings, source_resolver=documents.document_source, editor=documents)
            app.state.manager = manager
            from inkflow.automation.service import Automations
            automations = None
            try:
                automations = Automations(manager, documents)
                app.state.automations = automations
                yield
            finally:
                if automations is not None:
                    await asyncio.to_thread(automations.close)
                await asyncio.to_thread(manager.close)
        finally:
            process_lock.close()

    app = FastAPI(title="InkFlow 本地工作台", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        origin = request.headers.get("origin")
        # Vite 开发代理保留原 Host，所以开发和正式使用都按浏览器同源判断。
        if origin and (urlsplit(origin).netloc != request.headers.get("host")
                       or urlsplit(origin).scheme not in {"http", "https"}):
            return JSONResponse({"detail": "只允许同源页面访问本机 API。"}, status_code=403)
        if request.method in {"POST", "PUT", "DELETE"}:
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "不允许跨站操作。"}, status_code=403)
            if request.method != "DELETE" and request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"detail": "请使用 JSON 请求。"}, status_code=415)
        # 限制流式/无 Content-Length 请求体，避免仅检查声明长度被绕过。
        if request.method in {"POST", "PUT"}:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 8_000_000:
                    return JSONResponse({"detail": "请求过大，请缩短输入。"}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    from inkflow.workflow_assets.api import router
    from inkflow.orchestration.api import router as definition_router
    app.include_router(definition_router())
    from inkflow.ai.api import router as ai_router
    app.include_router(ai_router(workspace_context, workspace_run))
    from inkflow.automation.api import router as automation_router
    app.include_router(automation_router())
    from inkflow.workflow_assets.prompts import AssetValidationError
    app.include_router(router())

    @app.exception_handler(AssetValidationError)
    async def invalid_asset(request, error):
        return JSONResponse({'detail': str(error), 'errors': error.errors}, status_code=422)

    @app.get('/api/runs/{run_id}/attempts')
    def run_attempts(run_id: str, request: Request):
        with workspace_context(request):
            workspace_run(run_id, request)
            return request.app.state.manager.diagnostics.list('run', run_id)

    @app.get('/api/runs/{run_id}/attempts/{attempt_id}')
    def run_attempt(run_id: str, attempt_id: str, request: Request):
        with workspace_context(request):
            workspace_run(run_id, request)
            return request.app.state.manager.diagnostics.get('run', run_id, attempt_id)

    @app.exception_handler(KeyError)
    async def missing(request, error):
        return JSONResponse({"detail": "任务或产物不存在。"}, status_code=404)

    @app.exception_handler(ConflictError)
    async def conflict(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        # Pydantic 异常可能包含输入原值，单独处理以免回显密钥。
        from pydantic import ValidationError
        message = "配置格式不正确，请检查字段。" if isinstance(error, ValidationError) else str(error)
        return JSONResponse({"detail": message}, status_code=422)

    @app.exception_handler(RequestValidationError)
    async def validation(request, error):
        return JSONResponse({"detail": "请求字段不完整或格式不正确。"}, status_code=422)

    @app.exception_handler(OSError)
    async def filesystem_error(request, error):
        return JSONResponse({"detail": "本地文件操作失败，请检查目录是否存在、权限或文件占用。"}, status_code=400)

    @app.exception_handler(sqlite3.Error)
    async def history_error(request, error):
        return JSONResponse({"detail": "历史数据库暂时不可用，请检查磁盘空间、目录权限和文件占用。"}, status_code=503)

    @app.exception_handler(PickerUnavailable)
    async def picker_error(request, error):
        return JSONResponse({'detail': str(error)}, status_code=503)

    @app.get('/api/workspaces')
    def workspaces(request: Request):
        return {'default_id': request.app.state.default_workspace, 'legacy_root': str(request.app.state.editor.root),
                'workspaces': request.app.state.workspaces.list()}

    @app.post('/api/workspaces')
    def register_workspace(body: WorkspaceRequest, request: Request):
        return request.app.state.workspaces.register(body.path)

    @app.get('/api/workspaces/{workspace_id}/files')
    def workspace_files(workspace_id: str, request: Request):
        return request.app.state.workspaces.files(workspace_id)

    @app.post('/api/workspaces/{workspace_id}/open')
    def workspace_open(workspace_id: str, body: WorkspaceRequest, request: Request):
        editor = request.app.state.workspaces.editor(workspace_id)
        return request.app.state.documents.register(str(editor.path(body.path)))

    @app.get('/api/documents')
    def documents(request: Request):
        return request.app.state.documents.list_documents()

    @app.post('/api/documents/open')
    def open_document(body: WorkspaceRequest, request: Request):
        return request.app.state.documents.register(body.path)

    @app.post('/api/documents/pick-file')
    def pick_document(request: Request):
        selected = picker.choose(directory=False, initial=settings.root, title='打开 Markdown / MDX 文件')
        return request.app.state.documents.register(selected) if selected else None

    @app.post('/api/documents')
    def new_document(request: Request):
        return request.app.state.documents.new()

    @app.get('/api/documents/{document_id}')
    def get_document(document_id: str, request: Request):
        return request.app.state.documents.open(document_id)

    @app.get('/api/documents/{document_id}/versions')
    def document_versions(document_id: str, request: Request):
        return request.app.state.documents.versions(document_id)

    @app.delete('/api/documents/{document_id}')
    def delete_document(document_id: str, body: DeleteDraftRequest, request: Request):
        with request.app.state.manager.lock:
            request.app.state.documents.delete_draft(document_id, body.revision)
        return {'deleted': document_id}

    @app.post('/api/documents/{document_id}/operation')
    def document_operation(document_id: str, body: DocumentOperation, request: Request):
        with request.app.state.manager.lock:
            doc = request.app.state.documents.mutate(document_id, body.revision, body.text, body.action,
                                                     version=body.version, stale_ids=body.stale_ids)
            request.app.state.manager.sync_document(doc, body.stale_ids)
            return doc

    @app.post('/api/documents/{document_id}/save-as')
    def save_document_as(document_id: str, body: SaveAsRequest, request: Request):
        with request.app.state.manager.lock:
            try:
                return request.app.state.documents.save_as(document_id, body.revision, body.text, body.path)
            finally:
                request.app.state.manager.sync_document(request.app.state.documents.get(document_id))

    @app.post('/api/documents/{document_id}/relocate')
    def relocate_document(document_id: str, body: SaveAsRequest, request: Request):
        with request.app.state.manager.lock:
            try:
                return request.app.state.documents.relocate(document_id, body.revision, body.text, body.path)
            finally:
                request.app.state.manager.sync_document(request.app.state.documents.get(document_id))

    @app.post('/api/local-directory')
    def validate_local_directory(body: WorkspaceRequest):
        path = checked_location(body.path, directory=True)
        if not path.is_dir():
            raise ValueError('目录不存在，请选择已有目录；草稿仍保留。')
        return {'path': str(path)}

    @app.post('/api/local-picker')
    def local_picker(body: PickerRequest, request: Request):
        titles = {'article': '选择文章', 'workspace': '选择保存目录',
                  'blog_repository': '选择博客仓库', 'blog_content': '选择博客内容目录',
                  'review': '选择审阅稿目录', 'report': '选择报告目录'}
        with workspace_context(request) as editor:
            root = editor.root
        base = root if body.purpose in {'article', 'workspace'} else settings.root
        if body.purpose == 'blog_content':
            if not body.repo_path.strip():
                raise ValueError('请先填写或选择博客仓库路径。')
            base = resolve_directory(body.repo_path, settings.root)
            if not base.is_dir():
                raise ValueError('博客仓库目录不存在，请先选择有效的仓库。')
        initial = resolve_directory(body.initial_path, base) if body.initial_path else base
        # 等待用户选择时不占运行锁，SSE、草稿和其它文章操作继续可用。
        selected = picker.choose(directory=body.purpose != 'article', initial=initial, title=titles[body.purpose])
        with workspace_context(request) as editor:
            if editor.root != root:
                raise ConflictError('选择期间工作区已切换，请刷新页面后重新选择。')
            if not selected:
                return {'path': None}
            path = Path(selected)
            if body.purpose == 'article':
                if not path.is_absolute() or not path.is_relative_to(root):
                    raise ValueError('所选文章不在当前工作区，请先点击工作区旁的“切换工作区”选择所在文件夹。')
                return {'path': editor.canonical(path.relative_to(root).as_posix())}
            path = path.resolve()
            if not path.is_dir():
                raise ValueError('所选目录不存在，请重新选择。')
            if body.purpose == 'blog_content':
                if not path.is_relative_to(base):
                    raise ValueError('博客内容目录必须位于所选博客仓库内。')
                return {'path': path.relative_to(base).as_posix()}
            return {'path': str(path)}

    @app.post('/api/editor/workspace')
    def switch_workspace(body: WorkspaceRequest, request: Request):
        with workspace_context(request) as editor:
            target = resolve_directory(body.path, settings.root)
            if not target.is_dir():
                raise ValueError('工作区目录不存在，请选择已有文件夹。')
            if target == editor.root:
                return editor.files()
            if request.app.state.manager.live:
                raise ConflictError('还有运行中或等待处理的任务，请先结束或停止所有任务，再切换工作区。')
            with editor.lock:
                previous = editor.root
                editor.root = target
                try:
                    return editor.files()
                except Exception:
                    editor.root = previous
                    raise

    @app.get("/api/editor/files")
    def editor_files(request: Request):
        with workspace_context(request) as editor:
            return editor.files()

    @app.get("/api/editor/document")
    def editor_document(request: Request, path: str):
        with workspace_context(request) as editor:
            return editor.open(path)

    @app.get("/api/editor/versions")
    def editor_versions(request: Request, path: str):
        with workspace_context(request) as editor:
            return editor.versions(path)

    @app.post("/api/editor/operation")
    def editor_operation(body: EditorOperation, request: Request):
        with workspace_context(request):
            return apply_editor_operation(body, request)

    def apply_editor_operation(body: EditorOperation, request: Request):
        editor = request.app.state.editor
        if body.action == 'check':
            from uuid import uuid4
            return request.app.state.manager.create(name=body.path, raw_text=body.text, source_name=body.path,
                no_publish=True, request_id=uuid4().hex, workflow_id='full-check', feedback=body.feedback,
                editor_source={'document_id': editor.resolve(body.path), 'workspace': str(editor.root),
                               'path': editor.canonical(body.path), 'revision': body.revision})
        if body.action in {'accept', 'ignore', 'reject'}:
            raise ConflictError('请通过当前全文检查运行处理建议。旧历史仅供查看。')
        manager = request.app.state.manager
        with manager.lock:
            doc = editor.mutate(body.path, body.revision, body.text, body.action, body.suggestion_id, body.version, body.stale_ids)
            manager.sync_document(doc, body.stale_ids)
            return doc

    @app.get("/api/workflow")
    def workflow(request: Request):
        return request.app.state.manager.workflow

    @app.get("/api/workflows")
    def workflows(request: Request, include_drafts: bool = False):
        return request.app.state.manager.catalog.definitions.list(include_drafts)

    @app.get("/api/workflows/{workflow_id}")
    def workflow_detail(workflow_id: str, request: Request):
        try:
            return request.app.state.manager.catalog.active(workflow_id)['payload']['graph']
        except KeyError:
            raise HTTPException(404, "工作流不存在。") from None

    @app.post("/api/runs", status_code=201)
    def create_run(body: RunRequest, request: Request):
        with workspace_context(request) as editor:
            data = body.model_dump()
            source = data.get('editor_source')
            data['source_signature'] = body.editor_source.model_dump(exclude_none=True) if body.editor_source else None
            if source and not source.get('document_id'):
                source.update(document_id=editor.resolve(source['path']), workspace=str(editor.root),
                              path=editor.canonical(source['path']))
            if source and resource_context(request) and source['document_id'] != resource_context(request):
                raise ConflictError('运行输入与文档上下文不一致。')
            return request.app.state.manager.create(**data)

    @app.get("/api/runs")
    def runs(request: Request, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
             path: str | None = None, workspace: bool = False, unlinked: bool = False, document_id: str | None = None):
        with workspace_context(request) as editor:
            if document_id:
                request.app.state.documents.resource(document_id)
                return request.app.state.manager.store.list(limit, offset, document_id=document_id)
            return request.app.state.manager.store.list(limit, offset, None if unlinked else str(editor.root),
                                                         editor.canonical(path) if path else None, unlinked)

    @app.get('/api/document-runs')
    def document_runs(request: Request):
        manager = request.app.state.manager
        with manager.lock, manager.store.connection() as db:
            rows = db.execute("SELECT payload FROM runs WHERE json_extract(payload,'$.editor_source.document_id') IS NOT NULL ORDER BY created_at DESC,rowid DESC").fetchall()
            result = {}
            for row in rows:
                record = workflow_record(json.loads(row['payload']))
                document_id = record['editor_source']['document_id']
                if document_id in result and record['status'] in TERMINAL:
                    continue
                items = record.get('annotations', record['state'].get('suggestions', []))
                result[document_id] = {k: record[k] for k in ('id', 'name', 'workflow_id', 'status', 'editor_source', 'created_at', 'updated_at')}
                result[document_id].update(workflow_name=record['workflow']['name'], pending_count=sum(s['status'] in {'pending', 'stale'} for s in items))
            return result

    @app.get('/api/article-runs')
    def article_runs(request: Request):
        with workspace_context(request):
            return list_article_runs(request)

    def list_article_runs(request: Request):
        manager = request.app.state.manager
        root = str(request.app.state.editor.root)
        # 按文章返回最新与活跃状态，包含未选中文章；不受历史分页大小影响。
        with manager.store.connection() as db:
            rows = db.execute("SELECT payload FROM runs WHERE json_extract(payload,'$.editor_source.workspace')=? ORDER BY created_at DESC,rowid DESC", (root,)).fetchall()
        result = {}
        for row in rows:
            record = json.loads(row['payload'])
            path = record['editor_source']['path']
            if path in result and record['status'] in TERMINAL:
                continue
            annotations = record.get('annotations', record['state'].get('suggestions', []))
            result[path] = {'id': record['id'], 'name': record['name'], 'workflow_id': record['workflow_id'],
                'workflow_name': record['workflow']['name'], 'status': record['status'],
                'pending_count': sum(s['status'] in {'pending', 'stale'} for s in annotations),
                'editor_source': record['editor_source'], 'created_at': record['created_at'], 'updated_at': record['updated_at']}
        return result

    @app.get("/api/runs/{run_id}")
    def run(run_id: str, request: Request):
        with workspace_context(request):
            workspace_run(run_id, request)
            return request.app.state.manager.detail(run_id)

    def document_result(run_id: str, body: DocumentResultRequest, request: Request, apply: bool):
        manager = request.app.state.manager
        with workspace_context(request):
            record = workspace_run(run_id, request)
            result = manager.document_result(record)
            if result is None or result["id"] != body.result_id:
                raise ConflictError("工作流结果已更新或已撤回，请重新对比。")
            editor = request.app.state.documents
            target_id = body.document_id or request.app.state.editor.resolve(body.path)
            editor.resource(target_id)
            source = record.get("editor_base") or record.get("editor_source")
            if apply:
                if not source or record['status'] in TERMINAL:
                    raise ConflictError('历史运行仅供查看，请基于当前文章启动新运行。')
                with editor.lock, manager.store.transaction():
                    adopted = editor.apply_result(target_id, body.revision, body.text, source, result,
                                                   run_id, record["workflow_id"], body.confirm_conflict, body.stale_ids)
                    import hashlib
                    record['editor_base'] = {**source, 'revision': adopted['revision'],
                        'text_hash': hashlib.sha256(adopted['text'].encode()).hexdigest()}
                    manager.store.save_event(record, 'working_copy_updated', '已明确采用运行稿件，工作副本与确认基线已同步。')
                    return adopted
            return editor.preview_result(target_id, body.revision, body.text, source, result)

    @app.post("/api/runs/{run_id}/document-preview")
    def document_preview(run_id: str, body: DocumentResultRequest, request: Request):
        return document_result(run_id, body, request, False)

    @app.post("/api/runs/{run_id}/document-apply")
    def document_apply(run_id: str, body: DocumentResultRequest, request: Request):
        return document_result(run_id, body, request, True)

    @app.post("/api/runs/{run_id}/actions")
    def action(run_id: str, body: ActionRequest, request: Request):
        with workspace_context(request):
            workspace_run(run_id, request)
            data = body.model_dump()
            target = data['payload'].get('editor')
            legacy_document_id = None
            if isinstance(target, dict) and not target.get('document_id') and target.get('path'):
                legacy_document_id = request.app.state.editor.resolve(target['path'])
            return request.app.state.manager.action(run_id, data, legacy_document_id=legacy_document_id)

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request, after: int = Query(0, ge=0)):
        manager: RunManager = request.app.state.manager
        with workspace_context(request):
            workspace_run(run_id, request)
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise HTTPException(400, "事件位置必须为整数。") from None

        async def stream():
            nonlocal cursor
            ticks = 0
            yield "retry: 1500\n\n"
            while not await request.is_disconnected():
                try:
                    with workspace_context(request):
                        record = workspace_run(run_id, request)
                        batch = manager.store.events(run_id, cursor)
                except (KeyError, ConflictError):
                    return
                for event in batch:
                    # yield 后可能等待客户端接收；下一条正文发送前再次检查归属。
                    try:
                        with workspace_context(request):
                            workspace_run(run_id, request)
                    except (KeyError, ConflictError):
                        return
                    cursor = event["seq"]
                    yield f"id: {cursor}\nevent: run_event\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                try:
                    with workspace_context(request):
                        record = workspace_run(run_id, request)
                except (KeyError, ConflictError):
                    return
                if record["status"] in TERMINAL and cursor >= record["event_cursor"]:
                    yield "event: end\ndata: {}\n\n"
                    return
                if batch:
                    continue
                ticks += 1
                if ticks % 30 == 0:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

    @app.get("/api/runs/{run_id}/artifacts/{artifact_id}")
    def artifact(run_id: str, artifact_id: str, request: Request, download: bool = False):
        with workspace_context(request):
            workspace_run(run_id, request)
            path = request.app.state.manager.artifact(run_id, artifact_id)
        return FileResponse(path, media_type="text/plain; charset=utf-8", filename=path.name,
                            content_disposition_type="attachment" if download else "inline")

    @app.get("/api/settings")
    def read_settings():
        return settings.public()

    @app.put("/api/settings")
    def save_settings(body: SettingsData):
        return settings.save(body)

    @app.put("/api/settings/api-key")
    def save_key(body: KeyRequest):
        settings.save_key(body.api_key.get_secret_value())
        return {"api_key_configured": bool(settings.key(settings.read())), "local_key_saved": True}

    @app.delete("/api/settings/api-key")
    def clear_key():
        settings.clear_key()
        return {"api_key_configured": bool(settings.key(settings.read())), "local_key_saved": False}

    dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    # 允许先启动服务再构建前端；没有构建时首页仍返回清楚的 503 提示。
    app.mount("/assets", StaticFiles(directory=dist / "assets", check_dir=False), name="assets")

    @app.get("/")
    def index():
        if not (dist / "index.html").is_file():
            return JSONResponse({"detail": "前端尚未构建，请在 frontend 目录运行 npm ci 和 npm run build。"}, status_code=503)
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    return app
