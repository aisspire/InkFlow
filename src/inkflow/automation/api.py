from typing import Literal
from fastapi import APIRouter, Request
from pydantic import Field
from inkflow.orchestration.definitions import Strict


class Source(Strict):
    kind: Literal['http', 'rss', 'project']
    url: str = Field(default='', max_length=2000)
    network: Literal['public', 'private'] = 'public'
    credential_ref: str = Field(default='', pattern=r'^(|INKFLOW_SOURCE_[A-Z0-9_]+)$', max_length=100)
    workspace_id: str = Field(default='', max_length=100)
    files: list[str] = Field(default_factory=list, max_length=50)


class Create(Strict):
    name: str = Field(min_length=1, max_length=200)
    source: Source
    workflow_id: str = Field(min_length=1, max_length=100)
    version_policy: Literal['fixed', 'latest'] = 'fixed'
    target_mode: Literal['existing', 'new'] = 'existing'
    document_id: str = Field(default='', max_length=100)
    interval_seconds: int = Field(default=3600, ge=60, le=2592000)
    timezone: str = Field(default='Asia/Shanghai', max_length=100)
    enabled: bool = True


class Control(Strict):
    generation: int = Field(ge=1)
    action: Literal['pause', 'resume', 'check']


class Retry(Strict):
    revision: str = Field(max_length=100)


def router():
    api = APIRouter(prefix='/api/automations')

    @api.get('')
    def listing(request: Request):
        return request.app.state.automations.list()

    @api.post('')
    def create(body: Create, request: Request):
        service = request.app.state.automations
        # 与文档删除和候选调度采用相同锁顺序，避免校验后绑定已删除的草稿。
        with service.lock, service.manager.lock, service.documents.lock, service.store.transaction():
            return service.create(body.model_dump())

    @api.post('/template')
    def template(request: Request):
        return request.app.state.automations.template()

    @api.get('/{identity}')
    def detail(identity: str, request: Request):
        return request.app.state.automations.history(identity)

    @api.post('/{identity}/control')
    def control(identity: str, body: Control, request: Request):
        return request.app.state.automations.control(identity, body.generation, body.action)

    @api.get('/{identity}/snapshots/{snapshot_id}')
    def evidence(identity: str, snapshot_id: str, request: Request):
        return request.app.state.automations.evidence(identity, snapshot_id)

    @api.post('/{identity}/changes/{change_id}/retry')
    def retry(identity: str, change_id: str, body: Retry, request: Request):
        return request.app.state.automations.retry(identity, change_id, body.revision)

    return api
