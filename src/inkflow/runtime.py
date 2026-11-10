"""执行依赖只在内存中存活，不进入文章或运行历史。"""

import threading
from dataclasses import dataclass, field
from typing import Any

from inkflow.llm import LLMConfig


@dataclass
class WorkflowRuntime:
    llm: LLMConfig
    api_key: str | None = field(default=None, repr=False)
    stop: threading.Event = field(default_factory=threading.Event)

    def scrub(self, value: Any) -> Any:
        if isinstance(value, str):
            return value.replace(self.api_key, "[密钥已隐藏]") if self.api_key else value
        if isinstance(value, dict):
            return {key: self.scrub(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.scrub(item) for item in value]
        return value
