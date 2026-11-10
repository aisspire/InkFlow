"""配置和私有密钥管理；图只接收无密钥的快照。

TOMLKit 修改指定字段，保留原文件注释、顺序及无关配置段。
所有相对路径以 config.toml 所在目录为基准，在实际后端操作系统上解析。
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit

import tomlkit
from pydantic import BaseModel, ConfigDict, Field, field_validator

from inkflow.llm import LLMConfig
from inkflow.services.local_files import atomic_write
from inkflow.services.file_permissions import private_directory, restrict_access


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool = False
    provider: str = "openai-compatible"
    model: str = "deepseek-v4-pro"
    base_url: str = ""
    api_key_env: str = "OPENAI_API_KEY"
    temperature: float = Field(default=0.7, ge=0, le=2)
    timeout_seconds: float = Field(default=30, ge=1, le=600)
    max_tokens: int = Field(default=0, ge=0, le=1000000)

    @field_validator("model", "api_key_env")
    @classmethod
    def required_text(cls, value: str) -> str:
        if not value.strip() or len(value) > 200:
            raise ValueError("模型名和密钥环境变量名不能为空，且不能超过 200 字符。")
        return value.strip()

    @field_validator("provider")
    @classmethod
    def supported_provider(cls, value: str) -> str:
        if value != "openai-compatible":
            raise ValueError("当前仅支持 openai-compatible。")
        return value

    @field_validator("api_key_env")
    @classmethod
    def environment_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("环境变量名格式不正确。")
        return value

    @field_validator("base_url")
    @classmethod
    def endpoint(cls, value: str) -> str:
        value = value.strip()
        if value:
            parsed = urlsplit(value)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise ValueError("服务地址必须为 HTTP(S) 地址，不得在地址中携带密钥或账号。")
        return value


class DirectorySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    dir: str


class BlogSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    repo_path: str = ""
    content_dir: str = "src/content/blog"


class SettingsData(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    llm: ModelSettings = Field(default_factory=ModelSettings)
    blog: BlogSettings = Field(default_factory=BlogSettings)
    review: DirectorySettings = Field(default_factory=lambda: DirectorySettings(dir="reviews"))
    report: DirectorySettings = Field(default_factory=lambda: DirectorySettings(dir="reports"))


def resolve_directory(value: str, base: Path) -> Path:
    """拒绝把 Windows 路径在 WSL 下误当作相对目录创建。"""

    value = value.strip()
    if not value or "\0" in value:
        raise ValueError("目录不能为空或包含空字符。")
    if os.name != "nt" and (PureWindowsPath(value).drive or "\\" in value):
        raise ValueError("当前后端运行在非 Windows 环境，请使用本机路径（如 /mnt/e/...）。")
    try:
        path = Path(value).expanduser()
    except RuntimeError:
        raise ValueError("无法解析用户目录，请填写明确的本机路径。") from None
    return (path if path.is_absolute() else base / path).resolve()


def check_writable_directory(path: Path) -> None:
    """仅检查现有目录或最近父目录，不在保存设置时创建目标。"""

    existing = path
    while not existing.exists():
        if existing == existing.parent:
            raise ValueError("找不到目录的有效父路径。")
        existing = existing.parent
    if not existing.is_dir() or not os.access(existing, os.W_OK):
        raise ValueError(f"目录不存在、不可写或父路径不是目录：{path}")


class SettingsStore:
    def __init__(self, config_path: Path, data_dir: Path | None = None):
        self.config_path = config_path.resolve()
        self.root = self.config_path.parent
        self.llm_path = self.root / "llm.toml"
        self.data_dir = data_dir or self.root / ".inkflow"
        self.key_path = self.data_dir / "secrets.json"
        self.lock = threading.RLock()

    @staticmethod
    def _read(path: Path):
        return tomlkit.parse(path.read_text(encoding="utf-8")) if path.exists() else tomlkit.document()

    def read(self) -> SettingsData:
        with self.lock:
            config = self._read(self.config_path).unwrap()
            llm = self._read(self.llm_path).unwrap().get("llm", {})
            # 只读取允许编辑的字段；无关配置继续留在原 TOML 中。
            values = {key: {k: v for k, v in config.get(key, {}).items()
                           if k in cls.model_fields}
                      for key, cls in (("blog", BlogSettings), ("review", DirectorySettings),
                                       ("report", DirectorySettings)) if key in config}
            values["llm"] = {key: val for key, val in llm.items() if key in ModelSettings.model_fields}
            return SettingsData.model_validate(values)

    def key(self, settings: SettingsData) -> str | None:
        with self.lock:
            if self.key_path.exists():
                restrict_access(self.key_path)
                saved = json.loads(self.key_path.read_text(encoding="utf-8")).get("api_key")
                if isinstance(saved, str) and saved:
                    return saved
            return os.environ.get(settings.llm.api_key_env) or None

    def public(self) -> dict:
        with self.lock:
            settings = self.read()
            return {**settings.model_dump(), "api_key_configured": bool(self.key(settings)),
                    "local_key_saved": self.key_path.exists(), "platform": os.name,
                    "config_directory": str(self.root)}

    def save_key(self, key: str) -> None:
        if not key.strip() or len(key) > 4096 or any(c.isspace() for c in key):
            raise ValueError("密钥不能为空、包含空白或超过 4096 字符。")
        with self.lock:
            private_directory(self.data_dir)
            atomic_write(self.key_path, json.dumps({"api_key": key}), private=True)

    def clear_key(self) -> None:
        with self.lock:
            self.key_path.unlink(missing_ok=True)

    def save(self, settings: SettingsData) -> dict:
        from inkflow.services.publish import resolve_blog_destination

        with self.lock:
            for directory in (settings.review.dir, settings.report.dir):
                check_writable_directory(resolve_directory(directory, self.root))
            blog = settings.blog
            # 没有博客目录时仍能保存审阅稿；填了目录则必须可用。
            if blog.repo_path:
                repo = resolve_directory(blog.repo_path, self.root)
                target = resolve_blog_destination(repo, blog.content_dir, "inkflow-path-check")
                check_writable_directory(target.parent)
            else:
                from inkflow.services.publish import validate_content_dir
                validate_content_dir(blog.content_dir)
            config = self._read(self.config_path)
            llm = self._read(self.llm_path)
            for section, values in settings.model_dump().items():
                document = llm if section == "llm" else config
                if section not in document:
                    document[section] = tomlkit.table()
                for key, value in values.items():
                    document[section][key] = value
            # 两份文件分别原子替换；若第二份写入失败，恢复第一份。
            old_config = self.config_path.read_text(encoding="utf-8") if self.config_path.exists() else None
            atomic_write(self.config_path, tomlkit.dumps(config))
            try:
                atomic_write(self.llm_path, tomlkit.dumps(llm))
            except OSError:
                if old_config is None:
                    self.config_path.unlink(missing_ok=True)
                else:
                    atomic_write(self.config_path, old_config)
                raise
            return self.public()

    def snapshot(self, *, no_publish: bool = False) -> tuple[dict, LLMConfig, str | None]:
        """业务快照可持久化；返回的第三项密钥只能交给进程内 runtime。"""

        with self.lock:
            settings = self.read()
            model = settings.llm.model_dump()
            model["base_url"] = model["base_url"] or None
            model["max_tokens"] = model["max_tokens"] or None
            llm_config = LLMConfig(**model)
            config = settings.model_dump(exclude={"llm"})
            config.update(_config_dir=str(self.root), llm=asdict(llm_config),
                          publish={"no_publish": no_publish})
            # 空字符串同样是一份已固定的“无密钥”快照，不能在调用时重读环境。
            return config, llm_config, self.key(settings) or ""
