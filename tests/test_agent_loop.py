"""Kiểm tra vòng lặp agent với model giả (không gọi API thật)."""

import asyncio
from types import SimpleNamespace

from botai.agent import Team
from botai.config import AgentConfig

from .test_core import make_ctx, make_settings


class FakeBlock(SimpleNamespace):
    pass


class FakeAnthropicMessages:
    """Lượt 1: gọi write_text_file + delegate. Lượt 2: trả lời chữ."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def create(self, **params):
        self.calls.append(params)
        content, stop = self.script.pop(0)
        return SimpleNamespace(content=content, stop_reason=stop, usage=SimpleNamespace(input_tokens=10, output_tokens=5))


def cfg(name, **kw):
    return AgentConfig(name=name, display_name=name.upper(), telegram_token_env="X", provider="anthropic",
                       model="claude-haiku-5-5", role="r", fallbacks=False, **kw)


def test_tool_loop_and_delegate(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    settings = make_settings(tmp_path)
    team = Team([cfg("boss", delegate_to=["worker"]), cfg("worker")], settings)
    boss, worker = team.agents["boss"], team.agents["worker"]

    boss_msgs = FakeAnthropicMessages([
        ([FakeBlock(type="tool_use", id="t1", name="write_text_file", input={"path": "note.txt", "content": "xin chào"}),
          FakeBlock(type="tool_use", id="t2", name="delegate", input={"agent": "worker", "task": "đọc note.txt"})], "tool_use"),
        ([FakeBlock(type="text", text="Xong hết rồi")], "end_turn"),
    ])
    worker_msgs = FakeAnthropicMessages([
        ([FakeBlock(type="tool_use", id="w1", name="read_text_file", input={"path": "note.txt"})], "tool_use"),
        ([FakeBlock(type="text", text="Nội dung là xin chào")], "end_turn"),
    ])
    boss.llm.client = SimpleNamespace(messages=boss_msgs)
    worker.llm.client = SimpleNamespace(messages=worker_msgs)

    ctx = make_ctx(tmp_path)
    ctx.workdir.mkdir(exist_ok=True)
    session = boss.session("chat")
    session.pending.append("nhân tiện, thêm logo nhé")
    answer = asyncio.run(boss.handle("chat", "làm việc đi", [], ctx))

    assert answer == "Xong hết rồi"
    assert (ctx.workdir / "note.txt").read_text(encoding="utf-8") == "xin chào"
    # kết quả tool + tin nhắn chen ngang được gửi lại cho model trong cùng 1 message
    tool_msg = boss_msgs.calls[1]["messages"][2]["content"]  # user, assistant, tool_results, ...
    assert [b["type"] for b in tool_msg] == ["tool_result", "tool_result", "text"]
    assert "Nội dung là xin chào" in tool_msg[1]["content"][0]["text"]
    assert "thêm logo" in tool_msg[2]["text"]
    assert session.usage.input_tokens == 20


def test_unknown_tool_and_bad_args(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    team = Team([cfg("a")], make_settings(tmp_path))
    a = team.agents["a"]
    ctx = make_ctx(tmp_path)
    r1 = asyncio.run(a.execute(ctx, "khong_co", {}))
    r2 = asyncio.run(a.execute(ctx, "read_text_file", {"path": "x", "bogus": 1}))
    r3 = asyncio.run(a.execute(ctx, "read_text_file", {"path": "khong_ton_tai.txt"}))
    assert r1.is_error and r2.is_error and r3.is_error
    assert "Không tìm thấy" in r3.text
