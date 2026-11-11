# Power BI v2 – Cảnh báo quá tải trạm biến áp

Báo cáo trả lời 5 nhóm câu hỏi nghiệp vụ (sped.md §5.2). **Không còn A/B testing.**

| Thư mục / file | Nội dung |
|---|---|
| `CanhBaoQuaTai_v2/` | Project Power BI mới (PBIP): 9 bảng, 32 measure, 5 trang. **Dùng bản này** |
| `gold_views.sql` | Lớp gold (VIEW) – cài trên `energy_history` và `energy` |
| `build_pbip_v2.py` | Sinh lại `CanhBaoQuaTai_v2/` từ view gold (ghi đè thư mục) |
| `CanhBaoQuaTai/` | Bản cũ (có A/B, chế độ CSV/PostgreSQL) – giữ để tham khảo |
| `_legacy_v1/`, `_backup_csv_*` | Dữ liệu và bản sao lưu cũ |

## 1. Nguồn dữ liệu (Import, Refresh tay)

| Tham số | Giá trị | Bảng |
|---|---|---|
| `PgServer` | `localhost:5433` | – |
| `DbHist` | `energy_history` | `dim_station`, `dim_customer_group`, `dim_date`, `fact_load_hourly`, `fact_overload_episode`, `fact_sensor_fault`, `hist_alert` (run HISTORY) |
| `DbLive` | `energy` | `live_alert` (run LIVE), `live_transfer` |

- **HISTORY**: toàn bộ 8 tháng kiểm thử + detector chạy một lượt, **không có người trực** → mọi Đỏ là "bỏ lỡ". Chỉ dùng cho tải lưới, sự cố, cảm biến.
- **LIVE**: các lần phát lại có người trực (Streamlit hoặc `scripts/simulate_operator.py`). Dùng cho vận hành và chuyển tải.
- Thời hạn xử lý tính theo **giờ dữ liệu**: Đỏ ≤ 30 phút (lên Đỏ → chuyển tải), Vàng ≤ 60 phút (mở → theo dõi).

## 2. Dựng DB `energy_history` (một lần, hoặc khi dữ liệu / luật thay đổi)

```bash
P="docker exec -i energy-timescaledb psql -U energy -v ON_ERROR_STOP=1 -q"
docker exec energy-timescaledb createdb -U energy energy_history
for f in db/init/001_schema.sql db/init/002_load_data.sql db/init/003_views.sql sql/elt.sql; do $P -d energy_history < $f; done
$P -d energy_history -c "\copy raw (ts,station_id,hufl,hull,mufl,mull,lufl,lull,ot) FROM '/data/scenario_data.csv' CSV HEADER"
$P -d energy_history -c "CALL sp_raw_to_cleaned()"
$P -d energy_history -c "CALL refresh_continuous_aggregate('agg_hourly', NULL, NULL)"
python detector/detector.py --source db --run-id HISTORY --once \
       --database-url postgresql://energy:energy@localhost:5433/energy_history
for db in energy_history energy; do $P -d $db < powerbi/gold_views.sql; done
```

Máy không cài được psycopg: chạy detector trong container `python:3.12-slim` trên network của compose (xem `scripts/replay_event.sh`, `USE_DOCKER=1`).

## 3. Mở báo cáo

1. Mở `CanhBaoQuaTai_v2/CanhBaoQuaTai_v2.pbip` bằng Power BI Desktop, bấm **Refresh**.
2. Lần đầu: đăng nhập PostgreSQL kiểu **Database**, `energy` / `energy`; lỗi SSL → kết nối không mã hoá; hỏi privacy level → **Organizational**.
3. Sau mỗi lần phát lại / thao tác trên Streamlit: **Refresh** để cập nhật trang 3–4.

## 4. Năm trang

| Trang | Câu hỏi | Nguồn | Hình chính |
|---|---|---|---|
| 1 · Lưới điện chịu tải thế nào? | Trạm nào thường xuyên tải cao (giờ ≥ 90% / ≥ 100%)? Cao điểm theo giờ / thứ / tháng? Xu hướng? | HISTORY `fact_load_hourly` | Cột theo trạm, cột theo giờ, cột theo thứ, đường theo tháng × trạm |
| 2 · Có bao nhiêu sự cố, xảy ra ở đâu? | Đỏ / Vàng / Cảm biến theo trạm, tháng; đợt quá tải kéo dài bao lâu, đỉnh bao nhiêu %; trạm nào cần nâng cấp | HISTORY `hist_alert`, `fact_overload_episode` | Cột theo trạm, theo tháng; bảng 9 đợt quá tải; thanh số đợt × giờ ≥ 100% |
| 3 · Đội vận hành xử lý có tốt không? | % Đỏ đúng hạn; Đỏ bị bỏ lỡ / trễ; tỷ lệ báo giả; thời gian phản ứng theo tháng | LIVE `live_alert` | Thẻ KPI, đường theo tháng dữ liệu, cột kết quả theo trạm, bảng cảnh báo |
| 4 · Chuyển tải có hiệu quả không? | Số lần, tổng lượng; % về an toàn; số lần hết giờ / quá hạn; trạm nhận có bị đẩy lên Vàng/Đỏ | LIVE `live_transfer` | Thẻ KPI, bảng từng lần chuyển |
| 5 · Thiết bị đo có đáng tin không? | Trạm hay lỗi cảm biến nhất; tổng giờ mất dữ liệu tin cậy; lỗi có quy luật | HISTORY `fact_sensor_fault` | Cột theo trạm, cột theo ngày trong tháng, bảng lỗi |

## 5. Số liệu tham chiếu (HISTORY, sau khi dựng lại DB)

| Câu hỏi | Kết quả |
|---|---|
| Giờ ≥ 90% / ≥ 100% | Trạm Y 178 / 21 · Trạm 2 7 / 0 · Trạm 4 7 / 0 · Trạm 3 0 / 0 |
| Tải TB 8 tháng | Trạm Y 52,6% · Trạm 2 47,0% · Trạm 4 46,4% · Trạm 3 38,5% |
| Đợt quá tải thật | 9, đều ở Trạm Y; dài nhất EV037 (315 phút ≥ 100%, đỉnh 115,3%) |
| Cảnh báo detector | Đỏ 100 (Trạm Y 96), Vàng 272, Cảm biến 22; 83/100 Đỏ không khớp sự cố (luật TTB nhạy) |
| Lỗi cảm biến | 20 (Trạm 3 nhiều nhất: 6 lỗi, 121 giờ); phát hiện 20/20, trễ 45 phút; DAY31_FILL lặp hằng tháng |

## 6. Ghi chú

- Câu "thời gian phản ứng có cải thiện qua các tháng" chỉ có ý nghĩa khi có nhiều lần phát lại LIVE ở các tháng khác nhau.
- "Báo giả" trang 3 là do người trực bấm; "cảnh báo không khớp sự cố" trang 2 là so với đáp án (độ tin cậy của luật phát hiện).
- Trang do script sinh: kiểm tra màu, nhãn, sắp xếp trong Power BI Desktop rồi Save. Chạy lại `build_pbip_v2.py` sẽ **ghi đè** chỉnh sửa tay.
