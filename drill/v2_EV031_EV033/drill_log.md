# Diễn tập v2 – EV031 (quá tải) và EV033 (cảm biến kẹt), Trạm Y

| Mục | Giá trị |
|---|---|
| Ngày | 14/11/2026, giờ Việt Nam |
| Hệ thống | nhánh `feature_dashboard_v2` @ `1e4aa07`, DB `energy` mới (`docker compose up` trên volume trống) |
| Người trực | Trợ lý AI thao tác trên Streamlit bằng Playwright ("Người trực A") |
| Tốc độ | ×20 (45 giây thật / mốc 15 phút) |
| Khoảng phát | EV031: 10/02/2018 18:45 → 23:30; EV033: 23/02/2018 02:30 → 06:30 (1 giờ trước `t_start`, như yêu cầu R2) |
| Giữa hai đoạn | Chỉ xóa `raw`, `cleaned`, watermark; giữ `fact_alert`, `fact_alert_action` (như R3). Checkpoint detector được đặt lại về `{}` thay vì xóa, vì detector từ chối chạy khi có cảnh báo mà thiếu checkpoint |

## 1. Diễn biến

| Giờ thật | Giờ dữ liệu | Sự kiện |
|---|---|---|
| 17:01:53 | 10/02 18:45 | Bắt đầu phát EV031 |
| 17:04:58 | 19:45 | Cảnh báo #2 Vàng (luật TTB, tải 91,5%, còn 45 phút) |
| 17:05:05 | 19:45 | Bấm **Theo dõi** (0 phút dữ liệu) |
| 17:08:49 | 21:00 | #2 lên **Đỏ** theo ngưỡng: tải tăng vọt 93,1% → 105,2% trong một mốc |
| 17:09:00 | 21:00 | **Xác nhận chuyển tải** 6,70 sang Trạm 3 (điền sẵn), 0 phút dữ liệu |
| – | 21:15 | Tải mô phỏng Trạm Y 73,2%; Trạm 3 89,6% (dưới Vàng) |
| – | 21:30 | #2 tự đóng (tải mô phỏng < 85% hai lần) |
| – | 23:00 | Trả tải **SAFE** (tải đo < 85% hai lần) |
| 17:16:48 | 23/02 02:30 | Phát EV033 (lần 1 detector không chạy do thiếu checkpoint; chạy lại lúc 17:30:16) |
| 17:35:37 | 04:15 | Cảnh báo #3 **Cảm biến** (S_H đứng yên từ 03:30) |
| 17:35:47 | 04:15 | Bấm **Chuyển bảo trì** |
| – | 04:45 | #3 tự đóng khi số liệu thay đổi trở lại |

## 2. Số liệu quyết định lúc lên Đỏ (21:00)

| | Giá trị |
|---|---|
| S_H Trạm Y / định mức | 21,51 / 20,45 (105,2%), tốc độ +12,3%/giờ |
| Cần giảm ngay (về < 85%) | 4,13 |
| Cần giảm nếu tải tăng tiếp 1 giờ | 6,65 → điền sẵn 6,7 |
| Trạm 3 nhận thêm tối đa | 7,61 (Trạm 3 đang 62,7%) |
| Dự kiến ngay sau chuyển | Trạm Y 72,4% · Trạm 3 86,7% |

Tải đo của Trạm Y ≥ 100% ở 3 mốc (21:00, 21:15 đỉnh 106,0%, 21:30). Có chuyển tải, tải mô phỏng chỉ ≥ 100% ở mốc 21:00.

## 3. KPI (trang Báo cáo quản trị)

| KPI | Kết quả |
|---|---|
| Đỏ chuyển tải đúng hạn (≤ 30′) | 1/1, sau 0 phút dữ liệu |
| Đỏ bị bỏ lỡ | 0 |
| Báo giả / tổng Đỏ | 0/1 |
| Chuyển tải: số lần / tổng lượng | 1 / 6,70 |
| Trả tải an toàn / hết giờ / quá hạn | 1 / 0 / 0 |

## 4. Nhận xét

1. EV031 không được báo Đỏ trước khi chạm 100%: tải tăng vọt 12 điểm phần trăm trong một mốc 15 phút nên TTB không kịp. Cảnh báo sớm là mức Vàng lúc 19:45, trước 75 phút.
2. Phát từ 1 giờ trước `t_start` thì Vàng mở lúc 19:45 (không phải 19:15), vì detector cần 4 bản ghi để tính tốc độ.
3. R3 cần sửa code: `reset_replay.sql` xóa cảnh báo; nếu chỉ xóa checkpoint thì detector từ chối chạy ("run_id đã có cảnh báo nhưng thiếu checkpoint").
4. "Vàng theo dõi đúng hạn" hiện 0/0 vì cảnh báo Vàng đã nâng lên Đỏ được tính là Đỏ.
5. Nút "Chuyển bảo trì" nằm ở hàng đợi trang Điều hành, không có trên trang Chi tiết trạm.

## 5. Ảnh

| File | Nội dung |
|---|---|
| `h51_vang_dieu_hanh.png` | Điều hành khi Trạm Y lên Vàng |
| `h52_do_chi_tiet_tram.png` | Chi tiết trạm khi lên Đỏ: Trạm 3 ở trên, khung chuyển tải |
| `h53_tai_do_mo_phong.png` | Kết quả xử lý, biểu đồ tải đo / mô phỏng, lịch sử chuyển tải |
| `h54_cam_bien_dieu_hanh.png` | Điều hành khi cảm biến kẹt (ô xám, nút Chuyển bảo trì) |
| `h55_bao_cao_quan_tri.png` | Báo cáo quản trị sau hai đoạn |
| `01*` … `06*` | Ảnh gốc chưa cắt |
