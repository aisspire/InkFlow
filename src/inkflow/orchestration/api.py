from fastapi import APIRouter, Request
from pydantic import Field
from inkflow.orchestration.definitions import Strict, TYPES, new_instance


class CreateDefinition(Strict):
    name: str = Field(min_length=1, max_length=200)
    source_id: str | None = None
    version_id: str | None = None


class DefinitionRequest(Strict):
    package: dict


class FlowTestRequest(DefinitionRequest):
    text: str = Field(min_length=1, max_length=1000000)
    allow_model: bool = False
    actions: list[dict] = Field(default_factory=list, max_length=100)


class ProfileRequest(Strict):
    payload: dict
    generation: int = Field(default=0, ge=0)


def router():
    api = APIRouter(prefix='/api')
    def manager(request):
        return request.app.state.manager

    @api.get('/node-types')
    def types():
        return [kind.describe() for kind in TYPES.values()]

    @api.get('/node-types/{type_id}/instance')
    def instance(type_id: str, request: Request):
        config, _, _ = manager(request).settings.snapshot()
        return new_instance(type_id, 'node', config['llm'])

    @api.post('/workflow-definitions')
    def create(body: CreateDefinition, request: Request):
        return manager(request).catalog.definitions.create(body.name, body.source_id, body.version_id)

    @api.post('/workflow-assets/{workflow_id}/plan')
    def plan(workflow_id: str, body: DefinitionRequest, request: Request):
        catalog = manager(request).catalog
        if body.package.get('workflow_id') != workflow_id:
            raise ValueError('定义不属于此流程。')
        catalog.validate(body.package)
        return catalog.definitions.compile(body.package)['compiled']

    @api.get('/publish-profiles')
    def profiles(request: Request):
        return manager(request).catalog.definitions.profiles.list()

    @api.post('/publish-profiles')
    def create_profile(body: ProfileRequest, request: Request):
        return manager(request).catalog.definitions.profiles.save(body.payload)

    @api.put('/publish-profiles/{profile_id}')
    def save_profile(profile_id: str, body: ProfileRequest, request: Request):
        return manager(request).catalog.definitions.profiles.save(body.payload, profile_id, body.generation)

    @api.get('/publish-profiles/{profile_id}/revisions/{revision}')
    def profile_revision(profile_id: str, revision: str, request: Request):
        return manager(request).catalog.definitions.profiles.revision(profile_id, revision)

    @api.post('/workflow-assets/{workflow_id}/definition-tests')
    def test(workflow_id: str, body: FlowTestRequest, request: Request):
        return manager(request).flow_tests.start(workflow_id, body.package, body.text, body.allow_model, body.actions)

    @api.get('/workflow-assets/{workflow_id}/definition-tests')
    def tests(workflow_id: str, request: Request):
        manager(request).catalog.active(workflow_id)
        return manager(request).flow_tests.list(workflow_id)

    @api.get('/workflow-assets/{workflow_id}/definition-tests/{test_id}')
    def detail(workflow_id: str, test_id: str, request: Request):
        return manager(request).flow_tests.get(workflow_id, test_id)

    @api.post('/workflow-assets/{workflow_id}/definition-tests/{test_id}/actions')
    def action(workflow_id: str, test_id: str, body: dict, request: Request):
        from inkflow.web.app import ActionRequest
        return manager(request).flow_tests.action(workflow_id, test_id, ActionRequest.model_validate(body).model_dump())

    @api.post('/workflow-assets/{workflow_id}/definition-tests/{test_id}/stop')
    def stop(workflow_id: str, test_id: str, request: Request):
        return manager(request).flow_tests.stop(workflow_id, test_id)

    @api.get('/workflow-assets/{workflow_id}/definition-tests/{test_id}/attempts')
    def attempts(workflow_id: str, test_id: str, request: Request):
        manager(request).flow_tests.get(workflow_id, test_id)
        return manager(request).diagnostics.list('test', test_id)

    @api.get('/workflow-assets/{workflow_id}/definition-tests/{test_id}/attempts/{attempt_id}')
    def attempt(workflow_id: str, test_id: str, attempt_id: str, request: Request):
        manager(request).flow_tests.get(workflow_id, test_id)
        return manager(request).diagnostics.get('test', test_id, attempt_id)

    @api.get('/run-center')
    def runs(request: Request, status: str = '', workflow_id: str = '', document_id: str = '', workspace_id: str = '', automation_id: str = '', offset: int = 0):
        service = manager(request)
        documents = request.app.state.documents
        with service.lock, service.store.connection() as db:
            clauses, params = ["json_extract(payload,'$.editor_source.document_id') IS NOT NULL"], []
            for field, value in [('status', status), ('workflow_id', workflow_id), ('editor_source.document_id', document_id), ('automation_id', automation_id)]:
                if value:
                    clauses.append(f"json_extract(payload,'$.{field}')=?"); params.append(value)
            rows = db.execute('SELECT payload FROM runs WHERE ' + ' AND '.join(clauses) + ' ORDER BY created_at DESC,rowid DESC', params).fetchall()
            result = []
            import json
            for row in rows:
                record = json.loads(row['payload'])
                try:
                    resource = documents.resource(record['editor_source']['document_id'])
                except (KeyError, ValueError, OSError):
                    continue
                if workspace_id:
                    # Explicit membership, not the workspace currently displayed by a page.
                    try:
                        workspace = request.app.state.workspaces.editor(workspace_id)
                    except KeyError:
                        raise KeyError('工作区未登记。') from None
                    from pathlib import Path
                    location = resource.get('location')
                    if not location or not Path(location).is_relative_to(workspace.root):
                        continue
                result.append({k: record[k] for k in ('id', 'name', 'status', 'created_at', 'updated_at', 'workflow_id', 'editor_source')})
                result[-1].update(workflow_name=record['workflow']['name'], pending=record.get('pending'))
                result[-1]['automation_id'] = record.get('automation_id')
            return result[max(0, offset):max(0, offset) + 50]
    return api
