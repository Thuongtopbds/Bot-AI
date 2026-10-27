"""Kỹ năng video: đọc video, nhận dạng giọng nói, lồng tiếng AI, bản đồ vị trí, dựng & xuất video.

Mọi xử lý nặng chạy bằng ffmpeg trên máy bạn (local/VPS), AI chỉ lên kế hoạch và gọi các tool này.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from . import ToolContext, ToolError, ToolResult, run_cmd, tool

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def _need(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise ToolError(f"Chưa cài {binary}. Cài bằng: sudo apt install -y ffmpeg (Linux) / brew install ffmpeg (Mac)")
    return path


async def _probe(path: Path) -> dict:
    code, out, err = await run_cmd(
        [_need("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        timeout=120,
    )
    if code != 0:
        raise ToolError(f"ffprobe lỗi: {err[-1500:]}")
    return json.loads(out)


async def _ffmpeg(args: list[str], cwd: Path, timeout: float = 3600) -> None:
    code, _, err = await run_cmd([_need("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", *args], cwd=cwd, timeout=timeout)
    if code != 0:
        raise ToolError(f"ffmpeg lỗi (exit {code}):\n{err[-3000:]}")


def _duration(info: dict) -> float:
    return float(info.get("format", {}).get("duration") or 0)


@tool(
    "probe_video",
    "Xem thông tin video/âm thanh: thời lượng, độ phân giải, có tiếng hay không.",
    {"path": {"type": "string"}},
    ["path"],
)
async def probe_video(ctx: ToolContext, path: str) -> str:
    p = ctx.resolve(path)
    info = await _probe(p)
    summary = {"file": ctx.rel(p), "duration_s": round(_duration(info), 2), "size_mb": round(p.stat().st_size / 1_048_576, 2)}
    for s in info.get("streams", []):
        if s.get("codec_type") == "video" and "video" not in summary:
            summary["video"] = {"w": s.get("width"), "h": s.get("height"), "codec": s.get("codec_name"), "fps": s.get("avg_frame_rate")}
        if s.get("codec_type") == "audio" and "audio" not in summary:
            summary["audio"] = {"codec": s.get("codec_name"), "channels": s.get("channels")}
    return json.dumps(summary, ensure_ascii=False)


@tool(
    "extract_frames",
    "Cắt vài khung hình đều nhau từ video để AI NHÌN nội dung (cảnh nào đẹp, có lỗi gì).",
    {"path": {"type": "string"}, "count": {"type": "integer", "description": "1-8, mặc định 4"}},
    ["path"],
)
async def extract_frames(ctx: ToolContext, path: str, count: int = 4) -> ToolResult:
    p = ctx.resolve(path)
    count = max(1, min(int(count), 8))
    dur = _duration(await _probe(p)) or 1
    out_dir = ctx.workdir / "frames"
    out_dir.mkdir(exist_ok=True)
    frames, lines = [], []
    for i in range(count):
        t = dur * (i + 0.5) / count
        out = out_dir / f"{p.stem}_{i + 1}.jpg"
        await _ffmpeg(["-ss", f"{t:.2f}", "-i", str(p), "-frames:v", "1", "-vf", "scale=640:-2", str(out)], cwd=ctx.workdir, timeout=120)
        frames.append(out)
        lines.append(f"Khung {i + 1}: giây {t:.1f} -> {ctx.rel(out)}")
    if not ctx.agent.vision:
        return ToolResult("\n".join(lines) + "\n(Model hiện tại không xem được ảnh)")
    return ToolResult("\n".join(lines), images=frames)


_whisper_cache: dict[str, object] = {}


@tool(
    "transcribe",
    "Nhận dạng lời nói trong video/âm thanh (faster-whisper, chạy trên máy). Trả về lời thoại có mốc thời gian "
    "và lưu file .srt. Dùng để dịch thuật hoặc làm phụ đề.",
    {
        "path": {"type": "string"},
        "language": {"type": "string", "description": "Mã ngôn ngữ nguồn, ví dụ 'zh', 'en', 'vi'. Bỏ trống để tự nhận"},
        "model_size": {"type": "string", "description": "tiny|base|small|medium|large-v3, mặc định small"},
    },
    ["path"],
)
async def transcribe(ctx: ToolContext, path: str, language: str | None = None, model_size: str = "small") -> str:
    p = ctx.resolve(path)
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:  # pragma: no cover - phụ thuộc môi trường
        raise ToolError(f"Chưa cài faster-whisper ({e}). Chạy: pip install faster-whisper")

    def work():
        model = _whisper_cache.get(model_size)
        if model is None:
            model = WhisperModel(model_size, device="auto", compute_type="int8")
            _whisper_cache[model_size] = model
        segments, info = model.transcribe(str(p), language=language or None, vad_filter=True)
        return list(segments), info

    segments, info = await asyncio.to_thread(work)
    srt_path = p.with_suffix(".srt")
    srt_path.write_text(segments_to_srt([(s.start, s.end, s.text.strip()) for s in segments]), encoding="utf-8")
    lines = [f"[{s.start:.1f}-{s.end:.1f}] {s.text.strip()}" for s in segments]
    head = f"Ngôn ngữ: {info.language} ({info.language_probability:.0%}). SRT: {ctx.rel(srt_path)}\n"
    return head + ("\n".join(lines) or "(không có lời nói)")


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def segments_to_srt(segments: list[tuple[float, float, str]]) -> str:
    blocks = [f"{i}\n{_ts(a)} --> {_ts(b)}\n{t}\n" for i, (a, b, t) in enumerate(segments, 1) if t]
    return "\n".join(blocks)


@tool(
    "text_to_speech",
    "Lồng tiếng AI: đọc văn bản thành file mp3 (Microsoft Edge TTS, miễn phí). Giọng Việt: "
    "vi-VN-HoaiMyNeural (nữ), vi-VN-NamMinhNeural (nam). Trả về thời lượng để canh khớp video.",
    {
        "text": {"type": "string"},
        "output": {"type": "string", "description": "Tên file .mp3, ví dụ voice.mp3"},
        "voice": {"type": "string"},
        "rate": {"type": "string", "description": "Tốc độ, ví dụ '+10%' hoặc '-5%'"},
    },
    ["text", "output"],
)
async def text_to_speech(ctx: ToolContext, text: str, output: str, voice: str = "vi-VN-HoaiMyNeural", rate: str = "+0%") -> str:
    try:
        import edge_tts
    except ImportError:  # pragma: no cover
        raise ToolError("Chưa cài edge-tts. Chạy: pip install edge-tts")
    out = ctx.resolve(output, must_exist=False)
    out.parent.mkdir(parents=True, exist_ok=True)
    await edge_tts.Communicate(text, voice, rate=rate).save(str(out))
    dur = _duration(await _probe(out))
    return f"Đã tạo {ctx.rel(out)} dài {dur:.1f} giây"


@tool(
    "make_map_image",
    "Vẽ ảnh bản đồ (OpenStreetMap) đánh dấu vị trí lô đất và các tiện ích xung quanh, có chú thích. "
    "Dùng làm cảnh 'map effect' trong video.",
    {
        "lat": {"type": "number"},
        "lon": {"type": "number"},
        "title": {"type": "string", "description": "Tiêu đề trên ảnh, ví dụ 'Vị trí lô đất'"},
        "places": {
            "type": "array",
            "description": "Tiện ích xung quanh",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "lat": {"type": "number"}, "lon": {"type": "number"}},
                "required": ["name", "lat", "lon"],
            },
        },
        "output": {"type": "string", "description": "Tên file .png, mặc định map.png"},
        "width": {"type": "integer"},
        "height": {"type": "integer"},
        "zoom": {"type": "integer", "description": "Mức zoom 12-18, bỏ trống để tự chọn"},
    },
    ["lat", "lon"],
)
async def make_map_image(
    ctx: ToolContext,
    lat: float,
    lon: float,
    title: str = "Vị trí",
    places: list[dict] | None = None,
    output: str = "map.png",
    width: int = 1080,
    height: int = 1080,
    zoom: int | None = None,
) -> str:
    out = ctx.resolve(output, must_exist=False)
    places = places or []
    await asyncio.to_thread(_draw_map, out, lat, lon, title, places, width, height, zoom)
    return f"Đã tạo bản đồ {ctx.rel(out)} ({width}x{height}) với {len(places)} tiện ích"


def _draw_map(out: Path, lat, lon, title, places, width, height, zoom) -> None:
    from PIL import Image, ImageDraw, ImageFont
    from staticmap import CircleMarker, StaticMap

    m = StaticMap(width, height, padding_x=80, padding_y=120, headers={"User-Agent": "BotAI-RealEstate/1.0"})
    for i, pl in enumerate(places, 1):
        m.add_marker(CircleMarker((pl["lon"], pl["lat"]), "white", 34))
        m.add_marker(CircleMarker((pl["lon"], pl["lat"]), "#1e66f5", 26))
    m.add_marker(CircleMarker((lon, lat), "white", 46))
    m.add_marker(CircleMarker((lon, lat), "#e64553", 36))
    img = m.render(zoom=zoom) if zoom else m.render()
    img = img.convert("RGB")
    draw = ImageDraw.Draw(img)

    def font(size):
        for name in ("DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "Arial.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    def to_px(plat, plon):
        return int(m._x_to_px(_lon_to_x(plon, m.zoom))), int(m._y_to_px(_lat_to_y(plat, m.zoom)))

    f_num, f_title, f_leg = font(22), font(46), font(30)
    for i, pl in enumerate(places, 1):
        x, y = to_px(pl["lat"], pl["lon"])
        draw.text((x, y), str(i), fill="white", font=f_num, anchor="mm")
    # tiêu đề
    draw.rectangle([0, 0, width, 90], fill=(0, 0, 0))
    draw.text((width // 2, 45), title, fill="white", font=f_title, anchor="mm")
    # chú thích
    if places:
        box_h = 20 + 42 * len(places)
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rectangle([0, height - box_h, width, height], fill=(0, 0, 0, 170))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)
        for i, pl in enumerate(places, 1):
            draw.text((24, height - box_h + 10 + 42 * (i - 1)), f"{i}. {pl['name']}", fill="white", font=f_leg)
    # Điều khoản sử dụng OpenStreetMap yêu cầu ghi công
    draw.text((width - 10, 100), "© OpenStreetMap contributors", fill=(60, 60, 60), font=font(16), anchor="ra")
    img.save(out)


def _lon_to_x(lon, zoom):
    from staticmap.staticmap import _lon_to_x as f

    return f(lon, zoom)


def _lat_to_y(lat, zoom):
    from staticmap.staticmap import _lat_to_y as f

    return f(lat, zoom)


RENDER_DESC = """Dựng video hoàn chỉnh theo 'kế hoạch dựng' (JSON). Các bước: cắt từng đoạn, đưa về cùng khung hình,
nối lại, chèn ảnh (bản đồ/logo) theo thời gian, lồng tiếng + nhạc nền, phụ đề, rồi XÓA METADATA.
- clips: danh sách đoạn theo thứ tự. Đoạn video: {"path","start","end"} (giây, tùy chọn).
  Đoạn ảnh (ví dụ bản đồ, có hiệu ứng zoom chậm): {"path":"map.png","duration":4}.
- overlays: ảnh đè lên video: {"image","start","end","x","y","width"} (x,y pixel hoặc 'center').
- voiceover: file mp3 lồng tiếng; keep_original_audio=false để tắt tiếng gốc (khi dịch sang tiếng Việt).
- subtitles: file .srt để in phụ đề lên video.
Mặc định khung dọc 1080x1920 (Reels/TikTok)."""


@tool(
    "render_video",
    RENDER_DESC,
    {
        "clips": {"type": "array", "items": {"type": "object"}},
        "output": {"type": "string", "description": "Tên file kết quả, ví dụ final.mp4"},
        "width": {"type": "integer"},
        "height": {"type": "integer"},
        "fps": {"type": "integer"},
        "keep_original_audio": {"type": "boolean"},
        "original_volume": {"type": "number"},
        "voiceover": {"type": "string"},
        "voiceover_volume": {"type": "number"},
        "music": {"type": "string"},
        "music_volume": {"type": "number"},
        "subtitles": {"type": "string"},
        "overlays": {"type": "array", "items": {"type": "object"}},
    },
    ["clips", "output"],
)
async def render_video(
    ctx: ToolContext,
    clips: list[dict],
    output: str,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    keep_original_audio: bool = True,
    original_volume: float = 1.0,
    voiceover: str | None = None,
    voiceover_volume: float = 1.0,
    music: str | None = None,
    music_volume: float = 0.15,
    subtitles: str | None = None,
    overlays: list[dict] | None = None,
) -> str:
    if not clips:
        raise ToolError("Cần ít nhất 1 clip")
    tmp = ctx.workdir / "_render"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    fit = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps},format=yuv420p"
    )
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-ar", "44100", "-ac", "2"]

    parts: list[Path] = []
    for i, c in enumerate(clips):
        src = ctx.resolve(str(c.get("path", "")))
        part = tmp / f"part{i:03}.mp4"
        if src.suffix.lower() in IMAGE_EXT:
            dur = float(c.get("duration", 4))
            frames = max(1, int(dur * fps))
            zoom = (
                f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=decrease,"
                f"pad={width * 2}:{height * 2}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"zoompan=z='min(zoom+0.0008,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={width}x{height}:fps={fps},"
                "setsar=1,format=yuv420p"
            )
            await _ffmpeg(
                ["-loop", "1", "-i", str(src), "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                 "-t", f"{dur}", "-vf", zoom, *enc, "-shortest", str(part)],
                cwd=ctx.workdir,
            )
        else:
            info = await _probe(src)
            has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
            cut = []
            if c.get("start") is not None:
                cut += ["-ss", str(c["start"])]
            if c.get("end") is not None:
                cut += ["-to", str(c["end"])]
            args = [*cut, "-i", str(src)]
            if not has_audio:
                args += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-shortest"]
            args += ["-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0", "-vf", fit, *enc, str(part)]
            await _ffmpeg(args, cwd=ctx.workdir)
        parts.append(part)

    concat_list = tmp / "list.txt"
    concat_list.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    joined = tmp / "joined.mp4"
    await _ffmpeg(["-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "joined.mp4"], cwd=tmp)
    total = _duration(await _probe(joined))

    # --- ghép lớp cuối: overlay ảnh, phụ đề, âm thanh -------------------------
    inputs = ["-i", str(joined)]
    filters: list[str] = []
    v = "[0:v]"
    idx = 1
    for j, ov in enumerate(overlays or []):
        img = ctx.resolve(str(ov.get("image", "")))
        inputs += ["-i", str(img)]
        w = int(ov.get("width", width // 2))
        x = "(W-w)/2" if ov.get("x", "center") == "center" else str(int(ov["x"]))
        y = "(H-h)/2" if ov.get("y", "center") == "center" else str(int(ov["y"]))
        s, e = float(ov.get("start", 0)), float(ov.get("end", total))
        filters.append(f"[{idx}:v]scale={w}:-1[ov{j}]")
        filters.append(f"{v}[ov{j}]overlay={x}:{y}:enable='between(t,{s},{e})'[v{j}]")
        v = f"[v{j}]"
        idx += 1
    if subtitles:
        srt = ctx.resolve(subtitles)
        shutil.copy(srt, tmp / "subs.srt")
        style = "FontName=DejaVu Sans,FontSize=13,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,MarginV=60"
        filters.append(f"{v}subtitles=subs.srt:force_style='{style}'[vsub]")
        v = "[vsub]"

    audio_labels = []
    if keep_original_audio:
        filters.append(f"[0:a]volume={original_volume}[a0]")
        audio_labels.append("[a0]")
    if voiceover:
        inputs += ["-i", str(ctx.resolve(voiceover))]
        filters.append(f"[{idx}:a]volume={voiceover_volume}[avo]")
        audio_labels.append("[avo]")
        idx += 1
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(ctx.resolve(music))]
        filters.append(f"[{idx}:a]volume={music_volume}[amu]")
        audio_labels.append("[amu]")
        idx += 1
    if audio_labels:
        filters.append(f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:duration=first:normalize=0[aout]")
        a_map = "[aout]"
    else:
        a_map = None

    out = ctx.resolve(output, must_exist=False)
    out.parent.mkdir(parents=True, exist_ok=True)
    args = list(inputs)
    if filters:
        if v == "[0:v]":
            filters.append("[0:v]null[vout]")
            v = "[vout]"
        args += ["-filter_complex", ";".join(filters), "-map", v]
    else:
        args += ["-map", "0:v"]
    if a_map:
        args += ["-map", a_map]
    args += ["-t", f"{total:.3f}", *enc, "-movflags", "+faststart", "-map_metadata", "-1",
             "-metadata", "title=", "-metadata", "encoder=", str(out)]
    await _ffmpeg(args, cwd=tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    return f"Xong: {ctx.rel(out)} — {total:.1f} giây, {out.stat().st_size / 1_048_576:.1f} MB (đã xóa metadata)"


@tool(
    "strip_metadata",
    "Xóa toàn bộ metadata (thiết bị quay, GPS, phần mềm...) khỏi video/ảnh mà không nén lại.",
    {"path": {"type": "string"}, "output": {"type": "string"}},
    ["path", "output"],
)
async def strip_metadata(ctx: ToolContext, path: str, output: str) -> str:
    src = ctx.resolve(path)
    out = ctx.resolve(output, must_exist=False)
    await _ffmpeg(["-i", str(src), "-map", "0", "-map_metadata", "-1", "-c", "copy", str(out)], cwd=ctx.workdir)
    return f"Đã xóa metadata -> {ctx.rel(out)}"


TOOLS = [probe_video, extract_frames, transcribe, text_to_speech, make_map_image, render_video, strip_metadata]
