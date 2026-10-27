"""Hạ tầng "kỹ năng" (tools) mà trợ lý AI được phép gọi."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from ..config import AgentConfig, Settings


@dataclass
class ToolResult:
    text: str
    images: list[Path] = field(default_factory=list)
    is_error: bool = False


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable[..., Awaitable[str | ToolResult]]


@dataclass
class ToolContext:
    """Thông tin cho một lượt làm việc: ai đang chat, thư mục làm việc, cách gửi file."""

    agent: "AgentConfig"
    settings: "Settings"
    chat_id: int
    workdir: Path
    send_file: Callable[[Path, str], Awaitable[None]]
    notify: Callable[[str], Awaitable[None]]
    extra: dict[str, Any] = field(default_factory=dict)

    def resolve(self, path: str, must_exist: bool = True) -> Path:
        """Chuẩn hóa đường dẫn và CHẶN truy cập ra ngoài thư mục làm việc."""
        p = Path(path)
        if not p.is_absolute():
            p = self.workdir / p
        p = p.resolve()
        root = self.workdir.resolve()
        if p != root and root not in p.parents:
            raise ToolError(f"Đường dẫn '{path}' nằm ngoài thư mục làm việc")
        if must_exist and not p.exists():
            raise ToolError(f"Không tìm thấy file '{path}'")
        return p

    def rel(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.workdir.resolve()))
        except ValueError:
            return str(path)


class ToolError(Exception):
    """Lỗi có thông điệp thân thiện, trả lại cho AI để nó tự sửa."""


def tool(name: str, description: str, properties: dict, required: list[str] | None = None):
    """Decorator đăng ký một hàm thành Tool với JSON schema."""

    def wrap(func):
        return Tool(
            name=name,
            description=description,
            parameters={
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
            func=func,
        )

    return wrap


async def run_cmd(args: list[str], cwd: Path | None = None, timeout: float = 1800) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise ToolError(f"Lệnh chạy quá {int(timeout)} giây và đã bị dừng: {' '.join(args[:3])}...")
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def get_skill_tools(skill: str) -> list[Tool]:
    from . import common, facebook, video

    skills = {
        "common": common.TOOLS,
        "shell": common.SHELL_TOOLS,
        "video": video.TOOLS,
        "facebook": facebook.TOOLS,
    }
    if skill not in skills:
        raise ValueError(f"Kỹ năng không tồn tại: {skill}. Có: {', '.join(skills)}")
    return skills[skill]
