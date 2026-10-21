"""Configuration: process knobs from the environment (`TANDEM_*`), product config from `tandem.yaml`.

With neither a config file nor any model keys, Tandem runs in mock mode with fake connectors, so a
fresh checkout is a complete offline demo.
"""

from __future__ import annotations

import os
import re
from datetime import time, timedelta
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]


def examples_dir() -> Path:
    """`<repo>/examples` in a checkout, `/app/examples` in the image, else `./examples`."""
    for candidate in (REPO_ROOT / "examples", Path.cwd() / "examples"):
        if candidate.is_dir():
            return candidate
    return REPO_ROOT / "examples"


class Settings(BaseSettings):
    """Process-level settings. Each field maps to `TANDEM_<NAME>` (e.g. `TANDEM_PORT`)."""

    model_config = SettingsConfigDict(env_prefix="TANDEM_", env_file=".env", extra="ignore")

    data_dir: Path = Path("./data")
    database_url: str | None = None
    config: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    auth_token: SecretStr | None = None
    mock: bool = False
    timezone: str | None = None
    log_level: str = "info"
    cors_origins: str = ""
    autostart: bool = True  # run the scheduler loop inside `serve`

    @property
    def state_dir(self) -> Path:
        path = self.data_dir.resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{(self.state_dir / 'tandem.db').as_posix()}"


# ---- tandem.yaml ---------------------------------------------------------------------------------

ProviderName = Literal["mock", "anthropic", "openai", "openai_compatible", "gemini", "ollama"]
ROLES = ("triage", "brief", "chat", "worker")


class ModelProfile(BaseModel):
    """One model you can assign to roles. Secrets come from `api_key_env`, never the file."""

    provider: ProviderName = "mock"
    model: str = "mock-1"
    base_url: str | None = None
    api_key_env: str | None = None
    temperature: float | None = None
    max_tokens: int = 4096
    timeout_s: float = 120.0
    name: str = ""

    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) if self.api_key_env else None

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "has_api_key": bool(self.api_key()),
        }


def parse_duration(value: str | int | float | timedelta) -> timedelta:
    """`90` (seconds), `"15m"`, `"2h"`, `"1h30m"`, `"45s"`."""
    if isinstance(value, timedelta):
        return value
    if isinstance(value, int | float):
        return timedelta(seconds=value)
    parts = re.findall(r"(\d+(?:\.\d+)?)\s*([smhd])", value.strip().lower())
    if not parts:
        return timedelta(seconds=float(value))
    unit = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return timedelta(seconds=sum(float(n) * unit[u] for n, u in parts))


def parse_hhmm(value: str) -> time:
    hh, mm = value.strip().split(":")
    return time(int(hh), int(mm))


class ScheduleConfig(BaseModel):
    checkin_every: str = "15m"
    brief_at: str = "07:30"
    timezone: str = "UTC"
    catchup: str = "5m"

    @property
    def checkin_interval(self) -> timedelta:
        return parse_duration(self.checkin_every)

    @property
    def brief_time(self) -> time:
        return parse_hhmm(self.brief_at)


class NotifyChannel(BaseModel):
    type: Literal["ui", "ntfy", "webhook"] = "ui"
    url: str | None = None


class NotifyConfig(BaseModel):
    min_urgency: Literal["low", "normal", "high", "critical"] = "high"
    quiet_hours: str | None = "21:00-07:00"
    max_per_hour: int = 4
    channels: list[NotifyChannel] = Field(default_factory=lambda: [NotifyChannel(type="ui")])


class HarnessConfig(BaseModel):
    default: str = "builtin"
    max_steps: int = 24
    max_concurrent: int = 2
    cli_command: Literal["auto", "claude", "codex"] = "auto"
    cli_timeout_s: float = 1800


class SandboxConfig(BaseModel):
    mode: Literal["auto", "docker", "local"] = "auto"
    image: str = "python:3.13-slim"
    network: bool = False
    memory: str = "1g"
    cpus: str = "1"
    exec_timeout_s: float = 120


class MemoryConfig(BaseModel):
    core_chars: int = 2000
    thread_window: int = 30


class VoiceConfig(BaseModel):
    provider: Literal["none", "mock", "openai_compatible"] = "none"
    base_url: str | None = None
    model: str = "whisper-1"
    api_key_env: str | None = None


class ConnectorConfig(BaseModel):
    """A connector instance: `type` picks the class, `name` prefixes its tools, the rest is options."""

    model_config = {"extra": "allow"}
    type: str
    name: str | None = None
    enabled: bool = True

    def options(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class AppConfig(BaseModel):
    models: dict[str, ModelProfile] = Field(default_factory=dict)
    roles: dict[str, str] = Field(default_factory=dict)
    harness: HarnessConfig = Field(default_factory=HarnessConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    connectors: list[ConnectorConfig] = Field(default_factory=list)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    policy: str | None = None  # path to policy.yaml; default examples/policy.yaml
    skills: list[str] = Field(default_factory=list)  # extra skill folders
    harness_options: dict[str, Any] = Field(
        default_factory=dict
    )  # per-harness extras, e.g. {deepagents: {native_model: true}}
    persona: str = ""  # extra instructions appended to every system prompt
    user_name: str = ""
    demo_autoplay: bool | None = None  # mock mode: brief + check-in at startup

    @field_validator("models", mode="before")
    @classmethod
    def _names(cls, v: Any) -> Any:
        if isinstance(v, dict):
            for key, prof in v.items():
                if isinstance(prof, dict):
                    prof.setdefault("name", key)
        return v

    @property
    def mock(self) -> bool:
        return all(p.provider == "mock" for p in self.models.values())

    def profile_for(self, role: str) -> ModelProfile:
        name = self.roles.get(role)
        if name and name in self.models:
            return self.models[name]
        if role == "triage" and "cheap" in self.models:
            return self.models["cheap"]
        if "strong" in self.models:
            return self.models["strong"]
        if self.models:
            return next(iter(self.models.values()))
        return ModelProfile(name="mock", provider="mock", model="mock-1")


def _profiles_from_env() -> dict[str, ModelProfile]:
    """Zero-config live mode: pick up a provider key from the environment if one is set."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return {
            "strong": ModelProfile(
                name="strong", provider="anthropic", model="claude-opus-5-5", api_key_env="ANTHROPIC_API_KEY"
            ),
            "cheap": ModelProfile(
                name="cheap", provider="anthropic", model="claude-haiku-5-5", api_key_env="ANTHROPIC_API_KEY"
            ),
        }
    if os.environ.get("OPENAI_API_KEY"):
        return {
            "strong": ModelProfile(
                name="strong", provider="openai", model="gpt-5", api_key_env="OPENAI_API_KEY"
            ),
            "cheap": ModelProfile(
                name="cheap", provider="openai", model="gpt-5-mini", api_key_env="OPENAI_API_KEY"
            ),
        }
    if os.environ.get("GEMINI_API_KEY"):
        return {
            "strong": ModelProfile(
                name="strong", provider="gemini", model="gemini-2.5-pro", api_key_env="GEMINI_API_KEY"
            ),
            "cheap": ModelProfile(
                name="cheap", provider="gemini", model="gemini-2.5-flash", api_key_env="GEMINI_API_KEY"
            ),
        }
    return {}


def load_config(settings: Settings) -> AppConfig:
    path = settings.config or (Path("tandem.yaml") if Path("tandem.yaml").is_file() else None)
    data: dict[str, Any] = {}
    if path:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    cfg = AppConfig.model_validate(data)
    if settings.mock:
        cfg.models = {
            "strong": ModelProfile(name="strong", provider="mock", model="mock-strong"),
            "cheap": ModelProfile(name="cheap", provider="mock", model="mock-cheap"),
        }
        cfg.roles = {}
    elif not cfg.models:
        cfg.models = _profiles_from_env() or {
            "strong": ModelProfile(name="strong", provider="mock", model="mock-strong"),
            "cheap": ModelProfile(name="cheap", provider="mock", model="mock-cheap"),
        }
    if not cfg.connectors:
        if cfg.mock:
            cfg.connectors = [
                ConnectorConfig(type="fake", name="demo"),
                ConnectorConfig(type="webhook", name="inbox"),
            ]
        else:
            cfg.connectors = [ConnectorConfig(type="webhook", name="inbox")]
    if settings.timezone:
        cfg.schedule.timezone = settings.timezone
    if cfg.demo_autoplay is None:
        cfg.demo_autoplay = cfg.mock
    if cfg.voice.provider == "none" and cfg.mock:
        cfg.voice.provider = "mock"
    return cfg


def policy_path(cfg: AppConfig) -> Path:
    return Path(cfg.policy) if cfg.policy else examples_dir() / "policy.yaml"
