"""应用配置。

沿用 nanobot「显式优于魔法」的配置理念：所有配置项都在这里显式列出，不做隐藏的
自动推断。当前用环境变量承载配置（.env 文件），先不做完整的 Pydantic 多层
schema——学习/复现的第一版，够用就好。
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ConfigurationError(RuntimeError):
    """用户可修复的启动配置错误。"""


class ProviderConfig(BaseModel):
    """一个 OpenAI 兼容接口的连接配置。"""

    model_config = ConfigDict(validate_default=True)

    api_key: str = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    api_base: str = Field(
        default_factory=lambda: os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
    )
    model: str = Field(default_factory=lambda: os.getenv("MODEL_NAME", "gpt-4o-mini"))
    temperature: float = Field(
        default_factory=lambda: float(os.getenv("MODEL_TEMPERATURE", "0.7"))
    )
    max_tokens: int = Field(
        default_factory=lambda: int(os.getenv("MODEL_MAX_TOKENS", "4096")),
        ge=1,
    )
    timeout_seconds: float = Field(
        default_factory=lambda: float(os.getenv("MODEL_TIMEOUT_SECONDS", "120")),
        gt=0,
    )

    @field_validator("api_base")
    @classmethod
    def validate_api_base(cls, value: str) -> str:
        """在构造 HTTP 客户端前拒绝空地址和未替换的示例占位符。"""
        value = value.strip().rstrip("/")
        if not value:
            raise ValueError("OPENAI_API_BASE 不能为空")
        if any(marker in value for marker in ("[workspace-id]", "<workspace-id>", "{workspace_id}")):
            raise ValueError("OPENAI_API_BASE 仍包含 workspace ID 占位符，请替换为真实值")
        try:
            parsed = urlsplit(value)
            hostname = parsed.hostname
        except ValueError as exc:
            raise ValueError(f"OPENAI_API_BASE 不是有效 URL：{value}") from exc
        if parsed.scheme not in {"http", "https"} or not hostname:
            raise ValueError("OPENAI_API_BASE 必须是包含主机名的 http/https URL")
        return value


class AppConfig(BaseModel):
    """应用级配置：会话存储位置、上下文压缩阈值、长任务上限、MCP 配置等。"""

    provider: ProviderConfig = Field(default_factory=ProviderConfig)

    workspace_dir: Path = Field(
        default_factory=lambda: Path(os.getenv("WORKSPACE_DIR", "./workspace")).resolve()
    )
    context_window: int = Field(default_factory=lambda: int(os.getenv("CONTEXT_WINDOW", "32000")))
    consolidation_ratio: float = Field(
        default_factory=lambda: float(os.getenv("CONSOLIDATION_RATIO", "0.5"))
    )
    max_iterations: int = Field(default_factory=lambda: int(os.getenv("MAX_ITERATIONS", "25")))
    max_goal_continuations: int = Field(
        default_factory=lambda: int(os.getenv("MAX_GOAL_CONTINUATIONS", "12")),
        ge=0,
    )
    run_timeout_seconds: float = Field(
        default_factory=lambda: float(os.getenv("RUN_TIMEOUT_SECONDS", "600")),
        gt=0,
    )
    max_concurrent_subagents: int = Field(
        default_factory=lambda: int(os.getenv("MAX_CONCURRENT_SUBAGENTS", "3")),
        ge=1,
    )
    subagent_timeout_seconds: float = Field(
        default_factory=lambda: float(os.getenv("SUBAGENT_TIMEOUT_SECONDS", "300")),
        gt=0,
    )
    dream_auto_threshold: int = Field(
        default_factory=lambda: int(os.getenv("DREAM_AUTO_THRESHOLD", "10")),
        ge=0,
    )
    mcp_config_path: str = Field(default_factory=lambda: os.getenv("MCP_CONFIG_PATH", ""))

    @property
    def db_path(self) -> Path:
        """LangGraph SqliteSaver 的会话数据库文件路径。"""
        return self.workspace_dir / "sessions.db"

    @property
    def memory_dir(self) -> Path:
        """长期记忆文件（SOUL.md / USER.md / MEMORY.md / history.jsonl）所在目录。"""
        return self.workspace_dir / "memory"

    def ensure_dirs(self) -> None:
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)


def load_config() -> AppConfig:
    """加载 .env（如果存在），构建并校验应用配置。"""
    load_dotenv()
    try:
        cfg = AppConfig()
    except ValidationError as exc:
        raise ConfigurationError(f"配置无效：\n{exc}") from exc
    if not cfg.provider.api_key:
        raise ConfigurationError(
            "未设置 OPENAI_API_KEY。请复制 .env.example 为 .env 并填入你的密钥。"
        )
    cfg.ensure_dirs()
    return cfg
