Dưới đây là các bước chi tiết. Phần định nghĩa sự cố và 3 phương pháp là **gợi ý của mình**, bạn cần đối chiếu với đề cương và chốt lại với nhóm.

## Bước 0: Gom đầu vào

| Cần lấy | Từ đâu | Dùng để |
|---|---|---|
| Độ trễ k giữa tải và OT, P95/P99 | `eda_ett.ipynb` (Trọng) | Làm OT tăng trễ, đặt ngưỡng nguy hiểm |
| Phân phối tăng tải 1h, ngưỡng z-score, số spike tự nhiên | `eda_load_volatility.ipynb` (Thiện) | Chọn biên độ, phân biệt sự cố với spike tự nhiên |
| `rated_load`, ngưỡng bật/tắt | `param_threshold.csv` (Thiện) | Định nghĩa "mức nguy hiểm" |
| Mean/std theo giờ × thứ | `param_profile.csv` (Thiện) | Phương pháp z-score |
| Bảng `event_label`, `fact_alert` | Schema của Thư | Thống nhất cột để ghi sự cố |

Ghi lại các con số này ở đầu tài liệu để có chỗ dẫn chứng cho biên độ.

## Bước 1: Định nghĩa 3 loại sự cố

Mỗi loại cần một bảng gồm: **cơ chế, biên độ, thời lượng, số lần bơm, trạm (ETTm1/ETTm2), cách bơm vào tải và OT**. Gợi ý:

- **Loại A, quá tải kéo dài:** `load_pct` tăng từ khoảng 85% lên 110% trong 2 giờ, giữ một lúc rồi hạ. OT tăng trễ k.
- **Loại B, spike đột ngột:** tải vọt cao trong thời gian ngắn (15-60 phút). Biên độ lớn hơn spike tự nhiên (ví dụ vượt P99 hoặc z-score cao hơn ngưỡng tự nhiên).
- **Loại C, tăng dốc (ramp):** tải leo nhanh bất thường so với tốc độ tăng 1h trong EDA, thường là loại khó phát hiện sớm nhất.

Cách chọn con số:
- Biên độ: lấy từ P95/P99 và phân phối tốc độ tăng 1h trong EDA, không tự đặt.
- Số lần: đủ để thống kê (ví dụ 10-20 sự cố mỗi loại, mỗi trạm trong 8 tháng cuối), cách nhau tối thiểu một khoảng để không chồng lên nhau.
- Cố định random seed để tái lập được.

## Bước 2: Định nghĩa t_start và t_breach

- **`t_start`**: timestamp bắt đầu bơm sự cố, là ground truth.
- **`t_breach`**: timestamp đầu tiên tải (hoặc OT) vượt mức nguy hiểm, ví dụ `load_pct ≥ 100%` hoặc vượt ngưỡng đã định ở `param_threshold`.
- Cần ghi rõ cách xử lý khi sự cố **không bao giờ breach** (ví dụ spike ngắn): quy ước `t_breach = N/A` và loại khỏi chỉ số lead time.
- Quy tắc khớp cảnh báo với sự cố: cảnh báo được tính là phát hiện đúng nếu `t_alert` nằm trong `[t_start, t_end + dung sai]`.
- Hai đại lượng suy ra: **độ trễ phát hiện = t_alert − t_start** và **lead time = t_breach − t_alert** (dương nghĩa là phát hiện sớm, âm nghĩa là muộn).

## Bước 3: Đặc tả cách bơm sự cố

- Chỉ bơm vào **8 tháng cuối**. Chỉ dùng 16 tháng đầu để hiệu chuẩn tham số.
- Tải: `load_inj(t) = load(t) + Δ · shape(t)`.
- OT: phản ứng với tải bơm vào nhưng **trễ k bước** (hoặc mô hình bậc nhất làm trơn), không tăng cùng lúc. Đây là yêu cầu bắt buộc trong bảng task.
- Không xóa ngoại lai, giữ nguyên dữ liệu gốc, chỉ cộng sự cố lên.
- Mỗi sự cố ghi vào `event_label`: `event_id, type, station, t_start, t_end, t_breach, amplitude`.

## Bước 4: Đặc tả 3 phương pháp phát hiện

Mình không thấy bảng ghi rõ 3 phương pháp là gì. Nếu nhóm chưa chốt, gợi ý: **(1) ngưỡng tĩnh có hysteresis, (2) z-score theo profile giờ × thứ, (3) kết hợp tốc độ tăng tải và OT**. Với mỗi phương pháp viết:
- Input (cột nào) và tham số (lấy từ file param nào, tính trên 16 tháng đầu).
- Điều kiện bật/tắt cảnh báo (ví dụ mở khi ≥ 90%, đóng khi < 85%).
- Điều kiện chống nhiễu (giữ tối thiểu N bước liên tiếp) nếu có.
- Output: cột nào ghi vào `fact_alert`.

## Bước 5: Đặc tả 4 chỉ số

Bảng chưa nêu 4 chỉ số, gợi ý:

| Chỉ số | Công thức | Ghi chú |
|---|---|---|
| Detection rate (recall) | sự cố phát hiện / tổng sự cố bơm | Tính theo loại và trạm |
| Độ trễ phát hiện | `t_alert − t_start` | Báo median và P90 |
| Lead time | `t_breach − t_alert` | Đo "sớm hay muộn" |
| False alarm rate | cảnh báo không khớp sự cố / số ngày | Tính trên đoạn không có sự cố |

Với mỗi chỉ số ghi rõ: công thức, đơn vị, cách xử lý trường hợp đặc biệt (không phát hiện, nhiều cảnh báo cho một sự cố, không breach).

## Bước 6: Viết tài liệu (1-2 trang, markdown)

Cấu trúc đề xuất:
1. Mục đích và dữ liệu đầu vào (kèm các số từ EDA)
2. Bảng 3 loại sự cố
3. Định nghĩa `t_start`, `t_breach`, quy tắc khớp
4. Cách bơm tải và OT trễ k
5. 3 phương pháp
6. 4 chỉ số
7. Nguyên tắc chống rò rỉ (16 tháng hiệu chuẩn, 8 tháng đánh giá)

## Bước 7: Nộp bài

```bash
git checkout -b feature/scenario_assessment
# thêm file .md, ví dụ docs/scenario_assessment.md
git add . && git commit -m "Add incident scenario and evaluation spec"
git push origin feature/scenario_assessment
```

Sau đó nhờ **Trọng** review (cột hỗ trợ), rồi báo **Thư** để khớp schema `event_label` và `fact_alert`.

Task này đã quá hạn 22/10 và các phần sau (bơm sự cố, đánh giá) phụ thuộc vào nó, nên nếu chưa xong thì bạn nên báo nhóm sớm.

Bạn muốn mình soạn sẵn khung markdown với các bảng trống để bạn điền số không?