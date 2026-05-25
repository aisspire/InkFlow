"""InkFlow 的 LLM 调用适配层。

这个模块只负责“怎么调用模型”，不负责“在哪个工作流节点里使用模型”。
后续 LangGraph 节点只需要调用这里暴露的简单函数，
就不用关心 OpenAI SDK、base_url、API Key 等细节。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NotRequired, Sequence, TypedDict

from inkflow.console_log import log_llm_thinking


DEFAULT_LLM_CONFIG_PATH = Path("llm.toml")
SUPPORTED_PROVIDER = "openai-compatible"


class LLMConfigError(RuntimeError):
    """LLM 配置不完整或不合法时抛出的错误。"""


class LLMMessage(TypedDict):
    """传给聊天模型的一条消息。

    这里先保留最通用的三种角色：
    - system：系统指令，用来约束模型身份和输出规则
    - user：用户输入，也就是本次要处理的内容
    - assistant：模型历史回复，后续做多轮对话时会用到
    """

    role: Literal["system", "user", "assistant"]
    content: str


class _ChatCompletionParams(TypedDict):
    """传给 OpenAI SDK 的可选参数。

    这个类型只在模块内部使用，目的是避免把 SDK 参数形状泄漏到业务代码。
    """

    max_tokens: NotRequired[int]


@dataclass(frozen=True)
class LLMConfig:
    """从 llm.toml 读取出来的模型配置。

    这个数据类是 InkFlow 自己的配置对象。
    即使以后底层从 OpenAI SDK 换成别的 SDK，
    外部代码也可以继续依赖这个稳定结构。
    """

    enabled: bool
    provider: str
    model: str
    api_key_env: str
    base_url: str | None = None
    temperature: float = 0.7
    timeout_seconds: float = 30
    max_tokens: int | None = None


def load_llm_config(config_path: Path = DEFAULT_LLM_CONFIG_PATH) -> LLMConfig:
    """读取 LLM TOML 配置，并转换成 LLMConfig。

    第一版只读取 [llm] 这一段，真实密钥不放在 TOML 中，
    而是通过 api_key_env 指向的环境变量读取。
    """

    if not config_path.exists():
        raise LLMConfigError(
            f"找不到 LLM 配置文件：{config_path}。"
            "请先复制 llm.example.toml 为 llm.toml，并按需修改。"
        )

    with config_path.open("rb") as config_file:
        raw_config = tomllib.load(config_file)

    llm_section = raw_config.get("llm")
    if not isinstance(llm_section, dict):
        raise LLMConfigError("LLM 配置文件必须包含 [llm] 配置段。")

    enabled = _optional_bool(llm_section, "enabled", default=False)
    provider = _required_string(llm_section, "provider")
    model = _required_string(llm_section, "model")
    api_key_env = _required_string(llm_section, "api_key_env")

    if provider != SUPPORTED_PROVIDER:
        raise LLMConfigError(
            f"暂不支持 provider={provider!r}。"
            f"当前第一版只支持 {SUPPORTED_PROVIDER!r}。"
        )

    base_url = _optional_string(llm_section, "base_url")
    temperature = _optional_number(llm_section, "temperature", default=0.7)
    timeout_seconds = _optional_number(llm_section, "timeout_seconds", default=30)
    max_tokens = _optional_positive_int(llm_section, "max_tokens")

    return LLMConfig(
        enabled=enabled,
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        base_url=base_url,
        temperature=temperature,
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
    )


def is_llm_enabled(config_path: Path = DEFAULT_LLM_CONFIG_PATH) -> bool:
    """判断当前配置是否启用真实 LLM。

    这个函数只读取配置，不会创建客户端，也不会触发网络请求。
    LangGraph 节点可以先用它决定是否走真实模型分支。
    """

    if not config_path.exists():
        return False

    return load_llm_config(config_path).enabled


def call_llm(
    messages: Sequence[LLMMessage],
    *,
    config: LLMConfig | None = None,
    config_path: Path = DEFAULT_LLM_CONFIG_PATH,
) -> str:
    """调用聊天模型并返回纯文本结果。

    外部模块只应该依赖这个简单返回值。
    OpenAI SDK 返回对象的 choices/message/content 等细节，
    都被限制在这个函数内部。
    """

    if not messages:
        raise ValueError("messages 不能为空。")

    active_config = config or load_llm_config(config_path)
    client = _create_openai_client(active_config)

    optional_params: _ChatCompletionParams = {}
    if active_config.max_tokens is not None:
        optional_params["max_tokens"] = active_config.max_tokens

    log_llm_thinking()
    response = client.chat.completions.create(
        model=active_config.model,
        messages=list(messages),
        temperature=active_config.temperature,
        **optional_params,
    )

    content = response.choices[0].message.content
    if content is None:
        raise RuntimeError("LLM 返回了空内容。")

    return content


def _create_openai_client(config: LLMConfig):
    """创建 OpenAI SDK 客户端。

    import 放在函数内部，是为了让配置读取等纯 Python 逻辑
    不被第三方 SDK 是否安装影响；真正调用模型时才需要 openai 依赖。
    """

    try:
        from openai import OpenAI
    except ImportError as error:
        raise RuntimeError(
            "缺少 openai 依赖。请先安装项目依赖，例如运行：pip install -e ."
        ) from error

    api_key = os.environ.get(config.api_key_env)
    if not api_key:
        raise LLMConfigError(
            f"环境变量 {config.api_key_env!r} 未设置。"
            "请先在当前终端或系统环境变量中配置真实 API Key。"
        )

    client_kwargs: dict[str, object] = {
        "api_key": api_key,
        "timeout": config.timeout_seconds,
    }
    if config.base_url is not None:
        client_kwargs["base_url"] = config.base_url

    return OpenAI(**client_kwargs)


def _required_string(section: dict, key: str) -> str:
    """读取必填字符串配置。"""

    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LLMConfigError(f"LLM 配置项 {key!r} 必须是非空字符串。")
    return value.strip()


def _optional_string(section: dict, key: str) -> str | None:
    """读取可选字符串配置，空字符串会被当作未配置。"""

    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise LLMConfigError(f"LLM 配置项 {key!r} 必须是字符串。")

    stripped_value = value.strip()
    return stripped_value or None


def _optional_bool(section: dict, key: str, *, default: bool) -> bool:
    """读取可选布尔配置。"""

    value = section.get(key, default)
    if not isinstance(value, bool):
        raise LLMConfigError(f"LLM 配置项 {key!r} 必须是布尔值。")
    return value


def _optional_number(section: dict, key: str, *, default: float) -> float:
    """读取可选数字配置。"""

    value = section.get(key, default)
    if not isinstance(value, int | float):
        raise LLMConfigError(f"LLM 配置项 {key!r} 必须是数字。")
    return float(value)


def _optional_positive_int(section: dict, key: str) -> int | None:
    """读取可选正整数配置。

    在示例配置里，0 表示“不主动传这个参数”。
    """

    value = section.get(key)
    if value is None or value == 0:
        return None
    if not isinstance(value, int) or value < 0:
        raise LLMConfigError(f"LLM 配置项 {key!r} 必须是非负整数。")
    return value
