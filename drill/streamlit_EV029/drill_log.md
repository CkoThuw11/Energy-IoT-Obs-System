# Diễn tập Red Alert trên dashboard Streamlit – Trạm Y (EV029)

| Mục | Giá trị |
|---|---|
| Ngày diễn tập | 07/11/2026 (giờ thật theo giờ Việt Nam, UTC+7) |
| Người thực hiện | Claude (trợ lý AI) thao tác thay "Người trực A" trên máy của Thiện |
| Sự cố | `EV029` – TRAM_Y, OVERLOAD, REAL_PEAK: t_start 2018-02-04 18:30, t_breach 2018-02-04 21:15, t_end 2018-02-05 21:30 |
| Hệ thống | nhánh `main` @ `f14ec54` (Streamlit, quy trình 3 bước, tự dừng phát lại, mô phỏng chuyển tải); DB dựng mới bằng `docker compose down -v` |
| Lệnh | `scripts/replay_event.sh EV029 60` (phát 2018-02-04 16:30 → 2018-02-05 23:30, ×60 = 15 giây / mốc) |
| Thời lượng | 20:32:57 → 21:06:46, 125 mốc, 500 dòng |

## 1. Diễn biến

| Giờ thật | Giờ dữ liệu | Sự kiện |
|---|---|---|
| 20:34:52 | 04/02 18:15 | Mở cảnh báo #1 (Vàng, luật TTB) |
| 20:35:07 | 04/02 18:30 | #1 lên **Đỏ**: tải 92,9%, +18,3 %/giờ, TTB 23 phút. Phát lại tự dừng |
| 20:35:53 | – | Bấm "Nhận & xử lý" (sau 1:01) |
| 20:36:12 | – | ① Xác nhận – quá tải thật (sau 1:19) |
| 20:36:42 | – | ② Chuyển 5,40 sang Trạm 3 theo gợi ý (sau 1:49). Phát lại chạy tiếp |
| 20:37:03 | 04/02 19:00 | #1 **tự đóng**: tải mô phỏng < 85% hai lần |
| – | 05/02 01:00 | Trả tải #1: tải đo < 85% hai lần |
| 20:58:53, 20:59:23 | 05/02 16:45, 17:15 | Cảnh báo Vàng #2, #3 (luật TTB, tải 63–69%), mỗi cái tự đóng sau 15 phút |
| 21:00:53 | 05/02 18:45 | Mở cảnh báo #4 (Vàng, luật TTB) |
| 21:01:06 | 05/02 19:00 | #4 lên **Đỏ**: tải 90,8%, +19,4 %/giờ, TTB 28 phút. Phát lại tự dừng |
| 21:01:51 | – | Nhận & xử lý (sau 0:58) |
| 21:02:03 | – | ① Xác nhận (sau 1:09) |
| 21:02:15 | – | ② Chuyển 5,20 sang Trạm 3 (sau 1:21) |
| 21:02:32 | 05/02 19:30 | #4 **tự đóng** |
| 21:06:46 | 05/02 23:30 | Kết thúc phát lại; lần chuyển tải #4 vẫn còn hiệu lực (tải đo 96,8%) |

## 2. Số liệu quyết định (đọc trên trang Chi tiết trạm)

| | Cảnh báo #1 | Cảnh báo #4 |
|---|---|---|
| S_H Trạm Y / định mức | 18,99 / 20,45 | 18,57 / 20,45 |
| Cần giảm ngay (về < 85%) | 1,61 | 1,19 |
| Cần giảm nếu tải tăng tiếp 1 giờ | 5,35 | 5,16 |
| Trạm 3 nhận thêm tối đa | 8,66 (Trạm 3 đang 58,9%) | 7,70 (Trạm 3 đang 62,4%) |
| Lượng chuyển (theo gợi ý) | 5,40 | 5,20 |
| Dự kiến sau chuyển | Trạm Y 66,5% · Trạm 3 78,3% | – |

Tải đo của Trạm Y vẫn lên tới 103,2% ở mốc 2018-02-05 21:30, trong khi tải mô phỏng sau chuyển tải không vượt ngưỡng đóng; nếu không chuyển tải, trạm đã quá tải.

## 3. KPI (trang Báo cáo quản trị)

| KPI | Kết quả | Mục tiêu | Đạt? |
|---|---|---|---|
| Thời gian nhận TB | 0:59 | ≤ 3:00 | ✔ |
| Thời gian chuyển tải TB | 1:35 | ≤ 16:00 | ✔ |
| Đỏ bị bỏ lỡ | 0 | 0 | ✔ |
| Báo giả / tổng Đỏ | 0 / 2 | chỉ theo dõi | – |

## 4. Nhận xét

1. Cả hai cảnh báo Đỏ đều do luật TTB, mở khi tải mới ở 91–93%. Cảnh báo #1 lên Đỏ lúc 18:30, khoảng 2 giờ 45 phút trước t_breach (21:15).
2. Hai cảnh báo Vàng #2, #3 ở mức tải 63–69% là báo nhầm của luật TTB (tải tăng nhanh trong 1 giờ), khớp với điểm yếu nêu ở phần đánh giá.
3. Ở ×60, cảnh báo tự đóng chỉ khoảng 20 giây sau khi chuyển tải, nên ô "Nguyên nhân" của bước ③ biến mất trước khi kịp lưu; nguyên nhân được ghi lại ở thẻ "Kết quả xử lý sự cố Đỏ gần nhất".
4. Ô "Nguyên nhân" phải nhấn Enter (hoặc rời ô) trước khi bấm "Lưu nguyên nhân", nếu không giá trị không được ghi.
5. Đồng hồ /16' tính từ lúc cảnh báo **mở** (khi còn Vàng), không phải lúc lên Đỏ; với #1 lệch khoảng 15 giây.
6. Người thực hiện là trợ lý AI nên thời gian thao tác ngắn hơn người thật.

## 5. Ảnh

| File | Nội dung |
|---|---|
| `01_red_alert.png` | Trang Điều hành khi #1 lên Đỏ, phát lại dừng |
| `02_sau_chuyen_tai.png` | Chi tiết Trạm Y sau khi #1 đóng: 3 bước kèm giờ, chuyển tải đang hiệu lực |
| `03_dieu_hanh_sau_dong.png` | Trang Điều hành sau khi #1 đóng |
| `04_red_alert_lan2.png` | Trang Điều hành khi #4 lên Đỏ |
| `05_bao_cao_quan_tri.png` | Báo cáo quản trị: 4 KPI, biểu đồ cảnh báo, bảng kết quả |
| `06_chi_tiet_tram_ket_qua.png` | Chi tiết Trạm Y: kết quả xử lý #4, biểu đồ tải đo / mô phỏng |
| `07_dieu_hanh_ket_thuc.png` | Trang Điều hành khi kết thúc phát lại |
