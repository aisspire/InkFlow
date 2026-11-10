"""按文章管理运行：锁保护人工操作，受控线程执行模型，SQLite 保存事实。

每个任务独立 graph/checkpointer/runtime，密钥在任务结束时随 runtime 释放。
服务重启不尝试恢复内存 checkpoint，只提供使用原输入的新任务。
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
        self.store.recover()
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="inkflow")
        self.live: dict[str, dict] = {}
        self.workflows = {key: definition.describe() for key, definition in WORKFLOWS.items()}
        self.workflow = self.workflows[DEFAULT_WORKFLOW_ID]  # 兼容旧 /api/workflow。

    def close(self) -> None:
        with self.lock:
            for entry in self.live.values():
                entry["runtime"].stop.set()
        self.pool.shutdown(wait=True, cancel_futures=False)
        self.live.clear()

    def detail(self, run_id: str) -> dict:
        with self.lock:
            record = self.store.get(run_id)
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
            item.setdefault('execution_id', next((e['id'] for e in reversed(record['executions']) if e['node'] == item['step']), None))
        if record['status'] not in TERMINAL and self.editor and source and source['workspace'] == str(self.editor.root):
            try:
                doc = self.editor.get(source['path'])
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
                if not source or source['workspace'] != doc['workspace'] or source['path'] != doc['path']:
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
        definition = WORKFLOWS.get(record["workflow_id"])
        return definition.document_result(record["state"]) if definition else None

    def create(self, *, name: str, raw_text: str, source_name: str, no_publish: bool,
               request_id: str, rerun_of: str | None = None, workflow_id: str | None = None,
               editor_source: dict | None = None, feedback: str = '') -> dict:
        signature = [name, raw_text, source_name, no_publish, rerun_of, workflow_id, feedback]
        if editor_source is not None:
            signature.append(editor_source)
        request_hash = hashlib.sha256(encode(signature).encode()).hexdigest()
        with self.lock:
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
                if (editor_source['path'] != previous['editor_source']['path'] or
                    self.editor and previous['editor_source']['workspace'] != str(self.editor.root)):
                    raise ConflictError('重新检查必须属于原文章与工作区。')
            if editor_source is not None:
                if self.source_resolver is None:
                    raise ValueError("当前入口不支持关联编辑文档。")
                editor_source = self.source_resolver(editor_source["path"], editor_source["revision"], raw_text)
            else:
                raise ValueError('请先打开工作区文章，新运行必须绑定文章。CLI 仍支持独立输入。')
            for live_id in self.live:
                live_source = self.store.get(live_id).get('editor_source')
                if live_source and all(live_source[k] == editor_source[k] for k in ('workspace', 'path')):
                    raise ConflictError('该文章已有活跃运行，请返回待办或先结束、停止当前运行。')
            definition = get_workflow(workflow_id or DEFAULT_WORKFLOW_ID)
            if not raw_text.strip() or len(raw_text) > 1000000:
                raise ValueError("请输入正文，最多 100 万字符。")
            config, llm, key = self.settings.snapshot(no_publish=no_publish)
            if definition.id == 'full-check':
                if len(raw_text) > 24000:
                    raise ValueError('全文检查上限为 24,000 个 Unicode 字符；未发送或截断文章。')
                if not llm.enabled or not key:
                    raise ValueError('全文检查需要启用模型并配置密钥，请在设置中完成配置。')
            if key and (key in raw_text or key in feedback):
                raise ValueError('输入包含当前模型密钥，请移除后再操作。')
            runtime = WorkflowRuntime(llm, key)
            graph = definition.build_graph(runtime)
            run_id = uuid4().hex
            state = runtime.scrub({"raw_text": raw_text, "source_path": source_name, "config": config,
                                   "run_id": run_id, "warnings": [], "audit_events": [], 'editor_bound': True,
                                   'input_revision': editor_source['revision'], 'feedback': feedback})
            record = {"id": run_id, "creation_id": request_id, "creation_hash": request_hash,
                      "name": runtime.scrub(name.strip() or source_name or "未命名文章"), "status": "running",
                      "created_at": now(), "updated_at": now(), "state": state, "pending": None,
                      "current_node": None, "executions": [], "traversed_edges": [], "artifacts": [],
                      "event_cursor": 0, "rerun_of": rerun_of,
                      "workflow_id": definition.id, "workflow": definition.describe(graph),
                      "editor_source": editor_source, 'editor_base': dict(editor_source)}
            try:
                self.store.create(record)
            except sqlite3.IntegrityError:
                raise ConflictError('该文章已有活跃运行，或请求 ID 已使用。请刷新当前文章。') from None
            self.store.save_event(record, "created", "任务已创建，配置已固定。")
            self.live[run_id] = {"graph": graph, "runtime": runtime, "definition": definition}
            self.pool.submit(self._drive, run_id, state)
            return self.detail(run_id)

    def action(self, run_id: str, request: dict) -> dict:
        # JSON 字段顺序不改变请求含义，重试时也应被视为同一次操作。
        request_hash = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        with self.lock:
            record = self.store.get(run_id)
            repeated = self.store.action(run_id, request["request_id"])
            if repeated:
                if repeated["hash"] != request_hash:
                    raise ConflictError("相同请求 ID 已用于其它操作。")
                return repeated["result"]
            if record["status"] in TERMINAL or run_id not in self.live:
                raise ConflictError("该任务已经结束或中断，请重新运行。")
            action = request["action"]
            pending = record["pending"]
            entry = self.live[run_id]
            resume = None
            if action == "stop":
                entry["runtime"].stop.set()
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
                if record['workflow_id'] == 'full-check' and action in {'accept', 'ignore', 'reject'}:
                    self._suggestion_action(record, action, payload)
                    self.store.save_event(record, 'suggestion_updated', '建议处理已记录。')
                    result = {'accepted': True, 'run_id': run_id, 'event_cursor': record['event_cursor']}
                    self.store.remember_action(run_id, request['request_id'], request_hash, result)
                    return result
                entry["definition"].validate_action(pending, action, payload)
                if pending['kind'] == 'diff_review' and action == 'accept':
                    self._accept_redaction(record, payload)
                resume = {"action": action, "payload": entry["runtime"].scrub(request["payload"])}
                record.update(status="running", pending=None)
            self.store.save_event(record, "action_accepted", "已请求停止，将在安全节点边界结束。" if action == "stop" else "人工操作已接收。")
            result = {"accepted": True, "run_id": run_id, "event_cursor": record["event_cursor"]}
            self.store.remember_action(run_id, request["request_id"], request_hash, result)
            if resume is not None:
                self.pool.submit(self._drive, run_id, Command(resume=resume))
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
        if source['workspace'] != str(self.editor.root) or target.get('path') != source['path']:
            raise ConflictError('建议与当前文章不一致。')
        with self.editor.lock:
            value = self.editor.mutate(source['path'], target.get('revision'), target.get('text', ''), action,
                                       payload.get('suggestion_id'), stale_ids=target.get('stale_ids', []), suggestions=self.annotations(record))
            record['annotations'] = value['suggestions']
            record['annotation_text'] = value['text']
            if payload.get('feedback'):
                record.setdefault('feedback_history', []).append({'suggestion_id': payload.get('suggestion_id'), 'feedback': payload['feedback']})

    def _accept_redaction(self, record, payload):
        if not self.editor:
            raise ConflictError('当前入口无法确认文章稿件。')
        source = record['editor_source']
        target = payload.get('editor', {})
        self._validate_editor_target(target)
        if target.get('path') != source['path'] or source['workspace'] != str(self.editor.root):
            raise ConflictError('请在原文章中确认脱敏稿。')
        candidate = record['pending']['candidate_text']
        result = {'id': hashlib.sha256(candidate.encode()).hexdigest(), 'text': candidate,
                  'kind': 'redaction', 'label': '已确认脱敏稿'}
        value = self.editor.apply_result(source['path'], target.get('revision'), target.get('text', ''),
                                         record['editor_base'], result, record['id'], record['workflow_id'],
                                         payload.get('confirm_conflict') is True, target.get('stale_ids', []))
        record['editor_base'] = {'workspace': source['workspace'], 'path': source['path'],
                                 'revision': value['revision'], 'text_hash': hashlib.sha256(value['text'].encode()).hexdigest()}
        self.store.save_event(record, 'working_copy_updated', '已确认脱敏稿成为文章工作副本，原文件未写入。')

    @staticmethod
    def _validate_editor_target(target):
        if not isinstance(target, dict) or any(not isinstance(target.get(key), str) for key in ('path', 'revision', 'text')):
            raise ValueError('请提交当前文章、版本和正文快照。')
        stale = target.get('stale_ids', [])
        if not isinstance(stale, list) or len(stale) > 60 or any(not isinstance(item, str) for item in stale):
            raise ValueError('待复查标识格式不正确。')

    def _archive(self, record: dict, update: dict) -> None:
        directory = self.store.directory / "artifacts" / record["id"]
        for key, (artifact_id, label, suffix) in ARTIFACTS.items():
            if not update.get(key):
                continue
            # 原始路径来自已完成的节点，不接受浏览器传入的下载路径。
            content = Path(update[key]).read_text(encoding="utf-8")
            path = directory / f"{artifact_id}{suffix}"
            atomic_write(path, content)
            artifact = {"id": artifact_id, "label": label, "filename": path.name,
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
                executions.append({"id": uuid4().hex, "node": node,
                                   "iteration": 1 + sum(x["node"] == node for x in executions),
                                   "status": "running", "started_at": now(), "input": event.get("input", {}), "output": {}})
            record["current_node"] = node
            self.store.save_event(record, "node_started", f"开始：{self._node_label(record, node)}")
        else:
            record["executions"][-1]["status"] = "failed" if event["type"] == "node_failed" else "skipped"
            self.store.save_event(record, event["type"], event["summary"])

    def _drive(self, run_id: str, value) -> None:
        entry = self.live[run_id]
        graph, runtime = entry["graph"], entry["runtime"]
        config = {"configurable": {"thread_id": run_id}, "recursion_limit": 10000}
        try:
            while True:
                for mode, chunk in graph.stream(value, config, stream_mode=["updates", "custom"]):
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
                                record['annotation_text'] = record['state']['raw_text']
                                record['annotations'] = self.annotations(record)
                                source = record.get('editor_source')
                                if self.editor and source:
                                    record['annotation_text'] = self.editor.get(source['path'])['text']
                            if 'redaction_findings' in update:
                                record.pop('annotations', None)
                                record['annotations'] = self.annotations(record)
                                # annotations() 已投影到当前工作副本；检测快照仍保存在 checked_text。
                                source = record.get('editor_source')
                                record['annotation_text'] = self.editor.get(source['path'])['text'] if self.editor and source else record['state']['checked_text']
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
                    if interrupts:
                        if runtime.stop.is_set():
                            value = Command(resume={"action": "stop"})
                            continue
                        payload = runtime.scrub(interrupts[0].value)
                        record.update(status="waiting", pending={**payload, "id": uuid4().hex})
                        record["executions"][-1]["status"] = "waiting"
                        self.store.save_event(record, "waiting", "等待人工操作。")
                        return
                    state = record["state"]
                    record.update(status="failed" if state.get("failure") else "stopped" if state.get("stopped") else "completed", pending=None)
                    self.store.save_event(record, "finished", {"failed": "任务失败，已保留记录。", "stopped": "任务已停止，已写入的文件保留。",
                                                              "completed": "任务已完成。"}[record["status"]])
                    self.live.pop(run_id, None)
                    return
        except Exception as error:
            with self.lock:
                record = self.store.get(run_id)
                message = runtime.scrub(str(error)) if isinstance(error, OSError) else f"运行异常（{type(error).__name__}），记录已保留。"
                record.update(status="failed", pending=None)
                record["state"]["failure"] = message
                if record["executions"] and record["executions"][-1]["status"] in {"running", "waiting"}:
                    record["executions"][-1].update(status="failed", finished_at=now())
                self.store.save_event(record, "failed", message)
                self.live.pop(run_id, None)

    @staticmethod
    def _node_label(record: dict, node: str) -> str:
        return next((item["label"] for item in record["workflow"]["nodes"] if item["id"] == node), node)
