# Đặc tả bơm sự cố và đánh giá phát hiện

## Dữ liệu và quy ước

ETTm1/ETTm2 có chu kỳ 15 phút, từ 01/07/2016 đến 26/06/2018 (69.680 mẫu mỗi chuỗi). Hiệu chuẩn bằng 16 tháng đầu (đến 31/10/2017); bơm sự cố và đánh giá trên 8 tháng còn lại. EDA chia phần hiệu chuẩn thành TRAIN 12 tháng và VALID 4 tháng; chỉ dùng VALID để kiểm tra lựa chọn trước khi tính lại tham số trên đủ 16 tháng.

Tải là `HUFL` (`total_load`), không cộng ba kênh tải. `load_pct = 100 × HUFL / rated_load`. P99 sạch của 16 tháng là 18,888 (ETTm1) và 58,554 (ETTm2). Dùng mặc định `rating_coef=1.25` trong `param_threshold.csv`, tương ứng `rated_load` 23,610 và 73,193; đây là định mức mô phỏng vì ETT không có định mức thiết bị thật. Báo cáo độ nhạy với hệ số 1,0 và 1,5.

EDA cho tốc độ tăng tự nhiên 1 giờ P95/P99 là 16,7/30,9 điểm phần trăm (pp) ở ETTm1 và 7,0/10,6 pp ở ETTm2. P99,9 bước nhảy tự nhiên 15 phút xấp xỉ 45/12 pp. Các mốc này dùng để chọn biên độ, không phải giới hạn vận hành thật.

## Ba loại sự cố

Bơm trên bản sao của dữ liệu đánh giá; không sửa dữ liệu gốc. Mỗi loại có 10–20 lần/trạm (tối thiểu 10), các lần cách nhau ít nhất 24 giờ, không chồng lấn. Dùng seed cố định `20261025`, lấy trạm theo ánh xạ hiện có trong `mart.dim_substation`.

| Kịch bản | Biên độ và hình dạng | Thời lượng |
|---|---|---|
| A — quá tải kéo dài (`OVERLOAD_SUSTAINED`) | Từ mức nền sạch (P50–P85 của hiệu chuẩn) lên 110% định mức, theo dốc tuyến tính. | Tăng 2 giờ, giữ 1–2 giờ, giảm 1–2 giờ. Thời gian tăng là quy ước; đỉnh 110% dựa trên ngưỡng nguy hiểm và dải hệ số định mức EDA. |
| B — spike tải (`LOAD_SPIKE`) | Cộng ít nhất 2 × P99,9 bước nhảy tự nhiên: khoảng 90 pp ETTm1, 24 pp ETTm2. | 1–2 mẫu (15–30 phút), rồi trở về đường tải gốc. |
| C — ramp bất thường (`OVERLOAD_RAMP`) | Tăng tuyến tính từ nền sạch tới 110% hoặc 120%; chọn tốc độ quanh P95–P99 tăng tải 1 giờ của từng chuỗi. | Giữ và giảm mỗi lần 1–2 giờ; thời gian tăng suy ra từ biên độ chia cho tốc độ đã chọn. |

Trong schema, C lưu `event_type=OVERLOAD`, phân biệt bằng `scenario_id=OVERLOAD_RAMP`; A cũng là `OVERLOAD`, B là `SPIKE`. Không tạo `SENSOR_FAULT` trong bộ ba kịch bản này.

## Nhãn, breach và OT

- `t_start`: thời điểm bắt đầu cộng độ lệch; `t_end`: mẫu cuối cùng bị bơm. `t_breach`: mẫu đầu tiên trong `[t_start,t_end]` có `load_pct >= 100` (ngưỡng RED mở trong `param_threshold`). Nếu không vượt ngưỡng, lưu NULL.
- Với A/C, OT tăng theo tải sau **8 mẫu (2 giờ)**: `OT_inj(t) = OT(t) + β × Δload(t−8)`. Chốt `β` riêng theo chuỗi bằng `(P95(OT) − median(OT)) / 25 pp`, thống kê trên 16 tháng hiệu chuẩn. Như vậy ramp tăng 25 pp tạo mức tăng OT bằng dải median→P95 quan sát được; đây là quy ước mô phỏng, không phải mô hình nhiệt máy biến áp. Chạy độ nhạy `k=4, 16, 48` mẫu (1, 4, 12 giờ). EDA không xác nhận lag vật lý ổn định: ETTm1 có đỉnh tương quan phần dư yếu, rộng ở 12 giờ; ETTm2 tương quan gần 0.
- Ghi theo schema `mart.eval_event_label`: `event_id, substation_id, event_type, t_start, t_breach, t_end, magnitude, scenario_id, description`. `magnitude` là đỉnh `load_pct` sau bơm. Không dùng tên cột thay thế như `station`, `type`, `amplitude`.

## Ba phương pháp

Tham số cố định từ 16 tháng hiệu chuẩn; cùng đầu vào, nhãn và quy tắc ghép cho cả ba lần chạy. CHECK constraint trong `mart.eval_alert` quy định tên `method`.

| `method` | Quy tắc phát hiện | Tham số và đầu ra |
|---|---|---|
| `THRESHOLD_ONLY` | Hysteresis trên `load_pct`; mở YELLOW/RED tại 90/100%, đóng dưới 80/90%. Cần 2 mẫu liên tiếp để mở/đóng. | `param_threshold`; `alert_type=OVERLOAD`, `detection_layer=THRESHOLD`. |
| `THRESHOLD_ZSCORE` | Phát nếu ngưỡng tải **hoặc** z-score mở cảnh báo; hysteresis và 2 mẫu liên tiếp cho từng điều kiện. `z=(HUFL−mean_value)/std_used`. | Ngưỡng z YELLOW/RED: ETTm1 3,0/3,5; ETTm2 2,0/2,5. Mean/std từ `param_profile`; ETTm1 dùng std theo thứ×giờ, ETTm2 dùng `pooled_hour`. Ghi layer `THRESHOLD` hoặc `ZSCORE`. |
| `COMBINED_RANKED` | Hợp nhất threshold, z-score và ramp; ramp báo khi độ dốc 1 giờ dự báo chạm 100% trong ≤60 phút. Gộp trigger trùng thành một cảnh báo; lấy mức cao nhất. | Layer `THRESHOLD`, `ZSCORE`, `TREND`; sắp hạng theo trường priority có sẵn. |

`param_profile.csv` mã hóa thứ 0–6; đổi sang ISO 1–7 khi nối với schema. EDA ghi nhận ngưỡng z vượt nền trên VALID cao gấp 3–8 lần ở ETTm1; do dữ liệu nền không có nhãn nên gọi đây là tỷ lệ vượt nền, chưa kết luận là false alarm thật. Chưa dùng OT làm ngưỡng quá nhiệt IEC.

## Ghép cảnh báo và bốn chỉ số

Trong `eval_alert`, dùng `first_ts` làm `t_alert` theo thời gian dữ liệu; không dùng `opened_at` (thời gian hệ thống). Cảnh báo khớp sự cố khi `t_alert ∈ [t_start, t_end + 1 giờ]` (dung sai 4 mẫu). Ghép tối đa một cảnh báo đầu tiên cho mỗi sự cố; cảnh báo lặp không tăng recall. Cảnh báo không ghép được trong thời gian không có sự cố là báo động nền.

| Chỉ số | Công thức và cách báo cáo |
|---|---|
| Detection rate / recall (%) | Sự cố có cảnh báo khớp ÷ tổng sự cố × 100; miss vẫn ở mẫu số. |
| Detection delay (phút) | `t_alert − t_start`; median và P90 của sự cố được phát hiện; báo riêng số miss. |
| Lead time (phút) | `t_breach − t_alert`; dương là sớm, âm là muộn. Chỉ tính sự cố được phát hiện và có breach; báo median/P90 và số mẫu. |
| False alarm rate | Số đợt cảnh báo không ghép ÷ số ngày không sự cố; gộp các mẫu cùng vòng đời cảnh báo, loại khoảng sự cố cộng dung sai khỏi mẫu số. Báo cả số đợt và số ngày. |

Báo cáo tách theo phương pháp, loại sự cố và trạm. Lưu seed, ánh xạ trạm, hệ số định mức, `k`, `β` và tham số bơm cùng `run_id`. Không tính lại tham số trên 8 tháng đánh giá; ramp tuyến tính và OT trễ là kịch bản mô phỏng, không đại diện đầy đủ cho vận hành/nhiệt thực tế.
