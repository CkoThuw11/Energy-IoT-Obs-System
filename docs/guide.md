# Hướng dẫn chạy hệ thống

## 1. Yêu cầu
- Docker và Docker Compose v2
- Các cổng còn trống trên máy: `5433` (DB), `3000` (Grafana), `8000` (API)

## 2. Khởi động
```bash
git pull
docker compose up -d --build
docker compose ps        # cả 3 service ở trạng thái running, DB ở trạng thái healthy
```

## 3. Kiểm tra nhanh
```bash
curl -s localhost:8000/health
# Kết quả đúng: {"status":"ok"}

curl -s -u admin:admin localhost:3000/api/datasources/uid/timescaledb/health
# Kết quả đúng: "status":"OK"
```

## 4. Thông tin truy cập

| Thành phần | Địa chỉ | Tài khoản |
|---|---|---|
| TimescaleDB | `localhost:5433`, database `energy` | energy / energy |
| Grafana | http://localhost:3000 | admin / admin |
| API | http://localhost:8000/docs | không cần |

Truy cập DB bằng psql:
```bash
docker compose exec timescaledb psql -U energy -d energy
```

## 5. Cấu trúc kho dữ liệu

| Schema | Nội dung |
|---|---|
| raw | `sensor_reading`: dữ liệu nguyên vẹn từ producer |
| staging | `sensor_reading`: dữ liệu đã làm sạch. `etl_watermark`: mốc ELT |
| mart | Bảng dim, param, fact, eval và view `v_latest_reading` |

Chi tiết xem `docs/erd.md` và `docs/architecture.md`.

## 6. Sửa DDL hoặc dựng lại DB từ đầu
Các script trong `db/init/` **chỉ chạy khi volume DB còn trống**. Sau khi sửa hoặc thêm file SQL:
```bash
docker compose down -v      # xóa toàn bộ dữ liệu DB và Grafana
docker compose up -d --build
docker compose logs timescaledb | grep -E "initdb.d|ERROR"
```
- File mới đặt tên theo số thứ tự tiếp theo, ví dụ `008_xxx.sql`.
- Script chạy theo thứ tự tên file.

## 7. Nạp CSV vào DB
Thư mục `data/` được mount vào container tại `/data`. Ví dụ nạp file dim:
```bash
docker compose exec timescaledb psql -U energy -d energy \
  -c "\copy mart.dim_substation FROM '/data/dim_substation.csv' CSV HEADER"
```
Thứ tự cột trong CSV phải khớp đúng với bảng. Xem lại bằng lệnh `\d mart.<tên_bảng>`.

## 8. Lỗi thường gặp

| Lỗi | Cách xử lý |
|---|---|
| `port is already allocated` | Tắt dịch vụ đang chiếm cổng, hoặc đổi cổng trong `docker-compose.yml` |
| Đã sửa SQL nhưng DB không đổi | Chạy `docker compose down -v` rồi khởi động lại |
| Grafana không thấy datasource | Xem log: `docker compose logs grafana \| grep -i provision` |
| API không lên | Xem log: `docker compose logs api` |

## 9. Dừng hệ thống
```bash
docker compose down        # dừng, giữ nguyên dữ liệu
docker compose down -v     # dừng và xóa toàn bộ dữ liệu
```