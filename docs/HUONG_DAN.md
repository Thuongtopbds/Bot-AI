# Hướng dẫn xây dựng "Đội trợ lý AI" trên Telegram (chạy local → GitHub → VPS)

Tài liệu này dựa trên video **"Cách Tạo Bot Trợ Lý AI Trên Điện Thoại Làm Việc 24/24: Tự Động Edit Video & Đăng Bài Facebook"**
(kênh Revid API) và hướng dẫn bạn tự dựng một hệ thống **tương đương bằng mã nguồn mở** trong repo này.
Bạn không phụ thuộc vào web trả phí nào: mọi thứ chạy trên máy của bạn, chỉ trả tiền API model AI.

---

## 1. Video làm gì? (tóm tắt phân tích)

| Mốc | Nội dung video | Trong repo này |
|---|---|---|
| 00:00 | Một "đội trợ lý" trên Telegram: video, mạng xã hội, văn phòng, code, tin tức. Chạy trên VPS, điều khiển bằng điện thoại. Dùng Telegram thay vì Zalo vì bảo mật tốt hơn. | 5 trợ lý mẫu trong `config/agents.example.yaml`, mỗi trợ lý là 1 bot Telegram |
| 00:46 | Gửi 3 video review nhà đất + yêu cầu. AI đọc từng video, đưa kế hoạch + **ước tính chi phí AI**, hỏi **vị trí lô đất** và tiện ích xung quanh | Tool `probe_video`, `extract_frames` (AI *nhìn* khung hình); quy tắc "báo kế hoạch + chi phí, thiếu thông tin thì hỏi"; nhận **location** Telegram |
| 01:27 | Đang làm thì nhắn thêm: "dịch video sang tiếng Việt, xóa metadata, cắt ghép" | Tin nhắn gửi trong lúc bot đang làm được **chen vào việc đang chạy** |
| 01:41 | Gặp lỗi thư viện (PyAV không hợp faster-whisper) → AI **tự hạ phiên bản** và sửa | Tool `run_shell` (bật bằng `allow_shell: true`) + quy tắc tự sửa lỗi |
| 02:03 | Lưu lại kinh nghiệm, "có bộ não", lần sau không lặp lỗi | Tool `save_lesson` → `data/memory/<trợ lý>.md`, tự nạp vào mọi lần sau |
| 02:16–03:15 | Video thành phẩm: lồng tiếng AI, chèn **map effect** vị trí + tiện ích | `transcribe` (faster-whisper), `text_to_speech` (giọng Việt miễn phí), `make_map_image` (OpenStreetMap), `render_video` |
| 03:21 | Gửi file TXT gồm page ID + access token (lưu trên máy, không lên web) → tự đăng hàng loạt, trả link | `fb_import_pages` (AI không bao giờ thấy token), `fb_post` |
| 03:49 | Khách bình luận "xin giá" → trợ lý mạng xã hội báo cáo + trả lời; có thể cho học **bộ FAQ** | `comment_watcher.py` + `knowledge_files: [faq.md]`, chế độ tự trả lời hoặc chờ duyệt |
| 04:20 | Tạo bot bằng **BotFather**, mỗi trợ lý một token; trợ lý trưởng dùng **Opus 5.5**, trợ lý khác dùng model rẻ (DeepSeek, GPT) | Mỗi trợ lý chọn `provider`/`model` riêng; trợ lý trưởng **giao việc** (`delegate`) cho trợ lý khác |
| 06:17 | Thêm cả 6 bot vào **một group Telegram**, giao việc từng con | Hỗ trợ group: bot chỉ trả lời khi được **@nhắc tên** hoặc **reply** |
| 06:40 | `ssh root@IP` → dán 1 lệnh cài đặt → chạy 24/7 | `deploy/install.sh` + dịch vụ systemd (hoặc Docker) |
| 07:15 | Nhắn "xin chào" → bot trả **mã kết nối** → "duyệt mã" | Cơ chế mã kết nối: `python -m botai approve <MÃ>` hoặc `/approve <MÃ>` |
| 08:00 | Không có VPS thì chạy thử trên Mac/Windows, nhưng máy phải bật 24/24 | `run.sh` / `run.bat` |

---

## 2. Kiến trúc

```
Điện thoại (Telegram)
   │  tin nhắn, video, ảnh, vị trí, file .txt
   ▼
┌──────────────────────── Máy của bạn (local / VPS) ────────────────────────┐
│ python -m botai                                                            │
│  ├─ Bot "Bé Na – Trợ lý trưởng" (Claude Opus 5.5) ──delegate──┐            │
│  ├─ Bot "Trợ lý Video"  ── ffmpeg, faster-whisper, edge-tts, bản đồ OSM    │
│  ├─ Bot "Trợ lý Mạng xã hội" ── Facebook Graph API (đăng bài, bình luận)   │
│  ├─ Bot "Văn phòng", "Tin tức"                                             │
│  ├─ Theo dõi bình luận Facebook (định kỳ)                                  │
│  └─ data/  (work/ file làm việc · memory/ bài học · secrets/ token page)   │
└────────────────────────────────────────────────────────────────────────────┘
          │ chỉ gửi văn bản/ảnh khung hình tới API model AI
          ▼
   Anthropic (Claude) / OpenAI / DeepSeek
```

Cấu trúc thư mục:

```
botai/
  __main__.py         # chạy bot, lệnh approve/users/revoke/check
  config.py           # đọc .env và config/agents.yaml
  auth.py             # mã kết nối, danh sách người được phép
  llm.py              # gọi Claude / OpenAI / DeepSeek + vòng lặp gọi tool
  agent.py            # trợ lý: vai trò, bộ nhớ, giao việc
  telegram_bot.py     # mỗi trợ lý = 1 bot Telegram
  comment_watcher.py  # theo dõi bình luận fanpage
  tools/
    common.py         # file, gửi file, báo tiến độ, ghi nhớ, shell
    video.py          # đọc video, khung hình, whisper, lồng tiếng, bản đồ, dựng video
    facebook.py       # đăng bài, đọc/trả lời bình luận
config/
  agents.example.yaml # đội trợ lý mẫu
  knowledge/faq.example.md
  pages.example.txt
deploy/install.sh, deploy/botai.service, Dockerfile, docker-compose.yml
```

---

## 3. Chuẩn bị (làm 1 lần)

### 3.1. Khóa API model AI
- **Claude (khuyên dùng cho trợ lý trưởng):** vào https://console.anthropic.com → *API Keys* → *Create Key*. Nạp credit.
- (Tùy chọn) **DeepSeek:** https://platform.deepseek.com → API keys. Rẻ, hợp việc khối lượng lớn.
- (Tùy chọn) **OpenAI:** https://platform.openai.com/api-keys.

### 3.2. Tạo bot Telegram bằng BotFather (mỗi trợ lý 1 bot)
1. Mở Telegram, tìm **@BotFather** (có tích xanh) → bấm *Start*.
2. Gõ `/newbot`.
3. Nhập tên hiển thị, ví dụ `Bé Na Trợ Lý`.
4. Nhập username, **phải kết thúc bằng `bot`**, ví dụ `bena_tro_ly_bot`.
5. BotFather trả về token dạng `123456789:AAH...` → chép vào `.env` (ví dụ `TG_TOKEN_DIEU_PHOI=...`).
6. Lặp lại cho các trợ lý khác nếu muốn mỗi trợ lý có bot riêng. **Chỉ bắt buộc 1 bot (trợ lý trưởng)**:
   các trợ lý không có token vẫn làm việc khi trợ lý trưởng giao việc.
7. Muốn dùng trong group: thêm các bot vào group; nhắn `@ten_bot việc cần làm` hoặc reply tin nhắn của bot.

> Giữ bí mật token. Lộ token → vào BotFather gõ `/revoke` để đổi.

### 3.3. Lấy Telegram ID của bạn
Chạy bot lần đầu rồi nhắn `/id` cho bot → điền số **User ID** vào `ADMIN_TELEGRAM_IDS` trong `.env`.
(Admin dùng được ngay, không cần mã kết nối, và duyệt được người khác bằng `/approve`.)

---

## 4. Chạy trên máy tính (local)

### 4.1. Cài phần mềm
- **Python 3.10+**: https://www.python.org/downloads/ (Windows: tick *Add Python to PATH*).
- **ffmpeg**:
  - Windows: `winget install Gyan.FFmpeg` (mở lại cửa sổ lệnh sau khi cài)
  - Mac: `brew install ffmpeg`
  - Ubuntu: `sudo apt install -y ffmpeg fonts-dejavu-core`
- **Git**: https://git-scm.com/downloads

### 4.2. Tải mã nguồn và cấu hình
```bash
git clone https://github.com/Thuongtopbds/Bot-AI.git
cd Bot-AI
cp .env.example .env                              # Windows: copy .env.example .env
cp config/agents.example.yaml config/agents.yaml  # Windows: copy config\agents.example.yaml config\agents.yaml
```
Mở `.env` bằng trình soạn thảo, điền tối thiểu:
```
ANTHROPIC_API_KEY=sk-ant-...
TG_TOKEN_DIEU_PHOI=123456789:AAH...
ADMIN_TELEGRAM_IDS=111111111
```

### 4.3. Chạy
- Mac/Linux: `./run.sh`
- Windows: bấm đúp `run.bat`

Lần đầu script tự tạo môi trường `.venv` và cài thư viện (vài phút). Kiểm tra cấu hình bất cứ lúc nào:
```bash
./run.sh check        # Windows: run.bat check
```
Thấy log `Đội trợ lý đã sẵn sàng` là xong. Mở Telegram, nhắn bot của bạn: `xin chào`.

### 4.4. Người khác muốn dùng bot (mã kết nối)
1. Họ nhắn bot → bot trả `Mã kết nối: K7P2QX`.
2. Bạn duyệt: trên máy chạy bot gõ `./run.sh approve K7P2QX`, **hoặc** nhắn bot `/approve K7P2QX` (nếu bạn là admin).
3. Xem/thu hồi: `./run.sh users`, `./run.sh revoke <user_id>`.

---

## 5. Dùng thử giống trong video

### 5.1. Dựng video bất động sản
Gửi cho **Bé Na** (hoặc thẳng **Trợ lý Video**) 1–3 video, rồi nhắn:
```
Dựng 3 video này thành 1 video Reels ~45 giây giới thiệu căn nhà 81m², mặt tiền 4,5m, sâu 18m,
4 tầng (đang hoàn thiện thô 2 tầng). Lồng tiếng giọng nữ miền Bắc, chèn bản đồ vị trí và tiện ích.
```
Bot sẽ: xem từng video → báo kế hoạch + ước tính chi phí → hỏi vị trí. Bạn bấm 📎 → **Location** gửi vị trí,
và nhắn tiện ích (ví dụ "bệnh viện Hợp Lực 3 phút đi bộ, trường cấp 1 500m"). Trong lúc bot đang làm, bạn vẫn
nhắn thêm được: *"dịch lời thoại sang tiếng Việt, xóa metadata"* — bot sẽ áp dụng vào việc đang chạy.

> Telegram Bot API chỉ cho bot tải file **≤ 20MB**. Video lớn hơn: nén bớt trước khi gửi, gửi dạng *file*,
> hoặc tự chạy [Telegram Bot API server](https://github.com/tdlib/telegram-bot-api) rồi đặt
> `TELEGRAM_API_BASE=http://localhost:8081` (giới hạn lên 2GB).

### 5.2. Đăng bài Facebook hàng loạt
1. **Lấy Page Access Token** (mỗi page):
   - Tạo app tại https://developers.facebook.com/apps (loại *Business*).
   - Mở **Graph API Explorer** → chọn app → *Get Page Access Token*, cấp quyền:
     `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `pages_manage_engagement`, `pages_read_user_content`.
   - Đổi sang token dài hạn bằng *Access Token Debugger* → *Extend Access Token*, rồi gọi `me/accounts`
     bằng user token dài hạn để lấy **page token không hết hạn** và **page ID**.
2. Tạo file `pages.txt` theo mẫu `config/pages.example.txt`: `tên | page_id | token`, mỗi page 1 dòng.
3. Gửi file cho **Trợ lý Mạng xã hội** và nhắn `nhập danh sách page`. File được chuyển vào
   `data/secrets/` (quyền 600) và xóa khỏi thư mục làm việc; AI chỉ thấy tên page, không thấy token.
4. Nhắn: `Đăng video final.mp4 vừa dựng lên tất cả page, viết caption hấp dẫn` → bot trả link từng bài.

### 5.3. Tự trả lời bình luận "xin giá"
1. Chép `config/knowledge/faq.example.md` thành `data/knowledge/faq.md`, điền giá, pháp lý, SĐT…
2. Trong `.env`:
   ```
   FB_WATCH_INTERVAL=120        # kiểm tra mỗi 2 phút
   FB_WATCH_AGENT=mang_xa_hoi
   FB_AUTO_REPLY=false          # false: bot đề xuất câu trả lời cho bạn duyệt; true: tự trả lời
   ```
3. Khởi động lại bot. Có bình luận mới → bạn nhận báo cáo trên Telegram. Bắt đầu với `false`, khi thấy
   câu trả lời ổn thì chuyển `true` (như video: "làm dần dần, hoặc cho học bộ FAQ trước").

### 5.4. Bộ nhớ / "bộ não"
- Bot tự ghi bài học vào `data/memory/<tên trợ lý>.md` sau khi sửa được lỗi. Bạn có thể mở file này để
  sửa/xóa, hoặc nhắn "ghi nhớ: luôn dùng giọng nam cho video căn hộ".
- `/reset` để bắt đầu việc mới (xóa ngữ cảnh hội thoại, **không** xóa bài học).

---

## 6. Tùy biến đội trợ lý (`config/agents.yaml`)

```yaml
  - name: ban_hang                     # tên nội bộ (không dấu)
    display_name: "Bé Như – Bán hàng"
    telegram_token_env: TG_TOKEN_BAN_HANG
    provider: anthropic                # anthropic | openai | deepseek
    model: claude-sonnet-5-5
    effort: medium                     # chỉ Claude: low|medium|high|xhigh|max
    skills: [common, facebook]         # common | video | facebook | shell
    knowledge_files: [faq.md, bang_gia.md]   # đặt trong data/knowledge/
    role: |
      Bạn là nhân viên bán hàng ...
```
Thêm trợ lý mới vào `delegate_to` của trợ lý trưởng để Bé Na giao việc được. Chọn model:
- Trợ lý trưởng/điều phối, dựng video (cần nhìn khung hình, tự sửa lỗi): `claude-opus-5-5`.
- Việc thường ngày: `claude-sonnet-5-5`. Việc đơn giản, khối lượng lớn: `claude-haiku-5-5` hoặc DeepSeek
  (đặt `vision: false` nếu model không xem được ảnh).

Muốn thêm kỹ năng mới (ví dụ đăng TikTok, gửi Zalo OA): viết hàm trong `botai/tools/` với decorator `@tool`
(xem `common.py` làm mẫu) và đăng ký trong `get_skill_tools()` ở `botai/tools/__init__.py`.

---

## 7. Đưa lên GitHub

`.gitignore` đã loại `.env`, `config/agents.yaml`, `data/` → **token và dữ liệu không bị đẩy lên**.
```bash
git add .
git commit -m "Cấu hình bot"
git push
```
Kiểm tra trước khi push: `git status` không được có `.env` hay `data/`. Nếu repo công khai và lỡ đẩy token:
đổi token ngay (BotFather `/revoke`, tạo lại API key, tạo lại page token).

> GitHub chỉ **lưu mã nguồn**; bot cần một máy chạy liên tục (máy bạn hoặc VPS). GitHub Actions không phù hợp
> để chạy bot 24/7.

---

## 8. Triển khai lên VPS (chạy 24/7)

**Cấu hình gợi ý:** Ubuntu 22.04/24.04, 2 vCPU, 4GB RAM (dựng video + whisper `small`), 40GB SSD.
VPS ở Singapore/Nhật cho độ trễ tốt tới Telegram và API AI.

### Cách A – Script cài đặt (giống video)
```bash
ssh root@IP_VPS
git clone https://github.com/Thuongtopbds/Bot-AI.git /opt/bot-ai
bash /opt/bot-ai/deploy/install.sh
nano /opt/bot-ai/.env                              # điền khóa API, token, ADMIN_TELEGRAM_IDS
nano /opt/bot-ai/config/agents.yaml                # (tùy chọn)
sudo -u botai /opt/bot-ai/.venv/bin/python -m botai check
systemctl start botai
journalctl -u botai -f                             # xem log (Ctrl+C để thoát xem)
```
Repo **private** thì `git clone` cần đăng nhập: tạo *Personal access token* (GitHub → Settings → Developer
settings) hoặc dùng *Deploy key* SSH.

Lệnh thường dùng:
```bash
systemctl restart botai          # khởi động lại sau khi sửa .env / agents.yaml
systemctl status botai
cd /opt/bot-ai && sudo -u botai .venv/bin/python -m botai approve MÃ
# cập nhật code mới từ GitHub:
cd /opt/bot-ai && sudo -u botai git pull && sudo -u botai .venv/bin/pip install -r requirements.txt && systemctl restart botai
```

### Cách B – Docker
```bash
apt install -y docker.io docker-compose-v2
git clone https://github.com/Thuongtopbds/Bot-AI.git && cd Bot-AI
cp .env.example .env && cp config/agents.example.yaml config/agents.yaml && nano .env
docker compose up -d --build
docker compose logs -f
docker compose exec botai python -m botai approve MÃ
```

### Bảo mật VPS (nên làm)
- Tạo user thường + đăng nhập bằng SSH key, tắt đăng nhập mật khẩu root.
- `ufw allow OpenSSH && ufw enable` (bot dùng polling nên **không cần mở cổng** nào khác).
- Sao lưu định kỳ thư mục `/opt/bot-ai/data` (bộ nhớ, danh sách người dùng, token page).

---

## 9. Lưu ý quan trọng khi vận hành

- **Chi phí:** mỗi lần AI xem khung hình/đọc lời thoại đều tính token. Xem token đã dùng bằng `/status`.
  Giảm chi phí: dùng Sonnet/Haiku/DeepSeek cho trợ lý phụ, `effort: low|medium`, `/reset` khi đổi việc.
- **`allow_shell: true`** cho AI chạy lệnh trên máy để tự sửa lỗi (như video). Rất tiện nhưng có rủi ro:
  chỉ bật cho trợ lý cần, chỉ cho người tin cậy dùng bot, chạy dưới user `botai` (không phải root).
- **Facebook:** đăng quá dày hoặc nội dung trùng lặp trên nhiều page dễ bị hạn chế. Dùng token page chính chủ,
  tuân thủ chính sách Meta. Tự trả lời bình luận nên bắt đầu ở chế độ chờ duyệt.
- **Bản quyền:** chỉ dịch/biên tập lại video bạn có quyền sử dụng. "Xóa metadata" là để bảo vệ thông tin cá nhân
  (GPS, thiết bị), không dùng để che giấu nguồn gốc nội dung của người khác.
- **Bản đồ** dùng dữ liệu OpenStreetMap (đã ghi công trên ảnh). Dùng mức vừa phải theo
  [chính sách tile](https://operations.osmfoundation.org/policies/tiles/).
- **Claude fallbacks:** với Opus 5.5 / Sonnet 5.5, bot bật `fallbacks: "default"` — khi model từ chối một yêu cầu,
  máy chủ Anthropic tự chuyển sang model dự phòng phù hợp. Tắt bằng `fallbacks: false` trong agents.yaml.
- Máy tính cá nhân chạy bot phải bật 24/24; tắt máy là bot ngừng.

---

## 10. Xử lý sự cố

| Hiện tượng | Cách xử lý |
|---|---|
| Bot không trả lời | `python -m botai check`; xem log; đúng token chưa; trong group phải @nhắc tên bot |
| `Conflict: terminated by other getUpdates request` | Cùng 1 token đang chạy ở 2 nơi (máy + VPS). Tắt một nơi |
| `Chưa cài ffmpeg` | Cài ffmpeg (mục 4.1), mở lại terminal |
| `File is too big` | File > 20MB, xem ghi chú mục 5.1 |
| Lỗi `faster-whisper` / `av` | Bật `allow_shell` để AI tự sửa, hoặc `pip install -U faster-whisper` |
| Facebook lỗi 190 | Token hết hạn/sai → tạo token page dài hạn, gửi lại file pages |
| Facebook lỗi 200/10 | Thiếu quyền → cấp đủ quyền ở mục 5.2 |
| Phụ đề lỗi font | Cài `fonts-dejavu-core` |
