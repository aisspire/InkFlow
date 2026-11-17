"""按文章管理运行：锁保护人工操作，受控线程执行模型，SQLite 保存事实。

每个任务独立 graph/checkpointer/runtime，密钥在任务结束时随 runtime 释放。
正式运行使用版本化持久检查点；未知外部效果先核验。
"""

from __future__ import annotations

import hashlib
import json
import threading
import sqlite3
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4
from inkflow.web.durable import DurableStore, RecoveryHold, ServicePause, PROTOCOL, IMPLEMENTATION, fingerprint, fault

from langgraph.types import Command

from inkflow.runtime import WorkflowRuntime
from inkflow.workflows import DEFAULT_WORKFLOW_ID, WORKFLOWS, get_workflow
from inkflow.services.local_files import atomic_write
from inkflow.settings import SettingsStore
from inkflow.web.storage import HistoryStore, encode, now

TERMINAL = {"completed", "failed", "stopped", "interrupted"}
ARTIFACTS = {"review_path": ("review", "审阅稿", ".md"), "blog_path": ("blog", "博客文件副本", ".mdx"),
             "report_path": ("report", "流程报告", ".md"), "report_jsonl_path": ("audit", "审计记录", ".jsonl")}


class ConflictError(ValueError):
    pass


class RunManager:
    def __init__(self, settings: SettingsStore, source_resolver=None, editor=None):
        self.settings = settings
        self.source_resolver = source_resolver
        self.editor = editor
        self.store = HistoryStore(settings.data_dir)
        self.durable = DurableStore(self.store)
        self.durable.document_lock = editor.lock if editor else threading.RLock()
        try:
            self._initialize(settings)
        except BaseException:
            self.durable.process_lock.close()
            raise

    def _initialize(self, settings):
        self.store.recover()
        from inkflow.workflow_assets.catalog import WorkflowCatalog
        from inkflow.workflow_assets.diagnostics import DiagnosticStore
        self.catalog = WorkflowCatalog(self.store, settings)
        self.diagnostics = DiagnosticStore(self.store, recover=True)
        from inkflow.workflow_assets.testing import ModelTests
        self.tests = ModelTests(self.catalog, self.diagnostics)
        from inkflow.orchestration.testing import DefinitionTests
        self.flow_tests = DefinitionTests(self.catalog, self.diagnostics)
        self.lock = threading.RLock()
        from inkflow.ai.service import AIService
        self.ai = AIService(self)
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="inkflow")
        self.scheduled = set()
        self.live: dict[str, dict] = {}
        self.workflows = {key: definition.describe() for key, definition in WORKFLOWS.items()}
        self.workflow = self.workflows[DEFAULT_WORKFLOW_ID]  # 兼容旧 /api/workflow。
        self.closing = threading.Event()
        self._recover()
        self.scheduler = threading.Thread(target=self._schedule, name="inkflow-recovery", daemon=True)
        self.scheduler.start()

    def _attach(self, record):
        package = record['version_package']
        if record.get('recovery_protocol') != PROTOCOL or record.get('implementation_fingerprint') != fingerprint():
            raise RecoveryHold('执行协议或核心实现已变化。请恢复匹配的代码及依赖，或停止此运行并基于当前稿件新建；不会猜测恢复位置。', 'recovery_blocked')
        if package.get('schema_version') == 2:
            from inkflow.orchestration.execution import workflow_definition
            definition = workflow_definition(package)
        else:
            definition = get_workflow(record['workflow_id'])
        _, llm, key = self.catalog.runtime_config({'payload': package}, record['state']['config'].get('publish', {}).get('no_publish', False))
        runtime = WorkflowRuntime(llm, key, package=package, version_id=record['workflow_version'], owner_id=record['id'],
            diagnostic_sink=self.diagnostics.save, document_id=record['editor_source']['document_id'],
            durable=self.durable, checkpointer=self.durable.saver(record['id']))
        runtime.node_keys = self.catalog.instance_keys(package)
        work = self.durable.work(record['id'])
        if work and work['stop']:
            runtime.stop.set()
        self.live[record['id']] = {'runtime': runtime, 'graph': definition.build_graph(runtime), 'definition': definition}

    def _recover(self):
        with self.store.connection() as db:
            rows = db.execute("SELECT id FROM runs WHERE status NOT IN ('completed','failed','stopped','interrupted')").fetchall()
        for row in rows:
            record = self.store.get(row['id'])
            try:
                if not record.get('recovery_protocol') or not self.durable.work(record['id']):
                    raise RecoveryHold('此运行没有完整恢复协议及工作记录。请停止后基于当前稿件新建。', 'recovery_blocked')
                self._attach(record)
                if record['status'] in {'verification_required', 'recovery_blocked'}:
                    continue
                if record['status'] == 'waiting':
                    cfg = {'configurable': {'thread_id': record['id']}}
                    snapshot = self.live[record['id']]['graph'].get_state(cfg)
                    interrupts = [item for task in snapshot.tasks for item in task.interrupts]
                    pending = record.get('pending') or {}
                    if not interrupts or pending.get('id') != hashlib.sha256((record['id'] + interrupts[0].id).encode()).hexdigest() or pending.get('node') != interrupts[0].value.get('node'):
                        raise RecoveryHold('人工待办与检查点不一致，请停止并核验历史。', 'recovery_blocked')
                    self.store.save_event(record, 'recovered', '原人工待办已恢复，请核对当前文章后继续。')
                else:
                    record['status'] = 'recovering'
                    self.store.save_event(record, 'recovering', '正在从固定版本和输入恢复。')
            except (RecoveryHold, Exception) as error:
                record.update(status='recovery_blocked', recovery_message=str(error))
                self.store.save_event(record, 'recovery_blocked', record['recovery_message'])

    def _schedule(self):
        while not self.closing.wait(.25):
            with self.lock:
                self.durable.heartbeat()
                for run_id in list(self.live):
                    work = self.durable.work(run_id)
                    if work and work['state'] == 'queued' and self.store.get(run_id)['status'] in {'running', 'recovering', 'stopping'}:
                        self._submit(run_id)

    def _submit(self, run_id):
        # The persistent queue owns backlog; do not accumulate duplicate in-memory futures.
        if run_id in self.scheduled or len(self.scheduled) >= 4:
            return
        self.scheduled.add(run_id)
        future = self.pool.submit(self._drive, run_id)
        def settled(_):
            with self.lock:
                self.scheduled.discard(run_id)
        future.add_done_callback(settled)

    def close(self) -> None:
        if self.closing.is_set():
            return
        self.closing.set()
        self.durable.pausing.set()
        self.scheduler.join()
        self.ai.close()
        self.flow_tests.close()
        self.tests.close()
        # Shutdown is not a user stop. Finish current drive or leave durable work for restart.
        self.pool.shutdown(wait=True, cancel_futures=False)
        self.live.clear()
        self.durable.process_lock.close()

    def detail(self, run_id: str) -> dict:
        with self.lock:
            record = self.store.get(run_id)
            record["effects"] = [{key: effect[key] for key in ('id', 'state', 'path', 'sha256', 'node')}
                                 for effect in self.durable.effects(run_id)]
            record["events"] = self.store.events(run_id, max(0, record["event_cursor"] - 200))
            record.pop("creation_id", None)
            record.pop("creation_hash", None)
            record["document_result"] = self.document_result(record)
            record['annotations'] = self.annotations(record)
            record['annotation_snapshot'] = record['state'].get('checked_text', record['state']['raw_text'])
            return record

    def annotations(self, record):
        source = record.get('editor_source')
        if 'annotations' in record or record['workflow_id'] == 'full-check':
            items = deepcopy(record.get('annotations', record['state'].get('suggestions', [])))
            text = record.get('annotation_text', record['state']['raw_text'])
        else:
            text = record['state'].get('checked_text', record['state']['raw_text'])
            items = []
            from inkflow.workflows.full_check import units
            for finding in record['state'].get('redaction_findings', []):
                excerpt = finding['original_excerpt']
                lines = text.splitlines(keepends=True)
                a = sum(map(len, lines[:max(0, finding['line_start'] - 1)]))
                b = sum(map(len, lines[:finding['line_end']]))
                region = text[a:b]
                found = a + region.find(excerpt) if excerpt and region.count(excerpt) == 1 else text.find(excerpt) if excerpt and text.count(excerpt) == 1 else -1
                start = units(text[:found]) if found >= 0 else None
                items.append({**finding, 'kind': 'redaction', 'title': finding['issue'], 'original': excerpt,
                              'replacement': finding['suggestion'], 'version': hashlib.sha256(text.encode()).hexdigest(),
                              'start': start, 'end': start + units(excerpt) if start is not None else None,
                              'snapshot_start': start, 'snapshot_end': start + units(excerpt) if start is not None else None,
                              'status': 'reviewed' if record['status'] in TERMINAL else 'pending'})
        for item in items:
            item.update(article=source, run_id=record['id'])
            item.setdefault('step', 'locate' if record['workflow_id'] == 'full-check' else 'redaction_plan')
            if record.get('version_package', {}).get('schema_version') == 2:
                item['step'] = item.get('node_id') or record.get('current_node')
                item['node_id'] = item['step']
            item.setdefault('execution_id', next((e['id'] for e in reversed(record['executions']) if e['node'] == item['step']), None))
        if record['status'] not in TERMINAL and self.editor and source and source.get('document_id'):
            try:
                doc = self.editor.get(source['document_id'])
                projection = {'text': text, 'suggestions': items}
                self.editor.sync(projection, doc['text'])
            except (KeyError, ValueError):
                for item in items:
                    if item['status'] == 'pending':
                        item['status'] = 'stale'
        return items

    def sync_document(self, doc, stale_ids=()):
        """草稿持久化时同步当前运行的标注；失效事实不因撤销回原文消失。"""
        with self.lock:
            for run_id in self.live:
                record = self.store.get(run_id)
                source = record.get('editor_source')
                if not source or source.get('document_id') != doc.get('document_id'):
                    continue
                if 'annotations' not in record:
                    continue  # 模型尚未返回；完成时会从输入快照向当前稿映射。
                projected = {'text': record.get('annotation_text', record['state']['raw_text']),
                             'suggestions': deepcopy(record['annotations'])}
                self.editor.sync(projected, doc['text'])
                for item in projected['suggestions']:
                    if item['id'] in stale_ids and item['status'] == 'pending':
                        item['status'] = 'stale'
                if record['annotations'] != projected['suggestions'] or projected['text'] != record.get('annotation_text'):
                    record.update(annotations=projected['suggestions'], annotation_text=projected['text'])
                    self.store.save_event(record, 'annotations_mapped', '标注已映射到当前工作副本，修改命中项待复查。')

    @staticmethod
    def document_result(record: dict) -> dict | None:
        if record.get('version_package', {}).get('schema_version') == 2:
            return record['state'].get('document_candidate')
        definition = WORKFLOWS.get(record["workflow_id"])
        return definition.document_result(record["state"]) if definition else None

    def create(self, *, name: str, raw_text: str, source_name: str, no_publish: bool,
               request_id: str, rerun_of: str | None = None, workflow_id: str | None = None,
               editor_source: dict | None = None, feedback: str = '', source_signature: dict | None = None,
               workflow_version: str | None = None, automation: dict | None = None, repair_of: dict | None = None) -> dict:
        signature = [name, raw_text, source_name, no_publish, rerun_of, workflow_id, feedback]
        if editor_source is not None:
            signature.append(source_signature if source_signature is not None else editor_source)
        if workflow_version or automation:
            signature.extend([workflow_version, automation])
        if repair_of:
            signature.append(repair_of)
        request_hash = hashlib.sha256(encode(signature).encode()).hexdigest()
        with self.lock:
            if fingerprint() != IMPLEMENTATION:
                raise ConflictError('执行源码已变化，请受控重启服务后再创建运行。')
            previous = self.store.by_creation_id(request_id)
            if previous:
                if previous["creation_hash"] != request_hash:
                    raise ConflictError("相同请求 ID 不能用于不同任务。")
                return self.detail(previous["id"])
            if rerun_of:
                previous = self.store.get(rerun_of)
                if workflow_id is not None and workflow_id != previous["workflow_id"]:
                    raise ValueError("重新运行必须使用原记录所属工作流。")
                workflow_id = previous["workflow_id"]
                name = name or previous["name"]
                no_publish = previous['state']['config'].get('publish', {}).get('no_publish', False)
                if not editor_source or not previous.get('editor_source'):
                    raise ValueError('旧记录没有文章归属，请打开文章后启动新运行。')
                if editor_source.get('document_id') != previous['editor_source'].get('document_id'):
                    raise ConflictError('重新检查必须属于原文档。')
            if editor_source is not None:
                if self.source_resolver is None:
                    raise ValueError("当前入口不支持关联编辑文档。")
                resolved = self.source_resolver(editor_source["document_id"], editor_source["revision"], raw_text)
                # 旧接口保留展示路径；授权与并发始终使用资源 ID。
                if editor_source.get('workspace'):
                    resolved.update(workspace=editor_source['workspace'], path=editor_source['path'])
                editor_source = resolved
            else:
                raise ValueError('请先打开或新建文章，新运行必须绑定文档。CLI 仍支持独立输入。')
            for live_id in self.live:
                live_source = self.store.get(live_id).get('editor_source')
                if live_source and live_source.get('document_id') == editor_source['document_id']:
                    raise ConflictError('该文章已有活跃运行，请返回待办或先结束、停止当前运行。')
            version = self.catalog.version(workflow_id, workflow_version) if workflow_version else self.catalog.active(workflow_id or DEFAULT_WORKFLOW_ID)
            if not version['id']:
                raise ValueError('流程尚未启用，请先校验并启用已保存的草稿。')
            if version['payload'].get('schema_version') == 2:
                from inkflow.orchestration.execution import workflow_definition
                definition = workflow_definition(version['payload'])
            else:
                definition = get_workflow(workflow_id or DEFAULT_WORKFLOW_ID)
            if not raw_text.strip() or len(raw_text) > 1000000:
                raise ValueError("请输入正文，最多 100 万字符。")
            self.catalog.validate(version['payload'])
            config, llm, key = self.catalog.runtime_config(version, no_publish)
            if version['payload'].get('schema_version') == 2:
                node_keys = self.catalog.instance_keys(version['payload'])
                for identity, node in version['payload']['compiled']['nodes'].items():
                    if node['type']['uses_model'] and (not node['model'].get('enabled') or not node_keys.get(identity)):
                        raise ValueError('流程包含模型节点，请启用该实例的模型并配置凭据。')
                if any(secret and (secret in raw_text or secret in feedback) for secret in node_keys.values()):
                    raise ValueError('输入包含节点模型凭据，请移除后再操作。')
            if definition.id == 'redaction-only':
                config['redaction_only'] = True
                if not llm.enabled or not key:
                    raise ValueError('仅脱敏需要启用模型并配置密钥；尚未进行风险检查。')
            if definition.id == 'full-check':
                if len(raw_text) > 24000:
                    raise ValueError('全文检查上限为 24,000 个 Unicode 字符；未发送或截断文章。')
                if not llm.enabled or not key:
                    raise ValueError('全文检查需要启用模型并配置密钥，请在设置中完成配置。')
            if key and (key in raw_text or key in feedback):
                raise ValueError('输入包含当前模型密钥，请移除后再操作。')
            run_id = uuid4().hex
            runtime = WorkflowRuntime(llm, key, package=version['payload'], version_id=version['id'],
                                      owner_id=run_id, diagnostic_sink=self.diagnostics.save, document_id=editor_source['document_id'],
                                      durable=self.durable, checkpointer=self.durable.saver(run_id))
            if version['payload'].get('schema_version') == 2:
                runtime.node_keys = node_keys
            graph = definition.build_graph(runtime)
            state = runtime.scrub({"raw_text": raw_text, "source_path": source_name, "config": config,
                                   "run_id": run_id, "warnings": [], "audit_events": [], 'editor_bound': True,
                                   'input_revision': editor_source['revision'], 'feedback': feedback})
            if automation:
                state['automation'] = runtime.scrub(automation)
            record = {"recovery_protocol": PROTOCOL, "implementation_fingerprint": IMPLEMENTATION, "id": run_id, "creation_id": request_id, "creation_hash": request_hash,
                      "name": runtime.scrub(name.strip() or source_name or "未命名文章"), "status": "running",
                      "created_at": now(), "updated_at": now(), "state": state, "pending": None,
                      "current_node": None, "executions": [], "traversed_edges": [], "artifacts": [],
                      "event_cursor": 0, "rerun_of": rerun_of,
                      "workflow_id": definition.id, "workflow": definition.describe(graph),
                      "workflow_version": version["id"], "version_package": version["payload"],
                      "editor_source": editor_source, 'editor_base': dict(editor_source)}
            if automation:
                record['automation_id'] = automation['automation_id']
                record['source_snapshot_id'] = automation['snapshot_id']
            if repair_of:
                record['repair_of'] = deepcopy(repair_of)
            try:
                with self.store.transaction():
                    self.store.create(record)
                    self.durable.enqueue(run_id, {"initial": state})
            except sqlite3.IntegrityError:
                raise ConflictError('该文章已有活跃运行，或请求 ID 已使用。请刷新当前文章。') from None
            self.store.save_event(record, "created", "任务已创建，配置已固定。")
            self.live[run_id] = {"graph": graph, "runtime": runtime, "definition": definition}
            self._submit(run_id)
            return self.detail(run_id)

    def action(self, run_id: str, request: dict, *, legacy_document_id: str | None = None) -> dict:
        # JSON 字段顺序不改变请求含义，重试时也应被视为同一次操作。
        request_hash = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        with self.lock, self.editor.lock, self.store.transaction():
            record = self.store.get(run_id)
            repeated = self.store.action(run_id, request["request_id"])
            if repeated:
                if repeated["hash"] != request_hash:
                    raise ConflictError("相同请求 ID 已用于其它操作。")
                return repeated["result"]
            if legacy_document_id:
                request = deepcopy(request)
                request['payload']['editor']['document_id'] = legacy_document_id
            if record['status'] in {'verification_required', 'recovery_blocked'}:
                if request['pending_id'] != (record.get('pending') or {}).get('id'):
                    raise ConflictError('恢复操作已过期，请刷新。')
                action = request['action']
                if action == 'stop':
                    record.update(status='stopped', pending=None)
                    if record['executions']:
                        record['executions'][-1].update(status='stopped', finished_at=now())
                    self.store.save_event(record, 'stopped', '已停止恢复；已有文件保留，请人工核对。')
                    self.live.pop(run_id, None)
                    with self.store.connection() as db:
                        db.execute("UPDATE execution_work SET state='done',stop=1 WHERE run_id=?", (run_id,))
                elif action == 'reconcile' and record['status'] == 'verification_required':
                    self._attach(record)
                    record.update(status='recovering', recovery_message=None)
                    self.store.save_event(record, 'recovering', '重新核验已记录的输出；不会更换目标或重写。')
                    with self.store.connection() as db:
                        db.execute("UPDATE execution_work SET state='queued' WHERE run_id=?", (run_id,))
                else:
                    raise ConflictError('当前只能停止运行；请按恢复说明处理。')
                result = {'accepted': True, 'run_id': run_id, 'event_cursor': record['event_cursor']}
                self.store.remember_action(run_id, request['request_id'], request_hash, result)
                return result
            if record["status"] in TERMINAL or run_id not in self.live:
                raise ConflictError("该任务已经结束或中断，请重新运行。")
            action = request["action"]
            pending = record["pending"]
            entry = self.live[run_id]
            resume = None
            if action == "stop":
                entry["runtime"].stop.set()
                with self.store.connection() as db:
                    db.execute("UPDATE execution_work SET stop=1 WHERE run_id=?", (run_id,))
                record["status"] = "stopping"
                if pending:
                    resume = {"action": "stop"}
                    record["pending"] = None
            else:
                if not pending or pending["id"] != request.get("pending_id") or record["status"] != "waiting":
                    raise ConflictError("审核操作已过期或已被提交，请刷新当前任务。")
                payload = entry['runtime'].scrub(request['payload'])
                target_text = request['payload'].get('editor', {}).get('text', '') if isinstance(request['payload'].get('editor', {}), dict) else ''
                if isinstance(target_text, str) and entry['runtime'].api_key and entry['runtime'].api_key in target_text:
                    raise ValueError('文章包含运行中的模型密钥，请移除后再操作。')
                if pending['kind'] == 'suggestion_review' and action in {'accept', 'ignore', 'reject'}:
                    self._suggestion_action(record, action, payload)
                    self.store.save_event(record, 'suggestion_updated', '建议处理已记录。')
                    result = {'accepted': True, 'run_id': run_id, 'event_cursor': record['event_cursor']}
                    self.store.remember_action(run_id, request['request_id'], request_hash, result)
                    return result
                entry["definition"].validate_action(pending, action, payload)
                if pending['kind'] == 'final_review' and action == 'save' and 'editor' in payload:
                    self._submit_article_edit(record, payload)
                if pending['kind'] == 'article_metadata' and action == 'approve_article' and record.get('version_package', {}).get('schema_version') == 2:
                    if not self.editor or not record.get('editor_source'):
                        raise ConflictError('此文章审阅节点需要已登记的文档工作副本。')
                    from inkflow.orchestration.execution import render_article
                    article = payload['article']
                    document = render_article(article)
                    result = {'id': hashlib.sha256((pending['node_id'] + document).encode()).hexdigest(),
                              'text': document, 'kind': 'article', 'label': '已确认文章与元数据', 'node_id': pending['node_id']}
                    target = payload.get('editor', {})
                    self._validate_editor_target(target)
                    if target['document_id'] != record['editor_source']['document_id']:
                        raise ConflictError('请在原文章中重新审阅并确认。')
                    with self.editor.lock:
                        current = self.editor.get(target['document_id'])
                        if current['revision'] != target['revision'] or current['text'] != target['text']:
                            raise ConflictError('文章版本或正文已变化，当前草稿已保留，请重新打开文章并审阅。')
                        adopted = self.editor.apply_result(target['document_id'], target['revision'], target['text'],
                            record['editor_base'], result, record['id'], record['workflow_id'], payload.get('confirm_conflict') is True)
                    record['editor_base'] = {'document_id': record['editor_source']['document_id'],
                        'workspace': record['editor_source'].get('workspace', ''), 'path': record['editor_source']['path'],
                        'revision': adopted['revision'], 'text_hash': hashlib.sha256(adopted['text'].encode()).hexdigest()}
                    self.store.save_event(record, 'working_copy_updated', '已确认文章候选成为工作副本；历史已保留，原文件未写入。')
                if pending['kind'] == 'diff_review' and action == 'accept':
                    self._accept_redaction(record, payload)
                resume = {"action": action, "payload": entry["runtime"].scrub(request["payload"])}
                if pending['kind'] == 'suggestion_review' and action == 'finish_check' and record.get('version_package', {}).get('schema_version') == 2:
                    # Fix the accepted working-copy snapshot now, never read the current page later.
                    doc = self.editor.get(record['editor_source']['document_id'])
                    resume['payload']['confirmed_text'] = doc['text']
                record.update(status="running", pending=None)
            self.store.save_event(record, "action_accepted", "已请求停止，将在安全节点边界结束。" if action == "stop" else "人工操作已接收。")
            result = {"accepted": True, "run_id": run_id, "event_cursor": record["event_cursor"]}
            self.store.remember_action(run_id, request["request_id"], request_hash, result)
            if resume is not None:
                self.durable.enqueue(run_id, {"resume": resume, "checkpoint": entry["graph"].get_state({"configurable": {"thread_id": run_id}}).config["configurable"]["checkpoint_id"]})
            # Action, working copy, event, idempotency receipt and queue commit together.
            return result

    def _suggestion_action(self, record, action, payload):
        if record['pending']['kind'] != 'suggestion_review' or not self.editor:
            raise ConflictError('本轮建议不可操作。')
        source = record['editor_source']
        target = payload.get('editor', {})
        self._validate_editor_target(target)
        feedback = payload.get('feedback', '')
        if not isinstance(feedback, str) or len(feedback) > 4000:
            raise ValueError('反馈必须为文本，最多 4000 字符。')
        if target.get('document_id') != source.get('document_id'):
            raise ConflictError('建议与当前文章不一致。')
        with self.editor.lock:
            value = self.editor.mutate(source['document_id'], target.get('revision'), target.get('text', ''), action,
                                       payload.get('suggestion_id'), stale_ids=target.get('stale_ids', []), suggestions=self.annotations(record))
            record['annotations'] = value['suggestions']
            record['annotation_text'] = value['text']
            if payload.get('feedback'):
                record.setdefault('feedback_history', []).append({'suggestion_id': payload.get('suggestion_id'), 'feedback': payload['feedback']})

    def _submit_article_edit(self, record, payload):
        """Authorize this explicit editor submission without adopting or writing the source."""
        source = record.get('editor_source')
        target = payload['editor']
        self._validate_editor_target(target)
        if not self.editor or not source or target['document_id'] != source['document_id']:
            raise ConflictError('请在原文章中提交编辑稿。')
        current = self.editor.get(target['document_id'])
        if current['revision'] != target['revision'] or current['text'] != target['text']:
            raise ConflictError('文章版本或正文已变化，请重新核对后提交编辑稿。')
        imported = current.get('workflow_import') or {}
        if imported.get('run_id') != record['id'] or imported.get('kind') != 'article':
            raise ConflictError('请先显式对比并采用本次运行的文章候选。')
        if payload.get('document') != current['text']:
            raise ConflictError('提交的编辑稿与当前工作副本不一致。')
        record['editor_base'] = {**record['editor_base'], 'revision': current['revision'],
                                'text_hash': hashlib.sha256(current['text'].encode()).hexdigest()}
        self.store.save_event(record, 'working_copy_submitted', '已显式提交当前编辑快照；源文件未写入，输出仍需确认。')

    def _accept_redaction(self, record, payload):
        if not self.editor:
            raise ConflictError('当前入口无法确认文章稿件。')
        source = record['editor_source']
        target = payload.get('editor', {})
        self._validate_editor_target(target)
        if target.get('document_id') != source.get('document_id'):
            raise ConflictError('请在原文章中确认脱敏稿。')
        candidate = record['pending']['candidate_text']
        result = {'id': hashlib.sha256(candidate.encode()).hexdigest(), 'text': candidate,
                  'kind': 'redaction', 'label': '已确认脱敏稿'}
        value = self.editor.apply_result(source['document_id'], target.get('revision'), target.get('text', ''),
                                         record['editor_base'], result, record['id'], record['workflow_id'],
                                         payload.get('confirm_conflict') is True, target.get('stale_ids', []))
        record['editor_base'] = {'document_id': source['document_id'], 'workspace': source['workspace'], 'path': source['path'],
                                 'revision': value['revision'], 'text_hash': hashlib.sha256(value['text'].encode()).hexdigest()}
        self.store.save_event(record, 'working_copy_updated', '已确认脱敏稿成为文章工作副本，原文件未写入。')

    @staticmethod
    def _validate_editor_target(target):
        if not isinstance(target, dict) or any(not isinstance(target.get(key), str) for key in ('document_id', 'revision', 'text')):
            raise ValueError('请提交当前文章、版本和正文快照。')
        stale = target.get('stale_ids', [])
        if not isinstance(stale, list) or len(stale) > 60 or any(not isinstance(item, str) for item in stale):
            raise ValueError('待复查标识格式不正确。')

    def _archive(self, record: dict, update: dict) -> None:
        directory = self.store.directory / "artifacts" / record["id"]
        for key, (artifact_id, label, suffix) in ARTIFACTS.items():
            if not update.get(key):
                continue
            effect = next((e for e in reversed(self.durable.effects(record['id'])) if e['path'] == str(Path(update[key]).absolute()) and e['state'] == 'succeeded'), None)
            node_id = effect['node'] if effect else record.get('current_node')
            if record.get('version_package', {}).get('schema_version') == 2:
                artifact_id = node_id + '-' + artifact_id
            # 原始路径来自已完成的节点，不接受浏览器传入的下载路径。
            content = effect['content'] if effect else Path(update[key]).read_text(encoding="utf-8")
            path = directory / f"{artifact_id}{suffix}"
            atomic_write(path, content)
            artifact = {"id": artifact_id, "label": label, "filename": path.name,
                        'node_id': node_id,
                        "source_path": update[key], "revision": record["state"].get("revision", 0)}
            record["artifacts"] = [x for x in record["artifacts"] if x["id"] != artifact_id] + [artifact]

    def artifact(self, run_id: str, artifact_id: str) -> Path:
        record = self.store.get(run_id)
        artifact = next((x for x in record["artifacts"] if x["id"] == artifact_id), None)
        if not artifact:
            raise KeyError(artifact_id)
        root = (self.store.directory / "artifacts").resolve()
        candidate = root / run_id / artifact["filename"]
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(root) or not candidate.is_file():
            raise KeyError(artifact_id)
        return candidate

    def _custom(self, record: dict, event: dict) -> None:
        node = event["node"]
        if event["type"] == "node_started":
            executions = record["executions"]
            latest = executions[-1] if executions else None
            if latest and latest["node"] == node and latest["status"] == "waiting":
                latest["status"] = "running"  # resume 重入同一次等待，不算新的执行轮次。
            else:
                previous = record.get("current_node")
                if previous:
                    record["traversed_edges"].append({"source": previous, "target": node, "time": now()})
                executions.append({"id": uuid4().hex, "node": node, 'attempt_id': event.get('attempt_id'),
                                   "iteration": 1 + sum(x["node"] == node for x in executions),
                                   "status": "running", "started_at": now(), "input": event.get("input", {}), "output": {}})
            record["current_node"] = node
            self.store.save_event(record, "node_started", f"开始：{self._node_label(record, node)}")
        else:
            record["executions"][-1]["status"] = "failed" if event["type"] == "node_failed" else "skipped"
            self.store.save_event(record, event["type"], event["summary"])

    def _drive(self, run_id: str) -> None:
        with self.lock:
            if run_id not in self.live or not self.durable.claim(run_id):
                return
            entry = self.live[run_id]
        graph, runtime = entry["graph"], entry["runtime"]
        try:
            work = self.durable.work(run_id)
            command = json.loads(work['command']) if work['command'] else None
            value = None
            cfg = {'configurable': {'thread_id': run_id}}
            snapshot = entry['graph'].get_state(cfg)
            if command and 'initial' in command and not snapshot.values:
                value = command['initial']
            elif command and 'resume' in command:
                # Resume only the original interrupted task; never submit an accepted action to a later wait.
                pending_id = command.get('checkpoint')
                current_id = (snapshot.config or {}).get('configurable', {}).get('checkpoint_id')
                if pending_id == current_id:
                    value = Command(resume=command['resume'])
            fault('action_committed')
            config = {"configurable": {"thread_id": run_id}, "recursion_limit": 10000}
            while True:
                for mode, chunk in graph.stream(value, config, stream_mode=["updates", "custom"], durability="sync"):
                    with self.lock:
                        record = self.store.get(run_id)
                        if mode == "custom":
                            self._custom(record, runtime.scrub(chunk))
                            continue
                        for node, update in chunk.items():
                            if node == "__interrupt__" or update is None:
                                continue
                            record["state"].update(runtime.scrub(update))
                            if 'suggestions' in update:
                                record['annotations'] = deepcopy(update['suggestions'])
                                record['annotation_text'] = record['state'].get('checked_text', record['state']['raw_text'])
                                record['annotations'] = self.annotations(record)
                                source = record.get('editor_source')
                                if self.editor and source:
                                    record['annotation_text'] = self.editor.get(source['document_id'])['text']
                            if 'redaction_findings' in update:
                                record.pop('annotations', None)
                                record['annotations'] = self.annotations(record)
                                # annotations() 已投影到当前工作副本；检测快照仍保存在 checked_text。
                                source = record.get('editor_source')
                                record['annotation_text'] = self.editor.get(source['document_id'])['text'] if self.editor and source else record['state']['checked_text']
                            execution = record["executions"][-1]
                            if execution["status"] not in {"failed", "skipped"}:
                                execution["status"] = "completed"
                            execution["finished_at"] = now()
                            execution["output"] = {key: val for key, val in update.items() if key not in {"audit_events", "next_node"}}
                            # 先保存节点事实，归档失败也不会丢掉已写入博客的路径。
                            status_label = {"completed": "已完成", "failed": "失败", "skipped": "已跳过"}[execution["status"]]
                            self.store.save_event(record, "node_updated", f"{self._node_label(record, node)}：{status_label}")
                            self._archive(record, update)
                            if any(key in update for key in ARTIFACTS):
                                self.store.save_event(record, "artifacts_updated", "任务产物已归档。")
                snapshot = graph.get_state(config)
                interrupts = [item for task in snapshot.tasks for item in task.interrupts]
                with self.lock:
                    record = self.store.get(run_id)
                    record["state"].update(runtime.scrub(dict(snapshot.values)))
                    self._archive(record, record['state'])
                    if interrupts:
                        if runtime.stop.is_set():
                            value = Command(resume={"action": "stop"})
                            continue
                        payload = runtime.scrub(interrupts[0].value)
                        record.update(status="waiting", pending={**payload, "id": hashlib.sha256((run_id + interrupts[0].id).encode()).hexdigest(),
                            'run_id': run_id, 'workflow_id': record['workflow_id'], 'version_id': record.get('workflow_version'),
                            'document_id': (record.get('editor_source') or {}).get('document_id'),
                            'node_id': payload.get('node'), 'attempt_id': payload.get('attempt_id') or runtime.last_attempts.get((payload.get('node'), 'node'), {}).get('id')})
                        record["executions"][-1]["status"] = "waiting"
                        self.store.save_event(record, "waiting", "等待人工操作。")
                        self.durable.release(run_id, "waiting")
                        return
                    state = record["state"]
                    record.update(status="failed" if state.get("failure") else "stopped" if state.get("stopped") or runtime.stop.is_set() else "completed", pending=None)
                    self.store.save_event(record, "finished", {"failed": "任务失败，已保留记录。", "stopped": "任务已停止，已写入的文件保留。",
                                                              "completed": "任务已完成。"}[record["status"]])
                    self.durable.release(run_id, "done")
                    self.live.pop(run_id, None)
                    return
        except ServicePause:
            with self.lock, self.store.transaction():
                record = self.store.get(run_id)
                record.update(status='recovering')
                self.store.save_event(record, 'service_paused', '服务正在关闭，下次启动从持久检查点继续。')
                with self.store.connection() as db:
                    db.execute("UPDATE execution_work SET state='queued',owner=NULL,lease=NULL WHERE run_id=?", (run_id,))
        except RecoveryHold as error:
            with self.lock:
                record = self.store.get(run_id)
                record.update(status=error.status, recovery_message=str(error))
                if record['executions']:
                    record['executions'][-1]['status'] = error.status
                self.store.save_event(record, error.status, str(error))
                # Keep command until reconciliation; interrupted action may still need applying.
                with self.store.connection() as db:
                    db.execute("UPDATE execution_work SET state='blocked',owner=NULL,lease=NULL WHERE run_id=?", (run_id,))
        except Exception as error:
            with self.lock:
                record = self.store.get(run_id)
                message = runtime.scrub(str(error)) if isinstance(error, OSError) else f"运行异常（{type(error).__name__}），记录已保留。"
                record.update(status="failed", pending=None)
                record["state"]["failure"] = message
                if record["executions"] and record["executions"][-1]["status"] in {"running", "waiting"}:
                    record["executions"][-1].update(status="failed", finished_at=now())
                self.store.save_event(record, "failed", message)
                self.durable.release(run_id, "done")
                self.live.pop(run_id, None)

    @staticmethod
    def _node_label(record: dict, node: str) -> str:
        return next((item["label"] for item in record["workflow"]["nodes"] if item["id"] == node), node)
