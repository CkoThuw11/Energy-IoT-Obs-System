# Energy-IoT-Obs-System

Hệ thống BI thời gian thực **cảnh báo quá tải trạm biến áp**, có cơ chế **ưu tiên và xếp hạng cảnh báo** trên dashboard điều hành.

Luồng: phát lại dữ liệu cảm biến → kho dữ liệu chuỗi thời gian → phát hiện → xếp hạng cảnh báo → xử lý sự cố Đỏ theo 3 bước (**Xác nhận → Chuyển tải → Theo dõi**) trên Streamlit.

Dữ liệu: ETTm1 + ETTm2 (tải và nhiệt độ dầu máy biến áp, 15 phút/bản ghi), gán cho 4 trạm.

## 1. Kiến trúc

```
scenario_data.csv → Producer ──► raw → cleaned → agg_hourly
   (--event EVxxx)     ▲                  │
                       │ paused?          ▼  v_load_sim (tải đo ± chuyển tải mô phỏng)
                 replay_control ◄──── Detector ──► fact_alert ◄──► Streamlit (3 trang)
                       ▲   (dừng khi cảnh báo chuyển sang Đỏ)          │
                       └──── xác nhận chuyển tải / báo giả ────────────┤
                                                 fact_alert_action ◄───┘
```

- **Producer** phát từng mốc `ts` (4 trạm). Trước mỗi mốc, producer đọc `replay_control.paused`; nếu đang dừng thì chờ.
- **Detector** đọc dữ liệu theo thứ tự `ts`, mở/cập nhật/đóng cảnh báo. Khi một cảnh báo **chuyển sang Đỏ**, detector bật `paused = true`.
- **Dashboard** ghi thao tác của người trực vào DB. Khi không còn cảnh báo Đỏ nào chưa xử lý, dashboard tắt `paused`.
- **Tác dụng chuyển tải là mô phỏng.** Dữ liệu là lịch sử nên lưới thật không đổi. View `v_load_sim` tính trạm nguồn = S_H đo − X, trạm nhận = S_H đo + X. Bảng `cleaned` giữ nguyên số đo.
- **Hai đồng hồ:** cột `*_ts` là giờ dữ liệu, dùng cho mọi logic phát hiện. Cột `*_at` là giờ thật (`now()`), chỉ dùng để đo thời gian phản ứng của con người.
- **Run:** `LIVE` là phát lại qua DB, có dừng, hiện trên dashboard. `EVAL_A` / `EVAL_B` chấm phương pháp phát hiện trên CSV, không dừng.

| Service | Công nghệ | Cổng | Vai trò |
|---|---|---|---|
| `timescaledb` | TimescaleDB (PG16) | 5433 | Kho dữ liệu chuỗi thời gian |
| `dashboard` | Streamlit | 8501 | Dashboard điều hành và xử lý cảnh báo |

Chi tiết: `docs/architecture.md` (luồng, quy trình), `docs/erd.md` (schema), `producer/README.md` (producer, ELT).

## 2. Cài đặt

Cần Docker Compose v2 và Python ≥ 3.10.

```bash
cp .env.example .env
docker compose up -d --build                  # dựng lại dashboard mỗi khi sửa code
docker compose exec -T timescaledb psql -U energy -d energy -v ON_ERROR_STOP=1 < sql/elt.sql   # chỉ khi DB mới
pip install -r detector/requirements.txt      # psycopg 3 (hoặc: pyenv activate baseline-env)
```

Kiểm tra: `curl -s localhost:8501/_stcore/health` trả về `ok`. Hai bảng `param_threshold` và `eval.event_label` phải có lần lượt 4 và 38 dòng.

## 3. Chạy demo

Demo phát lại một **sự cố quá tải thật** của Trạm Y, không tiêm lỗi nhân tạo:

```bash
scripts/replay_event.sh EV029 60      # mã sự cố, tốc độ (×60: 15 phút dữ liệu = 15 giây)
```

| Sự cố | Quá tải (t_start → t_end) | Dữ liệu được phát (±2 giờ) | Thời lượng ở ×60 |
|---|---|---|---|
| **EV029** (khuyến nghị) | 04/02/2018 18:30 → 05/02 21:30 | 04/02 16:30 → 05/02 23:30 | ~31 phút |
| EV030 | 06/02/2018 19:00 → 07/02 21:15 | 06/02 17:00 → 07/02 23:15 | ~30 phút |
| EV037 | 08/05/2018 11:30 → 10/05 11:45 | 08/05 09:30 → 10/05 13:45 | ~52 phút |

Thời lượng trên chưa tính thời gian người trực xử lý. Script lần lượt: xóa dữ liệu phát lại cũ (`sql/reset_replay.sql`) → chạy detector nền (log `detector_EV029.log`) → chạy producer `--event`.

Mở **http://localhost:8501** và xử lý (EV029):

| Giờ dữ liệu | Diễn biến | Người trực |
|---|---|---|
| 04/02 ~18:30 | Trạm Y lên Đỏ, producer in `TẠM DỪNG` | **Điều hành** → **Nhận & xử lý** |
| | ① Xác nhận | **Xác nhận – quá tải thật** hoặc **Báo giả** |
| | ② Chuyển tải | Nhập X (có gợi ý) → **Xác nhận chuyển tải** → dữ liệu chạy tiếp |
| các mốc sau | ③ Theo dõi: tải mô phỏng Trạm Y giảm, Trạm 3 tăng X | Ghi nguyên nhân; **Chuyển thêm** nếu vẫn ≥ 90% |
| ~2 mốc sau | Tải mô phỏng < 85% hai lần → cảnh báo tự đóng | Xem thẻ **Kết quả xử lý** |
| 05/02 ~01:00 | Tải đo < 85% hai lần → **trả tải** tự động | — |
| 05/02 ~19:00 | Đỏ lần hai → dừng lại | Xử lý, hoặc thử **Báo giả** |

Cuối cùng mở trang **Báo cáo quản trị** để xem KPI.

- Dừng script bằng **Ctrl+C**, không dùng Ctrl+Z: Ctrl+Z chỉ treo tiến trình, lần chạy sau sẽ bị chặn.
- Chạy lại script thì dữ liệu tự reset.
- Muốn phát theo khoảng thời gian (`--start`, `--days`, `--end`), xem `producer/README.md`.

## 4. Dashboard

Ba trang, tự làm mới mỗi 5 giây. Chỉ có một người trực là "Người trực A".

| Trang | Nội dung |
|---|---|
| **Điều hành** | 4 ô trạm (% tải, xu hướng, "Còn X phút"). **Hàng đợi cảnh báo** xếp theo: đang mở → mức (Đỏ > Vàng > Cảm biến) → TTB (khung 10 phút) → khách hàng ưu tiên → % tải. Dòng quá hạn nhấp nháy (Đỏ chưa nhận > 3', Vàng > 15'). Bảng kết quả xử lý sự cố Đỏ |
| **Chi tiết trạm** | Khi có Đỏ: khung xử lý 3 bước kèm đồng hồ /16' và giờ thật của từng bước. Số liệu hiện tại, biểu đồ 24 giờ (tải đo / tải mô phỏng), trạm dự phòng, lịch sử cảnh báo 7 ngày |
| **Báo cáo quản trị** | 4 KPI kèm mục tiêu và Đạt/Chưa đạt: nhận TB ≤ 3', chuyển tải TB ≤ 16', Đỏ bị bỏ lỡ = 0, báo giả / tổng. Biểu đồ cảnh báo theo trạm × mức. Bảng kết quả xử lý sự cố Đỏ |

Quy trình xử lý sự cố Đỏ:
1. **Xác nhận** quá tải thật, hoặc **Báo giả** (đóng ngay, `verdict = FALSE_ALARM`).
2. **Chuyển tải** sang trạm lân cận:
   - cần giảm = S_H − 0,85 × định mức;
   - trạm lân cận nhận tối đa = 0,9 × định mức lân cận − S_H lân cận.
3. **Theo dõi**:
   - cảnh báo đóng khi tải **mô phỏng** < 85% hai lần liên tiếp;
   - chuyển tải được trả khi tải **đo** < 85% hai lần liên tiếp.

Màu chỉ dùng để báo trạng thái: Đỏ `#E02F44`, Vàng `#FADE2A`, Xám `#8E8E8E` (cảm biến), Xanh `#56A64B`. Xanh dương là số liệu mô phỏng.

## 5. Luật phát hiện

Ngưỡng lấy từ `param_threshold`, không hard-code. Detector **không đọc** `event_label`.

- Tốc độ = `load_pct` − `load_pct` 4 bản ghi trước (%/giờ).
- TTB = (100 − `load_pct`) / tốc độ × 60. TTB là NULL khi tốc độ ≤ 0 hoặc `load_pct` ≥ 100.
- **Đỏ:** ≥ 100% hoặc TTB ≤ 30. **Vàng:** ≥ 90% hoặc TTB ≤ 60. **Đóng:** < 85% ở 2 bản ghi liên tiếp.
- **Cảm biến kẹt:** S_H không đổi ≥ 4 bản ghi → cảnh báo SENSOR, bỏ qua bước xét quá tải.

```bash
python detector/detector.py --source csv --method A     # EVAL_A: chỉ ngưỡng %
python detector/detector.py --source csv --method B     # EVAL_B: thêm cảm biến kẹt + TTB
python -m unittest discover -s detector -v              # unit test
```

## 6. Trạm và dữ liệu

| Trạm | Nguồn | Loại | Định mức | Ghi chú |
|---|---|---|---|---|
| `TRAM_Y` | ETTm1 | Thật | 20,45 | Sát giới hạn (P99 đã loại lỗi × 1,05); trạm dùng cho Red Alert |
| `TRAM_2` | ETTm2 | Thật | 68,99 | |
| `TRAM_3` | ETTm1 | Dẫn xuất (+7 ngày) | 27,86 | Còn dư công suất; nhận chuyển tải từ Trạm Y |
| `TRAM_4` | ETTm2 | Dẫn xuất (+14 ngày) | 69,05 | |

- Giai đoạn hiệu chỉnh ngưỡng: 01/07/2016 → 31/10/2017. Giai đoạn kiểm thử: 01/11/2017 → 26/06/2018.
- Nhãn sự cố chỉ nằm trong `eval.event_label`. Bảng `raw` và `cleaned` không có nhãn.

## 7. Hạn chế

- Chỉ có 2 trạm thật; Trạm 3 và Trạm 4 là bản dời ngày.
- Tác dụng chuyển tải là mô phỏng; hệ thống không điều khiển lưới.
- Dừng dữ liệu khi người trực xử lý là một giả định về thời gian.
- Leo thang Vàng (15 phút) chỉ có ý nghĩa khi chạy thời gian thật (×1).
- Định mức và hệ số k là giả định của nhóm.
- Ngoài phạm vi: trường hợp trạm dự phòng cũng cao tải, tổn thất điện năng.

## 8. Lệnh thường dùng

```bash
docker compose logs -f <service>
docker compose down              # dừng, giữ dữ liệu
docker compose down -v           # dừng và XÓA dữ liệu (cần khi sửa db/init/ hoặc file CSV tham số)
```

Không commit file `.env`. Grafana và FastAPI cũ được giữ trong `archive/`, không còn dùng.
