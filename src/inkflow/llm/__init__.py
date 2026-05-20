"""InkFlow 的 LLM 适配层入口。

这里导出稳定接口，隐藏 core.py 的内部组织。
未来即使继续拆分 config、client、types，业务层也可以尽量不改导入。
"""

from inkflow.llm.core import (
    DEFAULT_LLM_CONFIG_PATH,
    LLMConfig,
    LLMConfigError,
    LLMMessage,
    call_llm,
    is_llm_enabled,
    load_llm_config,
)

__all__ = [
    "DEFAULT_LLM_CONFIG_PATH",
    "LLMConfig",
    "LLMConfigError",
    "LLMMessage",
    "call_llm",
    "is_llm_enabled",
    "load_llm_config",
]
