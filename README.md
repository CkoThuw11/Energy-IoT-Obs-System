# Energy-IoT-Obs-System

Hệ thống BI **cảnh báo quá tải trạm biến áp**, có cơ chế **ưu tiên và xếp hạng cảnh báo** trên dashboard điều hành (Streamlit) và báo cáo phân tích (Power BI).

Luồng: phát lại dữ liệu cảm biến → kho dữ liệu chuỗi thời gian → phát hiện → xếp hạng cảnh báo → người trực xử lý (Vàng: **Theo dõi**; Đỏ: **Chuyển tải** hoặc **Báo giả**). Dữ liệu **không dừng** khi lên Đỏ; thời hạn xử lý tính theo **giờ dữ liệu**.

Dữ liệu: ETTm1 + ETTm2 (tải và nhiệt độ dầu của 2 máy biến áp thật, 15 phút/bản ghi), gán cho 4 trạm. Không bơm lỗi nhân tạo.

## 1. Kiến trúc

```
scenario_data.csv → Producer ──► raw (append-only) ──ELT 10s──► cleaned ──► agg_hourly
   (--event EVxxx)                 giữ ingested_at        bản mới nhất    │
                                                                         ▼  v_load_sim (tải đo ± chuyển tải mô phỏng)
                                         Detector ──► fact_alert ◄──► Streamlit (3 trang)
                                            ▲  trả tải SAFE / TIMEOUT       │ Theo dõi / Chuyển tải / Báo giả
                                            └────── fact_alert_action ◄─────┘
```

- **Producer** phát từng mốc `ts` (4 trạm, 1 transaction) vào `raw` bằng `INSERT` thuần. Phát lại cùng mốc chỉ thêm dòng mới.
- **ELT** (`sql/elt.sql`, chạy mỗi 10 giây) lấy bản `raw` có `ingested_at` mới nhất cho mỗi (trạm, ts), tính `s_h`, `load_pct`, ghi `cleaned` bằng `ON CONFLICT DO UPDATE`.
- **Detector** đọc dữ liệu theo thứ tự `ts`, mở/cập nhật/đóng cảnh báo và trả tải. Dữ liệu chạy liên tục, không ai xử lý thì cảnh báo giữ nguyên tới khi tải tự về bình thường.
- **Dashboard** ghi thao tác của người trực, kèm giờ thật (`*_at`) và mốc dữ liệu (`acked_ts`, `action_ts`).
- **Tác dụng chuyển tải là mô phỏng.** Dữ liệu là lịch sử nên lưới thật không đổi. `v_load_sim`: trạm nguồn = S_H đo − X, trạm nhận = S_H đo + X. `cleaned` giữ nguyên số đo.
- **Hai đồng hồ:** `*_ts` là giờ dữ liệu, dùng cho phát hiện **và thời hạn xử lý**. `*_at` là giờ thật, chỉ để tham khảo.
- **Run:** `LIVE` = phát lại có người trực (DB `energy`). `HISTORY` = detector chạy một lượt toàn bộ 8 tháng cho Power BI (DB `energy_history`).

| Service | Công nghệ | Cổng | Vai trò |
|---|---|---|---|
| `timescaledb` | TimescaleDB (PG16) | 5433 | Kho dữ liệu chuỗi thời gian |
| `dashboard` | Streamlit | 8501 | Dashboard điều hành và xử lý cảnh báo |

Chi tiết: `docs/architecture.md` (luồng, quy trình), `docs/erd.md` (schema), `producer/README.md` (producer, ELT), `powerbi/README.md` (Power BI).

## 2. Cài đặt

Cần Docker Compose v2 và Python ≥ 3.10.

```bash
cp .env.example .env
docker compose up -d --build                  # dựng lại dashboard mỗi khi sửa code
P="docker compose exec -T timescaledb psql -U energy -d energy -v ON_ERROR_STOP=1 -q"
$P < sql/elt.sql                              # chỉ khi DB mới
pip install -r detector/requirements.txt      # psycopg 3 (máy không cài được: dùng USE_DOCKER=1, mục 3)
```

**Nâng cấp DB đã có từ bản cũ (v1)** – không cần xóa volume:

```bash
$P < sql/migrate_v2.sql && $P < db/init/003_views.sql && $P < sql/elt.sql && $P < sql/reset_replay.sql
```

Kiểm tra: `curl -s localhost:8501/_stcore/health` trả về `ok`. `param_threshold` có 4 dòng, `eval.event_label` có **29** dòng (9 quá tải + 20 lỗi cảm biến).

## 3. Chạy demo

Phát lại một **sự cố quá tải thật** của Trạm Y ở tốc độ demo **×20** (15 phút dữ liệu = 45 giây thật):

```bash
scripts/replay_event.sh EV029                # tốc độ mặc định 20
USE_DOCKER=1 scripts/replay_event.sh EV029   # chạy producer / detector trong container python
```

| Sự cố | Quá tải (t_start → t_end) | Dữ liệu được phát (±2 giờ) | Thời lượng ở ×20 |
|---|---|---|---|
| **EV029** (khuyến nghị) | 04/02/2018 18:30 → 05/02 21:30 | 04/02 16:30 → 05/02 23:30 | ~94 phút |
| EV031 | 10/02/2018 19:45 → 10/02 21:30 | 10/02 17:45 → 10/02 23:30 | ~18 phút |
| EV023 | 12/12/2017 07:00 → 12/12 07:45 | 12/12 05:00 → 12/12 09:45 | ~15 phút |

Thời hạn xử lý theo **giờ dữ liệu**:

| Mức | Thao tác | Thời hạn | Ở ×20 |
|---|---|---|---|
| Đỏ | **Xác nhận chuyển tải** (hoặc **Báo giả**) | ≤ 30 phút kể từ lúc lên Đỏ | ≈ 1 phút 30 giây thật |
| Vàng | **Theo dõi** | ≤ 60 phút kể từ lúc mở | ≈ 3 phút thật |

Mở **http://localhost:8501** và xử lý (EV029):

| Giờ dữ liệu | Diễn biến | Người trực |
|---|---|---|
| 04/02 ~18:30 | Trạm Y lên Đỏ (dữ liệu vẫn chạy) | **Điều hành** → **Xử lý** → trang Chi tiết trạm |
| | Một khung chuyển tải: lượng điền sẵn, trạng thái trạm nhận | **Xác nhận chuyển tải** (tự ghi quá tải thật) hoặc **Báo giả** |
| các mốc sau | Tải mô phỏng Trạm Y giảm, Trạm 3 tăng X | Ghi nguyên nhân; **Chuyển thêm** nếu vẫn ≥ 90% |
| sau 2 giờ | Tải đo vẫn ≥ 90% → **Chuyển tải quá hạn** | Theo dõi / chuyển thêm |
| | Tải đo < 85% hai lần → trả tải **SAFE**; hoặc đã ≥ 2 giờ và < 90% → trả tải **TIMEOUT** | — |
| | Không ai xử lý Đỏ → cảnh báo giữ nguyên tới khi tải tự giảm → **bị bỏ lỡ** | — |

Giả lập người trực (gọi đúng hàm ghi của dashboard, chạy trong container dashboard):

```bash
docker cp scripts/simulate_operator.py energy-dashboard:/app/
docker exec energy-dashboard python /app/simulate_operator.py --red-plan ontime,late,ignore,false
```

## 4. Dashboard (Streamlit)

Ba trang, tự làm mới mỗi 5 giây. Chỉ có một người trực là "Người trực A".

| Trang | Nội dung |
|---|---|
| **Điều hành** | 4 ô trạm (% tải, xu hướng, "Còn X phút", cờ **chuyển tải quá hạn**). **Hàng đợi** xếp: đang mở → mức (Đỏ > Vàng > Cảm biến) → TTB (khung 10 phút) → khách hàng ưu tiên → % tải. Cột "đã chờ X/30 (hoặc /60) phút dữ liệu"; quá hạn nhấp nháy. Nút: Đỏ **Xử lý**, Vàng **Theo dõi**, Cảm biến **Chuyển bảo trì** |
| **Chi tiết trạm** | **Trạm dự phòng ở trên đầu.** Khi có Đỏ: khung chuyển tải duy nhất + Báo giả, đồng hồ giờ dữ liệu /30′. Khi có Vàng: nút Theo dõi, đồng hồ /60′. Số liệu hiện tại, biểu đồ 24 giờ (tải đo / tải mô phỏng), chuyển tải (lý do trả, quá hạn), lịch sử 7 ngày |
| **Báo cáo quản trị** | Đỏ chuyển tải đúng hạn, Đỏ bị bỏ lỡ, báo giả / tổng Đỏ, Vàng theo dõi đúng hạn; số lần / lượng chuyển tải, trả an toàn, trả do hết giờ, quá hạn; cảnh báo theo trạm × mức; bảng kết quả Đỏ |

Chuyển tải:
- cần giảm = S_H − 0,85 × định mức (thêm dự báo 1 giờ nếu tải đang tăng);
- trạm nhận tối đa = 0,9 × định mức trạm nhận − S_H trạm nhận (kiểm tra lại trong DB lúc ghi);
- cảnh báo đóng khi tải **mô phỏng** < 85% hai lần; trả tải theo tải **đo** (SAFE / TIMEOUT).

Màu chỉ dùng để báo trạng thái: Đỏ `#E02F44`, Vàng `#FADE2A`, Xám `#8E8E8E` (cảm biến), Xanh `#56A64B`. Xanh dương là số liệu mô phỏng.

## 5. Luật phát hiện

Ngưỡng lấy từ `param_threshold`, không hard-code. Detector **không đọc** `event_label`. Một phương pháp duy nhất.

- Tốc độ = `load_pct` − `load_pct` 4 bản ghi trước (%/giờ), trên tải đo.
- TTB = (100 − `load_pct`) / tốc độ × 60. TTB là NULL khi tốc độ ≤ 0 hoặc `load_pct` ≥ 100.
- **Đỏ:** ≥ 100% hoặc TTB ≤ 30. **Vàng:** ≥ 90% hoặc TTB ≤ 60. **Đóng:** < 85% ở 2 bản ghi liên tiếp.
- **Lỗi cảm biến** (mọi lỗi dữ liệu gom về một loại): S_H không đổi ≥ 4 bản ghi → cảnh báo SENSOR, bỏ qua bước xét quá tải.

```bash
python detector/detector.py --source csv                 # chạy 8 tháng trên CSV → alerts.csv
python detector/detector.py --source db --run-id HISTORY --once   # ghi DB energy_history cho Power BI
python -m unittest discover -s detector -v               # unit test
```

## 6. Trạm và dữ liệu

| Trạm | Nguồn | Loại | Định mức | Ghi chú |
|---|---|---|---|---|
| `TRAM_Y` | ETTm1 | Thật | 20,45 | Sát giới hạn (P99 đã loại lỗi × 1,05); 9 đợt quá tải thật |
| `TRAM_2` | ETTm2 | Thật | 68,99 | Tải giảm dần ở năm gần nhất → tối đa chỉ lên Vàng |
| `TRAM_3` | ETTm1 | Dẫn xuất (−7 ngày) | 27,86 | Luôn dư công suất; nhận chuyển tải từ Trạm Y |
| `TRAM_4` | ETTm2 | Dẫn xuất (−14 ngày) | 69,05 | Tối đa chỉ lên Vàng |

- Hiệu chuẩn (16 tháng): 01/07/2016 → 31/10/2017 → P99 và định mức. Kiểm thử (8 tháng): 01/11/2017 → 26/06/2018.
- `scripts/build_scenario.py` dựng lại `scenario_data.csv` từ ETT gốc (không bơm lỗi) và `event_label.csv` (29 sự cố, giữ mã EV gốc).
- Nhãn sự cố chỉ nằm trong `eval.event_label`. Bảng `raw` và `cleaned` không có nhãn.
- Dữ liệu âm ở MUFL/LUFL… là đặc điểm của bộ dữ liệu gốc; S_H chỉ dùng HUFL, HULL.

## 7. Hạn chế

- Chỉ có 2 trạm thật; Trạm 3 và Trạm 4 là bản dời ngày.
- Định mức và hệ số k là giả định của nhóm ("quá tải" là so với định mức tự đặt).
- Tác dụng chuyển tải là mô phỏng; hệ thống không điều khiển lưới.
- Luật TTB nhạy với dao động ngắn: sinh nhiều cảnh báo Vàng/Đỏ không khớp sự cố (xem Power BI trang 2).
- Ngoài phạm vi: trường hợp trạm dự phòng cũng cao tải, tổn thất điện năng.

## 8. Lệnh thường dùng

```bash
docker compose logs -f <service>
docker compose down              # dừng, giữ dữ liệu
docker compose down -v           # dừng và XÓA dữ liệu (cài lại từ db/init/)
```

Không commit file `.env`. Grafana và FastAPI cũ được giữ trong `archive/`, không còn dùng.
