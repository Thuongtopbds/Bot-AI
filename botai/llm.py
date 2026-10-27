"""Kết nối model AI và vòng lặp gọi tool (agent loop).

- provider "anthropic": Claude (Opus 5.5, Sonnet 5.5, ...) qua SDK chính thức `anthropic`.
- provider "openai":    các model GPT qua SDK `openai`.
- provider "deepseek":  DeepSeek (API tương thích OpenAI) qua SDK `openai`.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from .config import AgentConfig
from .tools import Tool, ToolResult

log = logging.getLogger(__name__)

Execute = Callable[[str, dict], Awaitable[ToolResult]]
DrainPending = Callable[[], list[str]]

# Các model hỗ trợ tham số `fallbacks: "default"` (server-side fallback khi bị refusal).
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}

PENDING_PREFIX ="[Tin nhắn mới của người dùng gửi trong lúc bạn đang làm — hãy cân nhắc và áp dụng]\n"


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, i: int | None, o: int | None) -> None:
        self.input_tokens += i or 0
        self.output_tokens += o or 0


def _b64(path: Path) -> tuple[str, str]:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return mime, base64.standard_b64encode(path.read_bytes()).decode()


class AnthropicLLM:
    REFUSAL_TEXT = "Model đã từ chối yêu cầu này (stop_reason=refusal). Hãy diễn đạt lại yêu cầu."

    def __init__(self, cfg: AgentConfig):
        import anthropic

        self.cfg = cfg
        self.client = anthropic.AsyncAnthropic()

    def user_message(self, text: str, images: list[Path]) -> dict:
        content: list[dict] = []
        for img in images:
            mime, data = _b64(img)
            content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
        content.append({"type": "text", "text": text})
        return {"role": "user", "content": content}

    def _tools(self, tools: list[Tool]) -> list[dict]:
        return [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]

    async def _create(self, **params):
        if self.cfg.effort:
            params["output_config"] = {"effort": self.cfg.effort}
        if self.cfg.fallbacks and self.cfg.model in FALLBACK_MODELS:
            # Khi model chính từ chối (refusal), máy chủ tự chuyển sang model dự phòng phù hợp.
            return await self.client.beta.messages.create(
                **params, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
            )
        return await self.client.messages.create(**params)

    async def run(self, system: str, history: list, tools: list[Tool], execute: Execute, drain: DrainPending, usage: Usage) -> str:
        tool_defs = self._tools(tools)
        for _ in range(self.cfg.max_steps):
            resp = await self._create(
                model=self.cfg.model,
                max_tokens=self.cfg.max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                tools=tool_defs,
                messages=history,
            )
            usage.add(resp.usage.input_tokens, resp.usage.output_tokens)
            history.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason == "refusal":
                return self.REFUSAL_TEXT
            if resp.stop_reason == "pause_turn":
                continue
            calls = [b for b in resp.content if b.type == "tool_use"]
            text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
            if not calls:
                if resp.stop_reason == "max_tokens":
                    text += "\n(…câu trả lời bị cắt do giới hạn độ dài)"
                pending = drain()
                if pending:
                    history.append(self.user_message(PENDING_PREFIX + "\n".join(pending), []))
                    continue
                return text
            results = await asyncio.gather(*(execute(c.name, dict(c.input or {})) for c in calls))
            blocks: list[dict] = []
            for call, res in zip(calls, results):
                inner: list[dict] = [{"type": "text", "text": res.text or "(trống)"}]
                for img in res.images:
                    mime, data = _b64(img)
                    inner.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
                blocks.append({"type": "tool_result", "tool_use_id": call.id, "content": inner, "is_error": res.is_error})
            pending = drain()
            if pending:
                blocks.append({"type": "text", "text": PENDING_PREFIX + "\n".join(pending)})
            history.append({"role": "user", "content": blocks})
        return "Đã dừng vì vượt quá số bước cho phép (max_steps). Hãy nhắn 'tiếp tục' nếu muốn làm tiếp."


class OpenAICompatLLM:
    def __init__(self, cfg: AgentConfig):
        from openai import AsyncOpenAI

        self.cfg = cfg
        if cfg.provider == "deepseek":
            self.client = AsyncOpenAI(api_key=os.environ.get("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com")
        else:
            self.client = AsyncOpenAI(base_url=os.environ.get("OPENAI_BASE_URL") or None)

    def user_message(self, text: str, images: list[Path]) -> dict:
        if not images or not self.cfg.vision:
            return {"role": "user", "content": text}
        parts: list[dict] = []
        for img in images:
            mime, data = _b64(img)
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
        parts.append({"type": "text", "text": text})
        return {"role": "user", "content": parts}

    def _tools(self, tools: list[Tool]) -> list[dict]:
        return [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}} for t in tools]

    async def run(self, system: str, history: list, tools: list[Tool], execute: Execute, drain: DrainPending, usage: Usage) -> str:
        tool_defs = self._tools(tools)
        for _ in range(self.cfg.max_steps):
            resp = await self.client.chat.completions.create(
                model=self.cfg.model,
                messages=[{"role": "system", "content": system}, *history],
                tools=tool_defs,
            )
            if resp.usage:
                usage.add(resp.usage.prompt_tokens, resp.usage.completion_tokens)
            msg = resp.choices[0].message
            entry: dict = {"role": "assistant", "content": msg.content or ""}
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ]
            history.append(entry)
            if not msg.tool_calls:
                pending = drain()
                if pending:
                    history.append(self.user_message(PENDING_PREFIX + "\n".join(pending), []))
                    continue
                return (msg.content or "").strip()

            async def one(tc):
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError as e:
                    return ToolResult(f"Tham số JSON không hợp lệ: {e}", is_error=True)
                return await execute(tc.function.name, args)

            results = await asyncio.gather(*(one(tc) for tc in msg.tool_calls))
            images: list[Path] = []
            for tc, res in zip(msg.tool_calls, results):
                prefix = "LỖI: " if res.is_error else ""
                history.append({"role": "tool", "tool_call_id": tc.id, "content": prefix + (res.text or "(trống)")})
                images.extend(res.images)
            pending = drain()
            if images and self.cfg.vision:
                history.append(self.user_message("Ảnh từ kết quả công cụ ở trên.", images))
            if pending:
                history.append(self.user_message(PENDING_PREFIX + "\n".join(pending), []))
        return "Đã dừng vì vượt quá số bước cho phép (max_steps). Hãy nhắn 'tiếp tục' nếu muốn làm tiếp."


def make_llm(cfg: AgentConfig):
    if cfg.provider == "anthropic":
        return AnthropicLLM(cfg)
    if cfg.provider in {"openai", "deepseek"}:
        return OpenAICompatLLM(cfg)
    raise ValueError(f"provider không hỗ trợ: {cfg.provider} (dùng anthropic | openai | deepseek)")
