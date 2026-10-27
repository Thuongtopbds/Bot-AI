# Bot-AI – Đội trợ lý AI 24/7 trên Telegram

Điều khiển bằng điện thoại một "đội" bot AI chạy trên máy của bạn (local hoặc VPS):

- 🎬 **Trợ lý Video** – đọc video, chọn cảnh, dịch lời thoại (Whisper), lồng tiếng AI tiếng Việt, chèn bản đồ
  vị trí + tiện ích, phụ đề, nhạc nền, xóa metadata, xuất video dọc Reels/TikTok.
- 📣 **Trợ lý Mạng xã hội** – đăng bài/video hàng loạt lên nhiều fanpage, theo dõi và trả lời bình luận
  ("xin giá") dựa trên FAQ của bạn.
- 🧠 **Trợ lý trưởng** (Claude Opus 5.5) – nhận việc, lập kế hoạch, giao cho trợ lý chuyên môn, báo kết quả.
- 📝 Văn phòng, 📰 Tin tức… thêm trợ lý mới chỉ bằng vài dòng YAML.
- Tự sửa lỗi + **ghi nhớ bài học**; mã kết nối để duyệt người dùng; chạy trong group Telegram.

Model: Claude (Anthropic), OpenAI hoặc DeepSeek – chọn riêng cho từng trợ lý.

## Bắt đầu nhanh

```bash
git clone https://github.com/Thuongtopbds/Bot-AI.git && cd Bot-AI
cp .env.example .env            # điền ANTHROPIC_API_KEY, token BotFather, ADMIN_TELEGRAM_IDS
./run.sh                        # Windows: run.bat
```

VPS: `bash deploy/install.sh` (systemd) hoặc `docker compose up -d`.

👉 **Hướng dẫn chi tiết từng bước (tiếng Việt): [docs/HUONG_DAN.md](docs/HUONG_DAN.md)**

## Phát triển

```bash
pip install -r requirements-dev.txt
pytest
```
