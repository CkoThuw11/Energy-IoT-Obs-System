# Diễn tập Red Alert – Trạm Y (sự cố EV032)

| Mục | Giá trị |
|---|---|
| Ngày diễn tập | 04/11/2026 (giờ thật theo giờ Việt Nam, UTC+7) |
| Người thực hiện | Claude (trợ lý AI) chạy thay người trực, trên máy của Thiện, tài khoản Grafana `admin` |
| Sự cố | `EV032` – TRAM_Y, OVERLOAD, SIMULATED (INJECTED_RAMP): t_start 2018-02-19 18:15, t_breach 19:15, t_end 19:45 |
| Hệ thống | nhánh `main` @ `100e832` (pipeline 2.1, detector 2.2, dashboard 2.3), DB dựng mới bằng `docker compose down -v` |
| Detector | `python detector/detector.py --source db --method B --run-id LIVE` |
| Producer lần 1 | `python producer/producer.py --speed 10 --start "2018-02-19 16:15" --end "2018-02-19 19:00"` |
| Producer lần 2 | `python producer/producer.py --speed 10 --start "2018-02-19 19:00" --end "2018-02-19 20:45"` |

> Ghi chú: thay vì bấm `Ctrl+C` khi thấy Đỏ, producer lần 1 được đặt `--end` để dừng ngay sau mốc 18:45 (mốc dự kiến lên Đỏ).
> Kết quả giống hệt: dữ liệu đứng ở 18:45 trong suốt 4 bước xử lý.

## 1. Diễn biến hệ thống

| Giờ thật | Giờ dữ liệu | Sự kiện |
|---|---|---|
| 01:05:15 | 16:15 | Bắt đầu phát (×10 = 90 giây / mốc 15 phút) |
| 01:20:15 | 18:45 | Producer ghi mốc 18:45, rồi dừng |
| 01:20:26 | 18:45 | Detector mở cảnh báo **Đỏ** (alert_id 1): load_pct 89,6%, tốc độ +20,9 %/giờ, TTB 29,7 phút. Độ trễ từ lúc ghi dữ liệu đến lúc có cảnh báo: **11 giây** |
| 01:20:43 → 01:23:18 | 18:45 (dừng) | Người trực làm 4 bước (mục 2) |
| 01:23:24 | 19:00 | Phát tiếp |
| – | 19:15 | Quá tải thật: 100,9% |
| – | 19:45 | Đỉnh: 103,0% (S_H 21,06) |
| – | 20:15, 20:30 | 84,5% và 84,6%: 2 lần đo liên tiếp < 85% |
| 01:32:27 | 20:30 | Cảnh báo **tự đóng** (RESOLVED) |

**Lead time** (tính theo giờ dữ liệu) = t_breach − lúc lên Đỏ = 19:15 − 18:45 = **30 phút**.
Cảnh báo **mở thẳng ở mức Đỏ, không qua Vàng**: lúc 18:30 load_pct là 83,4% và TTB 68 phút, chưa tới ngưỡng Vàng; đến mốc 18:45 TTB đã ≤ 30 phút.

## 2. Bảng thời gian 4 bước (giờ thật)

| Bước | Bắt đầu | Kết thúc | Thời gian | Giới hạn | Đạt? |
|---|---|---|---|---|---|
| 1. Xác minh | 01:20:43 | 01:21:36 | 0'53" | 2' | ✔ |
| 2. Tiếp nhận | 01:21:36 | 01:22:24 | 0'48" | 1' | ✔ |
| 3. Đánh giá | 01:22:24 | 01:22:52 | 0'28" | 3' | ✔ |
| 4. Hành động | 01:22:52 | 01:23:18 | 0'26" | 10' | ✔ |
| **Tổng** | 01:20:43 | 01:23:18 | **2'35"** | **16'** | **✔** |

Thời gian phản ứng ghi trong `fact_alert`: `acked_at − opened_at` = 01:22:02,7 − 01:20:26,5 = **1'36"** (màn hình KPI làm tròn thành "2 mins").

## 3. Nội dung từng bước

**Bước 1 – Xác minh** (`02_verify.png`)
- Hàng đợi chỉ có 1 cảnh báo của Trạm Y: OVERLOAD, Đỏ. **Không có cảnh báo SENSOR** đang mở.
- Số liệu không đứng yên: S_H 5 mốc gần nhất là 14,05 → 14,62 → 15,64 → 17,06 → 18,33.
- Kết luận: cảnh báo là thật, không phải do cảm biến hỏng.

**Bước 2 – Tiếp nhận** (`03_ack.png`)
- Bấm "Tiếp nhận" lúc 01:22:03. Trạng thái chuyển OPEN → **ACK – đã nhận**, `acked_by = admin`, và API đưa về lại dashboard.

**Bước 3 – Đánh giá** (`04_assess.png`)

| Con số | Cách tính | Giá trị |
|---|---|---|
| load_pct Trạm Y | S_H / rated_load = 18,33 / 20,45 | 89,6% |
| TTB | (100 − 89,6) / 20,9 × 60 | 29,7 phút (dashboard hiển thị 30) |
| Cần giảm (hiện tại) | S_H − 90% × rated_load = 18,33 − 0,9 × 20,45 | **−0,08** (dashboard hiển thị "Phần vượt 90%" = 0,00) |
| Cần giảm (khi chạm 100%) | 10% × 20,45 | **2,05** |
| Cần giảm (đỉnh thực tế 19:45) | 21,06 − 18,405 | 2,66 |
| Trạm 3 nhận được | 90% × 27,86 − S_H Trạm 3 = 25,07 − 14,82 | **10,25** (Trạm 3 đang ở 53,2%) |
| Nhóm khách hàng bị ảnh hưởng | dim_customer_group | Bệnh viện 10% (ưu tiên), Khu công nghiệp 45%, Dân cư 45% |

Lượng "cần giảm" hiện tại âm vì cảnh báo Đỏ được bật do **TTB** (dự báo), lúc tải mới ở 89,6%. Người trực vì vậy dùng mức cần giảm dự kiến (2,05, đỉnh 2,66) để chuẩn bị.

**Bước 4 – Hành động** (`05_action.png`)
- Xét theo thứ tự: (a) chuyển tải sang Trạm 3 → (b) đề nghị khách hàng lớn giảm tiêu thụ → (c) nguồn dự phòng → (d) cắt giảm có kế hoạch.
- **Chọn (a): chuyển khoảng 2,7 đơn vị tải từ Trạm Y sang Trạm 3.** Trạm 3 còn trống 10,25, lớn hơn mức cần 2,66 khoảng 3,9 lần. Sau khi nhận, Trạm 3 ≈ (14,82 + 2,66) / 27,86 = **62,7%**, vẫn dưới 90%.
- Lúc tải Trạm Y đạt đỉnh (19:45), Trạm 3 vẫn còn trống 7,92 (lúc đóng cảnh báo 20:30 còn 7,21), nên vẫn đủ.
- Không cần dùng (b), (c), (d). Giữ nguyên cấp điện cho bệnh viện.
- Hệ thống giả lập không có thao tác chuyển tải, nên đây là **quyết định điều độ**. Dữ liệu phát tiếp vẫn là kịch bản gốc, chưa trừ phần tải đã chuyển.

**Sau đó** (`06_resolved.png`, `07_kpi.png`)
- Cảnh báo tự đóng ở mốc 20:30 sau 2 lần đo < 85%. Hàng đợi hiển thị RESOLVED, người nhận admin.
- KPI: Trạm Y có 1 cảnh báo Đỏ, thời gian phản ứng trung bình 2 phút, Chưa ai nhận = 0, Bị bỏ lỡ = 0.

## 4. Nhận xét

1. **Quy trình đạt:** 4 bước hết 2'35", còn dư khoảng 13 phút so với giới hạn 16 phút. Cộng thêm, lúc lên Đỏ TTB còn 30 phút, nên còn thời gian để thao tác chuyển tải ngoài hiện trường có hiệu lực.
2. **Lead time 30 phút** đúng như kế hoạch. Phương pháp A (chỉ dùng ngưỡng) chỉ lên Đỏ lúc 19:15, tức lead time 0.
3. **Màu ở màn hình 1 không khớp mức cảnh báo:** ô "load_pct hiện tại" và cột load_pct của Trạm Y vẫn **xanh lá** (89,6% < 90%) trong khi cảnh báo đã Đỏ do TTB. Người trực có thể đánh giá thấp mức khẩn cấp nếu chỉ nhìn màn hình 1.
4. **Biểu đồ "load_pct 24 giờ" và "load_pct và OT 24 giờ" không hiển thị đường tải.** Chỉ thấy đường ngưỡng nét đứt; trục X ghi "0 hour … 4 days" dù dữ liệu chỉ có khoảng 4 giờ. Có thể là lỗi đơn vị trục X, cần nhánh dashboard kiểm tra.
5. **Nút "Tiếp nhận" nằm ở cột cuối bảng hàng đợi,** bị khuất khi màn hình hẹp và phải cuộn ngang mới thấy.
6. **Ô TTB màu đỏ cả khi "Tải không tăng"** (tốc độ âm, sau 20:00). Màu này dễ gây hiểu nhầm là vẫn còn nguy cơ.

## 5. Hạn chế của buổi diễn tập

- Người thực hiện là trợ lý AI, không phải người trực thật. Thời gian thao tác của người thật sẽ dài hơn; nên diễn tập lại với thành viên nhóm để có số liệu thực tế.
- Khi producer dừng, giờ dữ liệu cũng dừng nên TTB đứng yên trong lúc xử lý. Thực tế tải vẫn tiếp tục tăng.
- Bước chuyển tải chỉ là quyết định ghi trên giấy, dữ liệu không thay đổi theo quyết định đó.

## 6. Ảnh chụp

| File | Nội dung |
|---|---|
| `01_red_alert.png` | Toàn bộ dashboard ngay khi cảnh báo Đỏ xuất hiện (OPEN – chưa nhận) |
| `02_verify.png` | Hàng đợi cảnh báo: chỉ có OVERLOAD Đỏ, không có SENSOR |
| `03_ack.png` | Sau khi bấm Tiếp nhận: ACK – đã nhận, người nhận admin |
| `04_assess.png` | Chi tiết Red Alert: load_pct 89,6%, tốc độ 20,9, TTB 30 phút, công suất dư Trạm 3 = 10,25 |
| `05_action.png` | Chuyển tải sang Trạm 3 và nhóm khách hàng của Trạm Y |
| `06_resolved.png` | Sau mốc 20:30: cảnh báo RESOLVED, Trạm Y 84,6% |
| `07_kpi.png` | KPI: 1 cảnh báo Đỏ, thời gian phản ứng 2 phút, không bỏ lỡ |
