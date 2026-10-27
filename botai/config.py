"""Đọc cấu hình: biến môi trường (.env) + danh sách trợ lý (config/agents.yaml)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> None:
    """Nạp file .env đơn giản (KEY=VALUE), không ghi đè biến đã có sẵn."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    data_dir: Path
    admin_ids: set[int]
    admin_chat_id: int | None
    telegram_api_base: str | None
    fb_graph_version: str
    fb_watch_interval: int
    fb_watch_agent: str | None
    fb_auto_reply: bool
    max_upload_mb: int

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    @property
    def memory_dir(self) -> Path:
        return self.data_dir / "memory"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"

    @property
    def knowledge_dir(self) -> Path:
        return self.data_dir / "knowledge"

    def ensure_dirs(self) -> None:
        for d in (self.work_dir, self.state_dir, self.memory_dir, self.secrets_dir, self.knowledge_dir):
            d.mkdir(parents=True, exist_ok=True)
        try:
            self.secrets_dir.chmod(0o700)
        except OSError:
            pass

    @classmethod
    def from_env(cls) -> "Settings":
        admin_ids = {
            int(x) for x in os.environ.get("ADMIN_TELEGRAM_IDS", "").replace(" ", "").split(",") if x
        }
        admin_chat = os.environ.get("ADMIN_CHAT_ID", "").strip()
        data_dir = Path(os.environ.get("BOTAI_DATA_DIR", ROOT / "data"))
        if not data_dir.is_absolute():
            data_dir = ROOT / data_dir
        return cls(
            data_dir=data_dir,
            admin_ids=admin_ids,
            admin_chat_id=int(admin_chat) if admin_chat else (min(admin_ids) if admin_ids else None),
            telegram_api_base=os.environ.get("TELEGRAM_API_BASE") or None,
            fb_graph_version=os.environ.get("FB_GRAPH_VERSION", "v23.0"),
            fb_watch_interval=int(os.environ.get("FB_WATCH_INTERVAL", "0") or 0),
            fb_watch_agent=os.environ.get("FB_WATCH_AGENT") or None,
            fb_auto_reply=_bool(os.environ.get("FB_AUTO_REPLY"), False),
            max_upload_mb=int(os.environ.get("MAX_UPLOAD_MB", "20") or 20),
        )


@dataclass
class AgentConfig:
    name: str
    display_name: str
    telegram_token_env: str
    provider: str  # anthropic | openai | deepseek
    model: str
    role: str
    skills: list[str] = field(default_factory=lambda: ["common"])
    delegate_to: list[str] = field(default_factory=list)
    knowledge_files: list[str] = field(default_factory=list)
    effort: str | None = None
    max_tokens: int = 16000
    max_steps: int = 40
    max_history_messages: int = 160
    vision: bool = True
    allow_shell: bool = False
    fallbacks: bool = True

    @property
    def telegram_token(self) -> str | None:
        return os.environ.get(self.telegram_token_env) or None


def load_agents(path: Path) -> list[AgentConfig]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = data.get("defaults", {}) or {}
    agents: list[AgentConfig] = []
    seen: set[str] = set()
    for item in data.get("agents", []) or []:
        merged = {**defaults, **item}
        if "name" not in merged or "role" not in merged:
            raise ValueError(f"Mỗi trợ lý cần có 'name' và 'role': {item!r}")
        if merged["name"] in seen:
            raise ValueError(f"Trùng tên trợ lý: {merged['name']}")
        seen.add(merged["name"])
        merged.setdefault("display_name", merged["name"])
        merged.setdefault("telegram_token_env", f"TG_TOKEN_{merged['name'].upper()}")
        merged.setdefault("provider", "anthropic")
        merged.setdefault("model", "claude-opus-5-5")
        allowed = AgentConfig.__dataclass_fields__.keys()
        unknown = set(merged) - set(allowed)
        if unknown:
            raise ValueError(f"Trợ lý {merged['name']}: khóa không hợp lệ {sorted(unknown)}")
        agents.append(AgentConfig(**merged))
    for a in agents:
        for target in a.delegate_to:
            if target not in seen:
                raise ValueError(f"Trợ lý {a.name} giao việc cho '{target}' nhưng không có trợ lý đó")
    return agents
