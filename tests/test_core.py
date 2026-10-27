import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from botai.auth import AccessControl
from botai.config import ROOT, AgentConfig, Settings, load_agents
from botai.telegram_bot import split_text
from botai.tools import ToolContext, ToolError
from botai.tools.facebook import parse_pages
from botai.tools.video import render_video, segments_to_srt, strip_metadata


def make_settings(tmp: Path) -> Settings:
    s = Settings(
        data_dir=tmp / "data", admin_ids={1}, admin_chat_id=1, telegram_api_base=None,
        fb_graph_version="v23.0", fb_watch_interval=0, fb_watch_agent=None, fb_auto_reply=False, max_upload_mb=20,
    )
    s.ensure_dirs()
    return s


def make_ctx(tmp: Path, sent: list | None = None) -> ToolContext:
    work = tmp / "work"
    work.mkdir(exist_ok=True)

    async def send_file(p, c):
        (sent if sent is not None else []).append((p, c))

    async def notify(t):
        pass

    cfg = AgentConfig(name="t", display_name="T", telegram_token_env="X", provider="anthropic", model="m", role="r")
    return ToolContext(agent=cfg, settings=make_settings(tmp), chat_id=1, workdir=work, send_file=send_file, notify=notify)


def test_example_config_loads():
    agents = load_agents(ROOT / "config" / "agents.example.yaml")
    names = [a.name for a in agents]
    assert names[0] == "dieu_phoi"
    assert set(agents[0].delegate_to) <= set(names)
    assert agents[1].allow_shell is True


def test_pairing_flow(tmp_path):
    ac = AccessControl(tmp_path, admin_ids={42})
    assert ac.is_allowed(42) and not ac.is_allowed(7)
    code = ac.request_code(7, "Khách")
    assert ac.request_code(7, "Khách") == code  # cùng người -> cùng mã
    assert ac.approve("sai") is None
    assert ac.approve(code.lower())["user_id"] == 7
    assert ac.is_allowed(7)
    assert ac.revoke(7) and not ac.is_allowed(7)


def test_parse_pages():
    pages = parse_pages("# comment\nPage A | 111 | tokA\n222\ttokB\n")
    assert [(p.name, p.page_id, p.token) for p in pages] == [("Page A", "111", "tokA"), ("222", "222", "tokB")]
    with pytest.raises(ValueError):
        parse_pages("chi co mot cot")


def test_split_text():
    chunks = split_text("a" * 9000)
    assert [len(c) for c in chunks] == [4000, 4000, 1000]


def test_srt():
    srt = segments_to_srt([(0, 1.5, "Xin chào"), (61.2, 62, "Tạm biệt")])
    assert "00:00:00,000 --> 00:00:01,500" in srt and "00:01:01,200" in srt


def test_sandbox_blocks_escape(tmp_path):
    ctx = make_ctx(tmp_path)
    with pytest.raises(ToolError):
        ctx.resolve("../../etc/passwd", must_exist=False)
    with pytest.raises(ToolError):
        ctx.resolve(str(tmp_path / "data" / "secrets" / "x"), must_exist=False)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="cần ffmpeg")
def test_render_video_end_to_end(tmp_path):
    ctx = make_ctx(tmp_path)
    w = ctx.workdir
    # 2 clip mẫu: một có tiếng, một không tiếng; 1 ảnh; 1 file lồng tiếng
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-metadata", "title=SECRET", "-shortest", str(w / "a.mp4")], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=30:duration=2", str(w / "b.mp4")], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:size=400x400", "-frames:v", "1", str(w / "map.png")], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=4", str(w / "voice.mp3")], check=True)
    (w / "sub.srt").write_text(segments_to_srt([(0, 2, "Nhà đẹp")]), encoding="utf-8")

    msg = asyncio.run(render_video.func(
        ctx,
        clips=[{"path": "a.mp4", "start": 0.5, "end": 2.5}, {"path": "map.png", "duration": 2}, {"path": "b.mp4"}],
        output="out/final.mp4", width=540, height=960, voiceover="voice.mp3", music="voice.mp3",
        subtitles="sub.srt", overlays=[{"image": "map.png", "start": 0, "end": 1, "x": 10, "y": 10, "width": 100}],
    ))
    out = w / "out" / "final.mp4"
    assert out.exists(), msg
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags",
                            "-show_entries", "stream=width,height", "-of", "default=nw=1", str(out)], capture_output=True, text=True).stdout
    assert "width=540" in probe and "height=960" in probe
    assert "SECRET" not in probe
    duration = float([l for l in probe.splitlines() if l.startswith("duration=")][0].split("=")[1])
    assert 5.5 < duration < 6.5

    asyncio.run(strip_metadata.func(ctx, "a.mp4", "clean.mp4"))
    tags = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags", str(w / "clean.mp4")], capture_output=True, text=True).stdout
    assert "SECRET" not in tags
