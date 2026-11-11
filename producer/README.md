# Pipeline dữ liệu: producer → raw → cleaned → agg_hourly

Phiên bản v2 (xem `sped.md` cục bộ / `docs/architecture.md`): `raw` append-only, không dừng khi lên Đỏ.

```
scenario_data.csv ──producer.py (INSERT)──► raw (append-only) ──sp_raw_to_cleaned (job 10 giây, bản mới nhất)──► cleaned ──(continuous aggregate)──► agg_hourly
```

| File | Vai trò |
|---|---|
| `producer/producer.py` | Phát lại `data/scenario_data.csv` vào `raw` theo thứ tự `ts` |
| `sql/elt.sql` | Thủ tục `sp_raw_to_cleaned` + đăng ký chạy mỗi 10 giây bằng `add_job` |
| `sql/check_pipeline.sql` | Kiểm tra nhanh 96 mốc / 96 / 24 dòng mỗi trạm |
| `db/init/001_schema.sql` | (đã có) bảng `raw`, `cleaned`, `etl_watermark`, continuous aggregate `agg_hourly` |

## 1. Chuẩn bị (làm một lần)

```bash
git pull
cp .env.example .env                       # nếu chưa có
docker compose up -d --build
docker compose ps                          # timescaledb phải ở trạng thái healthy

pip install -r producer/requirements.txt   # psycopg[binary]

# Nạp thủ tục ELT và đăng ký job 10 giây (chạy lại nhiều lần vẫn an toàn)
docker compose exec -T timescaledb psql -U energy -d energy -v ON_ERROR_STOP=1 < sql/elt.sql
```

Kiểm tra job đã đăng ký:
```bash
docker compose exec timescaledb psql -U energy -d energy \
  -c "SELECT job_id, proc_name, schedule_interval, scheduled FROM timescaledb_information.jobs WHERE proc_name='sp_raw_to_cleaned'"
```

## 2. Chạy producer

```bash
python producer/producer.py --speed 20 --event EV029               # demo: 15 phút dữ liệu = 45 giây
python producer/producer.py --speed 60 --start 2017-11-01          # nhanh: 15 phút dữ liệu = 15 giây
python producer/producer.py --speed 0  --start 2017-11-01 --days 1  # hết tốc độ, đúng 1 ngày (để kiểm tra)
python producer/producer.py --speed 0                               # nạp cả 8 tháng (91.328 dòng)
```

| Tham số | Ý nghĩa |
|---|---|
| `--speed` | `1`, `20` (demo), `60`, `x60`... ; `0` = hết tốc độ. 15 phút dữ liệu = 900 / speed giây |
| `--start` | Bắt đầu phát từ `YYYY-MM-DD` hoặc `"YYYY-MM-DD HH:MM"` (mặc định: đầu file) |
| `--days N` / `--end` | Dừng sau N ngày / trước mốc `--end` |
| `--event` | Phát lại một sự cố của `event_label.csv` (vd `EV029`): từ `t_start − 2 giờ` đến `t_end + 2 giờ` |
| `--dry-run` | Chỉ in kế hoạch phát, không ghi DB |
| `--dsn` | Chuỗi kết nối (mặc định `postgresql://energy:energy@localhost:5433/energy`, hoặc biến `DATABASE_URL` / `POSTGRES_*`) |

- Dừng bằng `Ctrl+C`: mốc đang ghi dở bị hủy, các mốc đã xong vẫn còn. Chạy lại cùng khoảng sẽ **thêm dòng** vào `raw` (append-only); ELT chỉ giữ bản `ingested_at` mới nhất nên `cleaned` không trùng.
- Mỗi mốc `ts` ghi đủ 4 trạm trong một transaction, nên ELT không bao giờ thấy mốc ghi dở.
- Log: `[ts] đã phát X/Y mốc | đã ghi N dòng`.
- **Không dừng** khi có cảnh báo Đỏ: dữ liệu chạy liên tục như lưới thật; thời hạn xử lý tính theo giờ dữ liệu.

## 3. Kiểm tra nhanh

**a) Phát 1 ngày: raw = cleaned = 96 dòng/trạm, agg_hourly = 24 dòng/trạm**
```bash
docker compose down -v && docker compose up -d        # DB sạch (bỏ qua nếu DB đang trống)
docker compose exec -T timescaledb psql -U energy -d energy -v ON_ERROR_STOP=1 < sql/elt.sql
python producer/producer.py --speed 0 --start 2017-11-01 --days 1
sleep 15                                              # chờ job 10 giây xử lý xong
docker compose exec -T timescaledb psql -U energy -d energy -v day=2017-11-01 < sql/check_pipeline.sql
```
Cột `ket_qua` của cả 4 trạm phải là `PASS`.

**b) Chạy lại: raw tăng, cleaned không trùng**
```bash
python producer/producer.py --speed 0 --start 2017-11-01 --days 1    # chạy lại lần 2
docker compose exec -T timescaledb psql -U energy -d energy -v day=2017-11-01 < sql/check_pipeline.sql
```
`raw_total` tăng lên 768 (append-only), `cleaned_total` vẫn 384, `ket_qua` vẫn `PASS`.

## 4. Lưu ý

- Dữ liệu có `ts` năm 2017–2018, nên mọi tính toán theo **thời gian của dữ liệu**, không theo giờ máy. Policy làm mới của `agg_hourly` đã đặt `start_offset => NULL` trong `001_schema.sql`, và `materialized_only = false` nên giờ đang phát vẫn hiện ngay.
- Dashboard hiển thị giờ dữ liệu nguyên trạng (không đổi múi giờ); giờ thật hiển thị theo `DISPLAY_TZ` (mặc định Asia/Ho_Chi_Minh).
- `cleaned` chỉ giữ `hufl`, `hull`, `ot`, `s_h`, `load_pct` (bỏ `quality_flag` và 4 cột tải trung/hạ áp); detector tự kiểm tra cảm biến đứng yên.
- Watermark tính theo `ingested_at` của từng trạm và quét lùi 1 phút, nên bản phát lại / sửa muộn của một mốc cũ vẫn được ELT ghi đè vào `cleaned`. Detector không xử lý lại mốc đã qua. Muốn phát lại sạch: `sql/reset_replay.sql`.
- Dừng job: `SELECT alter_job(<job_id>, scheduled => false);`
