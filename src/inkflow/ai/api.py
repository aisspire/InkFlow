from typing import Literal
from fastapi import APIRouter, Request
from pydantic import Field
from inkflow.orchestration.definitions import Strict, ProfileRef


class Repair(Strict):
    run_id: str
    node_id: str
    attempt_id: str


class Proposal(Strict):
    name: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=12000)
    constraints: str = Field(default='', max_length=12000)
    sample: str = Field(min_length=1, max_length=24000)
    capabilities: list[Literal['human', 'model', 'candidate', 'local-blog', 'local-review', 'local-report']] = Field(max_length=6)
    profile_refs: list[ProfileRef] = Field(default_factory=list, max_length=20)
    repair_of: Repair | None = None


class Revision(Strict):
    revision: int = Field(ge=1)
    generation: int = Field(ge=0)


class Sample(Strict):
    name: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=24000)
    actions: list[dict] = Field(default_factory=list, max_length=100)
    contains: list[str] = Field(default_factory=list, max_length=20)
    excludes: list[str] = Field(default_factory=list, max_length=20)


class Verify(Strict):
    samples: list[Sample] = Field(min_length=1, max_length=2)
    allow_model: bool = False


class RepairRun(Strict):
    request_id: str = Field(min_length=1, max_length=100)
    document_id: str = Field(min_length=1, max_length=100)
    revision: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=1000000)
    source_snapshot_id: str | None = None


def router(workspace_context, workspace_run):
    api = APIRouter(prefix='/api/ai')

    def service(request):
        return request.app.state.manager.ai

    def authorized(request, identity):
        item = service(request).get(identity)
        if item['repair_of']:
            with workspace_context(request):
                workspace_run(item['repair_of']['run_id'], request)
        return item

    @api.get('/proposals')
    def listing(request: Request):
        result = []
        for item in service(request).list():
            try:
                if item['repair_of']:
                    run = request.app.state.manager.store.get(item['repair_of']['run_id'])
                    doc = run['editor_source']['document_id']
                    request.app.state.documents.resource(doc)
                    expected = request.headers.get('x-inkflow-document')
                    if expected and expected != doc:
                        continue
                    item['document_id'] = doc
                result.append(item)
            except KeyError:
                continue
        return result

    @api.post('/proposals')
    def start(body: Proposal, request: Request):
        if body.repair_of:
            with workspace_context(request):
                workspace_run(body.repair_of.run_id, request)
        return service(request).start(body.model_dump())

    @api.get('/proposals/{identity}')
    def detail(identity: str, request: Request):
        authorized(request, identity)
        return service(request).refresh(identity)

    @api.post('/proposals/{identity}/cancel')
    def cancel(identity: str, request: Request):
        authorized(request, identity)
        return service(request).cancel(identity)

    @api.post('/proposals/{identity}/apply')
    def apply(identity: str, body: Revision, request: Request):
        authorized(request, identity)
        return service(request).apply(identity, body.revision, body.generation)

    @api.post('/proposals/{identity}/verify')
    def verify(identity: str, body: Verify, request: Request):
        authorized(request, identity)
        return service(request).verify(identity, [s.model_dump() for s in body.samples], body.allow_model)

    @api.post('/proposals/{identity}/revise')
    def revise(identity: str, body: Revision, request: Request):
        authorized(request, identity)
        return service(request).revise(identity, body.revision, body.generation)

    @api.post('/proposals/{identity}/activate')
    def activate(identity: str, body: Revision, request: Request):
        authorized(request, identity)
        return service(request).activate(identity, body.revision, body.generation)

    @api.post('/proposals/{identity}/runs')
    def run(identity: str, body: RepairRun, request: Request):
        authorized(request, identity)
        return service(request).repair_run(identity, body.model_dump())

    return api
