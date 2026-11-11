# Hướng dẫn chạy hệ thống

## 1. Yêu cầu
- Docker và Docker Compose v2
- Các cổng còn trống trên máy: `5433` (DB), `8501` (dashboard Streamlit)

## 2. Khởi động
```bash
git pull
docker compose up -d --build
docker compose ps        # dashboard ở trạng thái running, DB ở trạng thái healthy
```

## 3. Kiểm tra nhanh
```bash
curl -s localhost:8501/_stcore/health
# Kết quả đúng: ok
```

## 4. Thông tin truy cập

| Thành phần | Địa chỉ | Tài khoản |
|---|---|---|
| TimescaleDB | `localhost:5433`, database `energy` | energy / energy |
| Dashboard (Streamlit) | http://localhost:8501 | không cần; chọn người trực ở sidebar |

Truy cập DB bằng psql:
```bash
docker compose exec timescaledb psql -U energy -d energy
```

## 5. Cấu trúc kho dữ liệu

| Schema | Nội dung |
|---|---|
| public | `raw` (append-only), `cleaned`, `agg_hourly`, `etl_watermark`, `dim_*`, `param_threshold`, `fact_alert`, `fact_alert_action`, `detector_checkpoint`; view `v_data_clock`, `v_load_sim`, `v_station_latest`, `v_alert_queue`, `v_transfer_status` |
| eval | `event_label`: đáp án 29 sự cố, detector không đọc |
| gold | View cho Power BI (`powerbi/gold_views.sql`) |

| Database | Run | Dùng cho |
|---|---|---|
| `energy` | LIVE | Streamlit, phát lại có người trực |
| `energy_history` | HISTORY | Power BI: 8 tháng, detector chạy một lượt (xem `powerbi/README.md`) |

Chi tiết xem `docs/erd.md` và `docs/architecture.md`.

## 6. Sửa DDL hoặc dựng lại DB
Các script trong `db/init/` (001 bảng → 002 nạp CSV → 003 view) **chỉ chạy khi volume DB còn trống**.

Nâng cấp DB đang có lên v2 mà không xóa dữ liệu dimension:
```bash
P="docker compose exec -T timescaledb psql -U energy -d energy -v ON_ERROR_STOP=1 -q"
$P < sql/migrate_v2.sql && $P < db/init/003_views.sql && $P < sql/elt.sql && $P < sql/reset_replay.sql
```
Dựng lại từ đầu (xóa toàn bộ dữ liệu):
```bash
docker compose down -v
docker compose up -d --build
docker compose logs timescaledb | grep -E "initdb.d|ERROR"
```
- Sửa view: chỉ cần chạy lại `db/init/003_views.sql` (file tự DROP rồi CREATE).
- File mới đặt tên theo số thứ tự tiếp theo, ví dụ `004_xxx.sql`.

## 7. Dựng lại dữ liệu kịch bản
```bash
python scripts/build_scenario.py      # data/scenario_data.csv (ETT gốc, không bơm lỗi) + data/event_label.csv (29 sự cố)
```
Thư mục `data/` được mount vào container tại `/data`.

## 8. Lỗi thường gặp

| Lỗi | Cách xử lý |
|---|---|
| `port is already allocated` | Tắt dịch vụ đang chiếm cổng, hoặc đổi cổng trong `docker-compose.yml` |
| Đã sửa SQL trong `db/init/` nhưng DB không đổi | Volume cũ vẫn được dùng: chạy `sql/migrate_v2.sql` (mục 6) hoặc `docker compose down -v` |
| Dashboard báo `column ... does not exist` | DB còn schema cũ: chạy mục 6 |
| Dashboard báo lỗi kết nối | Xem log: `docker compose logs dashboard`; kiểm tra `DATABASE_URL` |
| `docker compose up --build` dừng ở "additional privileges requested" | Build riêng: `docker build -t <project>-dashboard dashboard` rồi `docker compose up -d --no-build --force-recreate dashboard` |
| Python báo `no pq wrapper available` (Windows App Control chặn DLL) | Chạy `USE_DOCKER=1 scripts/replay_event.sh EV029` |
| Detector báo "Checkpoint … phương pháp cũ" | Chạy `sql/reset_replay.sql` rồi chạy lại detector |

## 9. Dừng hệ thống
```bash
docker compose down        # dừng, giữ nguyên dữ liệu
docker compose down -v     # dừng và xóa toàn bộ dữ liệu
```