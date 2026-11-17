"""流程资产与固定样例 API；同源本机边界由应用统一执行。"""
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    package: dict


class EnableRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    generation: int = Field(ge=0)


class SourceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version_id: str | None = None


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version_id: str
    generation: int = Field(ge=1)


class PreviewSource(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    base_version: str | None = Field(default=None, max_length=100)
    draft_revision: int | None = None
    saved: bool = False


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    node_id: str
    prompt_id: str | None = None
    package: dict
    variables: dict
    source: PreviewSource = Field(default_factory=PreviewSource)


def router():
    routes = APIRouter(prefix='/api/workflow-assets')

    def manager(request):
        return request.app.state.manager

    @routes.get('/{workflow_id}')
    def detail(workflow_id: str, request: Request):
        return manager(request).catalog.view(workflow_id)

    @routes.get('/{workflow_id}/versions/{version_id}')
    def version(workflow_id: str, version_id: str, request: Request):
        return manager(request).catalog.version(workflow_id, version_id)

    @routes.post('/{workflow_id}/draft')
    def create(workflow_id: str, body: SourceRequest, request: Request):
        return manager(request).catalog.create_draft(workflow_id, body.version_id)

    @routes.put('/{workflow_id}/draft')
    def save(workflow_id: str, body: DraftRequest, request: Request):
        return manager(request).catalog.save(workflow_id, body.revision, body.package)

    @routes.post('/{workflow_id}/activate')
    def activate(workflow_id: str, body: EnableRequest, request: Request):
        return manager(request).catalog.activate(workflow_id, body.revision, body.generation)

    @routes.post('/{workflow_id}/rollback')
    def rollback(workflow_id: str, body: RollbackRequest, request: Request):
        return manager(request).catalog.rollback(workflow_id, body.version_id, body.generation)

    @routes.post('/{workflow_id}/preview')
    def preview(workflow_id: str, body: PreviewRequest, request: Request):
        result = manager(request).tests.prepare(workflow_id, body.node_id, body.package, body.variables, body.source.model_dump(), body.prompt_id)
        return {key: value for key, value in result.items() if key != 'package'}

    @routes.post('/{workflow_id}/tests')
    def start_test(workflow_id: str, body: PreviewRequest, request: Request):
        service = manager(request).tests
        return service.start(service.prepare(workflow_id, body.node_id, body.package, body.variables, body.source.model_dump(), body.prompt_id))

    @routes.get('/{workflow_id}/tests')
    def list_tests(workflow_id: str, request: Request):
        manager(request).catalog.active(workflow_id)
        return manager(request).tests.list(workflow_id)

    @routes.get('/{workflow_id}/tests/{test_id}')
    def test_detail(workflow_id: str, test_id: str, request: Request):
        return manager(request).tests.get(workflow_id, test_id)

    @routes.post('/{workflow_id}/tests/{test_id}/stop')
    def stop_test(workflow_id: str, test_id: str, request: Request):
        return manager(request).tests.stop(workflow_id, test_id)

    @routes.get('/{workflow_id}/tests/{test_id}/attempts')
    def attempts(workflow_id: str, test_id: str, request: Request):
        manager(request).tests.get(workflow_id, test_id)
        return manager(request).diagnostics.list('test', test_id)

    @routes.get('/{workflow_id}/tests/{test_id}/attempts/{attempt_id}')
    def attempt(workflow_id: str, test_id: str, attempt_id: str, request: Request):
        manager(request).tests.get(workflow_id, test_id)
        return manager(request).diagnostics.get('test', test_id, attempt_id)

    @routes.get('/{workflow_id}/prompts/{prompt_id}/revisions')
    def revisions(workflow_id: str, prompt_id: str, request: Request):
        import json
        catalog = manager(request).catalog
        if prompt_id not in catalog.active(workflow_id)['payload']['prompts']:
            raise KeyError(prompt_id)
        with catalog.store.connection() as db:
            return [json.loads(row['payload']) for row in db.execute('SELECT payload FROM prompt_revisions WHERE prompt_id=? ORDER BY rowid DESC', (prompt_id,))]

    @routes.get('/{workflow_id}/defaults')
    def defaults(workflow_id: str, request: Request):
        from inkflow.workflow_assets.catalog import initial_package
        from inkflow.workflows import get_workflow
        config, _, _ = manager(request).settings.snapshot()
        package = initial_package(get_workflow(workflow_id), config)
        return {key: package[key] for key in ('model', 'node_config')}

    return routes
