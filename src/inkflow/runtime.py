"""运行固定资产与非密钥参数；凭据只在内存中解析。"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import re
import threading
import time
from typing import Any, Callable
from uuid import uuid4

from inkflow.llm import LLMConfig


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


class ModelExecutionError(RuntimeError):
    def __init__(self, category, message):
        self.category = category
        super().__init__(message)


def error_category(error):
    if hasattr(error, 'category'):
        return error.category
    name = type(error).__name__.lower()
    if 'timeout' in name or isinstance(error, TimeoutError):
        return 'timeout'
    if 'connection' in name or isinstance(error, ConnectionError):
        return 'connection'
    if isinstance(error, OSError):
        return 'filesystem'
    if isinstance(error, ValueError):
        return 'business_validation'
    return 'code'


@dataclass
class WorkflowRuntime:
    llm: LLMConfig
    api_key: str | None = field(default=None, repr=False)
    stop: threading.Event = field(default_factory=threading.Event)
    package: dict | None = None
    version_id: str | None = None
    owner_id: str = 'cli'
    owner_kind: str = 'run'
    diagnostic_sink: Callable | None = field(default=None, repr=False)
    current_node: str | None = None
    current_attempt: str | None = None
    last_attempts: dict = field(default_factory=dict)
    node_keys: dict = field(default_factory=dict, repr=False)
    document_id: str | None = None
    durable: Any = field(default=None, repr=False)
    checkpointer: Any = field(default=None, repr=False)

    def wrap_node(self, name, function):
        return self.durable.wrap(self, name, function) if self.durable else function

    def __post_init__(self):
        if self.api_key is None:
            import os
            self.api_key = os.environ.get(self.llm.api_key_env) or ''
        self.package = deepcopy(self.package)

    def scrub(self, value: Any) -> Any:
        """Only the resolved connection credential is removed from business state."""
        if isinstance(value, str):
            for key in {self.api_key, *self.node_keys.values()} - {None, ''}:
                value = value.replace(key, '[密钥已隐藏]')
            return value
        if isinstance(value, dict):
            return {key: self.scrub(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.scrub(item) for item in value]
        return value

    def diagnostic_scrub(self, value: Any) -> Any:
        """Broader credential filtering applies to private diagnostic copies, never node inputs."""
        if isinstance(value, str):
            return re.sub(r'(?i)(Bearer\s+)[^\s"\'<>]+', r'\1[密钥已隐藏]', self.scrub(value))
        if isinstance(value, dict):
            return {key: '[密钥已隐藏]' if re.fullmatch(r'(?i)(api[_-]?key|authorization|access_token|secret)', str(key))
                    else self.diagnostic_scrub(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.diagnostic_scrub(item) for item in value]
        return value

    def _save(self, item):
        self.last_attempts[(item['node_id'], item['kind'])] = {'id': item['id'], 'status': item['status']}
        if self.diagnostic_sink:
            self.diagnostic_sink(self.diagnostic_scrub(deepcopy(item)))

    def _attempt(self, node, kind, inputs):
        identity = uuid4().hex
        previous = self.last_attempts.get((node, kind))
        self.last_attempts[(node, kind)] = {'id': identity, 'status': 'running'}
        return {'id': identity, 'attempt_id': identity, 'owner_id': self.owner_id, 'owner_kind': self.owner_kind,
                'run_id': self.owner_id if self.owner_kind == 'run' else None,
                'document_id': self.document_id, 'version_id': self.version_id,
                'test_id': self.owner_id if self.owner_kind == 'test' else None,
                'node_id': node, 'kind': kind, 'node_attempt_id': self.current_attempt,
                'workflow_version': self.version_id, 'workflow_id': self.package.get('workflow_id') if self.package else None,
                'implementation': (self.package or {}).get('nodes', {}).get(node, {}).get('implementation'),
                'started_at': timestamp(), 'finished_at': None, 'duration_ms': None, 'status': 'running',
                'input': inputs, 'previous_attempt_id': previous['id'] if previous else None,
                'retry_of': previous['id'] if previous and previous['status'] == 'failed' else None, 'usage': None, 'truncated': False, 'credentials_filtered': True}

    @contextmanager
    def node(self, name, inputs):
        item = self._attempt(name, 'node', inputs)
        old_node, old_attempt = self.current_node, self.current_attempt
        self.current_node, self.current_attempt = name, item['id']
        started = time.monotonic()
        self._save(item)
        try:
            yield item
            item['status'] = item.get('status') if item.get('status') != 'running' else 'completed'
        except BaseException as error:
            from langgraph.errors import GraphInterrupt
            if isinstance(error, GraphInterrupt):
                item['status'] = 'waiting'
            else:
                item.update(status='stopped' if error_category(error) == 'stopped' else 'failed', error={'category': error_category(error), 'message': self.diagnostic_scrub(str(error))})
            raise
        finally:
            item.update(finished_at=timestamp(), duration_ms=round((time.monotonic() - started) * 1000, 3))
            self._save(item)
            self.current_node, self.current_attempt = old_node, old_attempt

    def invoke(self, prompt_id, variables, parser, transport):
        from inkflow.workflow_assets.prompts import BUILTINS, render
        prompt = (self.package or {}).get('prompts', {}).get(prompt_id, BUILTINS[prompt_id])
        if self.package is not None and prompt_id not in self.package['prompts']:
            raise ModelExecutionError('configuration', '流程版本未绑定此提示词，不能读取全局替代。')
        item = self._attempt(self.current_node or prompt_id, 'model', variables)
        item.update(prompt_id=prompt_id, prompt_revision=prompt['revision'], model=asdict(self.llm))
        started = time.monotonic()
        try:
            item['messages'] = render(prompt, variables)
            self._save(item)
            if self.stop.is_set():
                raise ModelExecutionError('stopped', '已停止；没有发送新的模型请求。')
            if not self.llm.enabled:
                raise ModelExecutionError('disabled', '模型未启用，请配置模型并启用流程版本。')
            if not self.api_key:
                raise ModelExecutionError('credentials', '缺少模型密钥，请在设置中配置受控凭据。')
            from inkflow.web.durable import model_call
            raw = model_call(transport, item['messages'], scrub=self.scrub, config=self.llm, api_key=self.api_key)
            item['raw_output'] = str(raw)
            item['usage'] = getattr(raw, 'usage', None)
            if self.stop.is_set():
                raise ModelExecutionError('stopped', '已停止；已返回的模型结果仅保留诊断，不采用。')
            try:
                result = parser(self.scrub(str(raw)))
            except ValueError as error:
                failure = ModelExecutionError('output_format', '模型输出不符合协议：' + self.scrub(str(error)))
                failure.raw_output = self.scrub(str(raw))
                raise failure from None
            item.update(parsed_output=result, status='completed')
            return result
        except Exception as error:
            item.update(status='stopped' if error_category(error) == 'stopped' else 'failed',
                        error={'category': error_category(error), 'message': self.diagnostic_scrub(str(error))})
            raise
        finally:
            item.update(finished_at=timestamp(), duration_ms=round((time.monotonic() - started) * 1000, 3))
            self._save(item)
