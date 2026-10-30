# ERD – Kho dữ liệu

## 1. Tổng quan
Kho chia thành 4 lớp. Mọi bảng nối với `dim_substation` qua khóa `station_id`.

| Lớp | Bảng | Vai trò |
|---|---|---|
| Tham chiếu | `dim_substation`, `dim_customer_group`, `param_threshold` | Mô tả trạm, khách hàng, định mức và ngưỡng |
| Cảm biến | `raw` → `cleaned` → `agg_hourly`, `etl_watermark` | Số liệu đo 15 phút, đã làm sạch và tổng hợp theo giờ |
| Cảnh báo | `fact_alert` | Mỗi dòng là 1 cảnh báo và vòng đời của nó |
| Đánh giá | `eval.event_label` | Đáp án sự cố, schema riêng, detector không đọc |

Phần 6 gộp các lớp trên thành star schema.

---

## 2. Lớp tham chiếu

```mermaid
erDiagram
    dim_substation {
        text station_id PK
        text station_name
        text source
        smallint shift_days
        text data_origin
        smallint importance
        text neighbor_station_id FK
        text note
    }
    dim_customer_group {
        text group_id PK
        text station_id FK
        text group_name
        text customer_type
        numeric load_share_pct
        boolean is_priority
        text note
    }
    param_threshold {
        text station_id PK, FK
        text source
        float p99_sh
        float k
        float rated_load
        smallint yellow_pct
        smallint red_pct
        smallint yellow_ttb_min
        smallint red_ttb_min
        smallint close_pct
        smallint close_records
        smallint stuck_records
    }

    dim_substation |o--o{ dim_substation : "nhận chuyển tải"
    dim_substation ||--o{ dim_customer_group : "phục vụ"
    dim_substation ||--|| param_threshold : "định mức, ngưỡng"
```

| Bảng | Grain | Nội dung | Nguồn |
|---|---|---|---|
| dim_substation | 1 trạm | Nguồn ETT, số ngày dời, REAL/DERIVED, độ quan trọng (1–3), trạm lân cận | `001_schema.sql` (SIMULATED) |
| dim_customer_group | 1 nhóm khách hàng | Loại khách hàng, % tải của trạm, có ưu tiên giữ điện hay không | `001_schema.sql` (SIMULATED) |
| param_threshold | 1 trạm | `rated_load = p99_sh × k`; ngưỡng Vàng 90 / Đỏ 100 / đóng 85; TTB 60 / 30; đứng yên 4 bản ghi | `param_threshold.csv` (Task 1.2) |

- **Tự tham chiếu:** TRAM_Y → TRAM_3 và TRAM_2 → TRAM_4. Khóa ngoại đặt `DEFERRABLE` nên nạp dữ liệu không cần đúng thứ tự dòng.
- **Quan hệ 1–1** giữa `dim_substation` và `param_threshold`: mỗi trạm đúng 1 dòng tham số.

---

## 3. Lớp cảm biến

```mermaid
erDiagram
    dim_substation {
        text station_id PK
    }
    raw {
        timestamp ts PK
        text station_id PK, FK
        float hufl
        float hull
        float mufl
        float mull
        float lufl
        float lull
        float ot
        timestamptz ingested_at
    }
    cleaned {
        timestamp ts PK
        text station_id PK, FK
        float hufl_to_lull "6 cột tải như raw"
        float ot
        float s_h
        float load_pct
        text quality_flag
        timestamptz processed_at
    }
    agg_hourly {
        timestamp bucket
        text station_id
        float avg_load_pct
        float max_load_pct
        float avg_ot
        float max_ot
        bigint n_records
    }
    etl_watermark {
        text job_name PK
        text station_id PK, FK
        timestamp last_ts
        timestamptz updated_at
    }

    dim_substation ||--o{ raw : "đo tại"
    raw ||--|| cleaned : "ELT"
    dim_substation ||--o{ cleaned : "đo tại"
    cleaned ||--o{ agg_hourly : "tổng hợp theo giờ"
    dim_substation ||--o{ etl_watermark : "mốc đã xử lý"
```

| Bảng | Loại | Grain | Khóa | Người ghi |
|---|---|---|---|---|
| raw | Hypertable | 1 trạm × 15 phút | (station_id, ts) | Producer |
| cleaned | Hypertable | 1 trạm × 15 phút | (station_id, ts) | Job ELT |
| agg_hourly | Continuous aggregate | 1 trạm × 1 giờ | (bucket, station_id) | TimescaleDB, refresh mỗi phút |
| etl_watermark | Bảng thường | 1 job × 1 trạm | (job_name, station_id) | Job ELT |

- `s_h = √(hufl² + hull²)`; `load_pct = s_h / rated_load × 100`.
- `raw` và `cleaned` **không có cột nhãn nguồn**, nên detector không thể nhìn đáp án.
- `n_records < 4` trong `agg_hourly` nghĩa là giờ đó thiếu bản ghi.

---

## 4. Lớp cảnh báo

```mermaid
erDiagram
    dim_substation {
        text station_id PK
    }
    param_threshold {
        text station_id PK, FK
    }
    fact_alert {
        bigint alert_id PK
        text run_id
        text station_id FK
        text alert_type "OVERLOAD / SENSOR"
        text severity "RED / YELLOW / SENSOR"
        text trigger_rule "THRESHOLD / TTB / STUCK / MISSING"
        text status "OPEN / ACK / RESOLVED"
        timestamp opened_ts
        timestamp escalated_ts
        timestamp last_ts
        timestamp resolved_ts
        timestamptz opened_at
        timestamptz acked_at
        text acked_by
        timestamptz resolved_at
        float load_pct
        float peak_load_pct
        float ttb_min
        float speed_pct_h
        smallint below_close_count
        timestamptz updated_at
    }

    dim_substation ||--o{ fact_alert : "cảnh báo"
    dim_substation ||--|| param_threshold : "ngưỡng dùng để phát hiện"
```

| Thuộc tính | Giá trị |
|---|---|
| Loại fact | Accumulating snapshot (1 dòng được cập nhật suốt vòng đời) |
| Grain | 1 cảnh báo |
| Measure | `load_pct`, `peak_load_pct`, `ttb_min`, `speed_pct_h` |
| Mốc dữ liệu | `opened_ts` → `escalated_ts` (lên Đỏ) → `resolved_ts` |
| Mốc giờ thật | `opened_at` → `acked_at` → `resolved_at` |
| Người ghi | Detector (mở, cập nhật, đóng); FastAPI (ACK) |

- **Vòng đời:** OPEN → ACK → RESOLVED. Cảnh báo đóng khi `below_close_count` đạt `close_records` (2). RESOLVED mà chưa ACK được tính là bị bỏ lỡ.
- **`run_id`:** LIVE (dashboard), EVAL_A, EVAL_B (đánh giá), DRILL (diễn tập). Không có bảng `eval_alert` riêng.

---

## 5. Lớp đánh giá

```mermaid
erDiagram
    dim_substation {
        text station_id PK
    }
    eval_event_label {
        text event_id PK
        text station_id FK
        text type "OVERLOAD / SENSOR"
        timestamp t_start
        timestamp t_breach "chỉ OVERLOAD"
        timestamp t_end
        text source_type "REAL / SIMULATED"
        text detail
    }
    fact_alert {
        bigint alert_id PK
        text run_id
        text station_id FK
        text alert_type
        timestamp opened_ts
        timestamp escalated_ts
    }

    dim_substation ||--o{ eval_event_label : "sự cố"
    eval_event_label |o..o{ fact_alert : "khớp khi đánh giá"
```

| Thuộc tính | Giá trị |
|---|---|
| Bảng | `eval.event_label` (schema `eval`) |
| Grain | 1 sự cố |
| Nội dung | 38 sự cố: 9 OVERLOAD SIMULATED, 9 OVERLOAD REAL (Trạm Y), 20 SENSOR REAL |
| `detail` | INJECTED_RAMP, REAL_PEAK, DAY31_FILL, STUCK_HV |
| Nguồn | `event_label.csv` (Task 1.3) |

- **Quan hệ khớp với `fact_alert` là logic, không có khóa ngoại.** Một cảnh báo khớp một sự cố khi cùng `station_id`, cùng loại, và `opened_ts` ∈ [`t_start`, `t_end` + 1 giờ]. Nhiều cảnh báo cho cùng một sự cố chỉ tính 1 lần.
- **Chỉ số đánh giá:**
  - Độ trễ phát hiện = `opened_ts` − `t_start`.
  - Lead time = `t_breach` − `escalated_ts`.

---

## 6. Star schema
Hai bảng fact dùng chung dimension `dim_substation`. `param_threshold` và `dim_customer_group` là nhánh mở rộng của trạm (snowflake). Thời gian dùng trực tiếp cột `ts`, không có `dim_time`.

```mermaid
erDiagram
    dim_substation {
        text station_id PK
        text station_name
        text data_origin
        smallint importance
        text neighbor_station_id FK
    }
    param_threshold {
        text station_id PK, FK
        float rated_load
    }
    dim_customer_group {
        text group_id PK
        text station_id FK
        text customer_type
        boolean is_priority
    }
    cleaned {
        timestamp ts PK
        text station_id PK, FK
        float s_h
        float load_pct
        float ot
    }
    agg_hourly {
        timestamp bucket
        text station_id
        float avg_load_pct
        float max_load_pct
    }
    fact_alert {
        bigint alert_id PK
        text station_id FK
        text severity
        text status
        float peak_load_pct
        float ttb_min
    }

    dim_substation ||--o{ cleaned : "FACT số liệu đo"
    dim_substation ||--o{ fact_alert : "FACT cảnh báo"
    cleaned ||--o{ agg_hourly : "tổng hợp"
    dim_substation ||--|| param_threshold : "định mức"
    dim_substation ||--o{ dim_customer_group : "khách hàng"
```

| Bảng | Vai trò | Grain | Dùng cho màn hình |
|---|---|---|---|
| cleaned | Fact số liệu đo (transaction) | 1 trạm × 15 phút | 1 Tổng quan, 3 Chi tiết Red Alert |
| agg_hourly | Fact tổng hợp (periodic snapshot) | 1 trạm × 1 giờ | 1 Xu hướng 24 giờ, 4 KPI |
| fact_alert | Fact cảnh báo (accumulating snapshot) | 1 cảnh báo | 2 Danh sách cảnh báo, 4 KPI |
| dim_substation | Dimension trung tâm | 1 trạm | Tất cả |
| param_threshold | Mở rộng của trạm | 1 trạm | 3 Lượng cần giảm, công suất dư Trạm 3 |
| dim_customer_group | Mở rộng của trạm | 1 nhóm khách hàng | 3 Nhóm khách hàng bị ảnh hưởng |

**View phục vụ dashboard**
| View | Nội dung |
|---|---|
| v_station_latest | Bản ghi mới nhất mỗi trạm, kèm `rated_load` và mức NORMAL / YELLOW / RED |
| v_alert_queue | Cảnh báo đang mở lên trước; xếp Đỏ → Vàng → SENSOR, rồi TTB ngắn, rồi `load_pct` cao; có `is_missed`, `response_sec` |

---

## 7. Quy ước

| Quy ước | Ý nghĩa |
|---|---|
| `station_id` | TRAM_Y, TRAM_2, TRAM_3, TRAM_4 |
| `*_ts` (TIMESTAMP) | Thời gian dữ liệu (2017–2018); dùng cho phát hiện và đánh giá |
| `*_at` (TIMESTAMPTZ) | Giờ thật (`now()`); dùng đo độ trễ và thời gian phản ứng |
| `data_origin` | Trong `dim_substation`. REAL = ETT gốc; DERIVED = dời ngày |
| `source_type` | Chỉ có trong `eval.event_label`. REAL = có sẵn trong dữ liệu gốc; SIMULATED = nhóm bơm vào |

## 8. Ràng buộc quan trọng
- **Chống trùng:** `ux_alert_one_open (run_id, station_id, alert_type) WHERE status IN ('OPEN','ACK')`.
- **Loại khớp mức:** SENSOR ↔ severity SENSOR; OVERLOAD ↔ RED / YELLOW.
- **ACK hợp lệ:** status ACK bắt buộc có `acked_at` và `acked_by`.
- **Đóng hợp lệ:** status RESOLVED ⇔ có `resolved_ts`.
- **Lên Đỏ:** có `escalated_ts` thì severity phải là RED.
- **Ngưỡng hợp lệ:** `close_pct < yellow_pct < red_pct`; `red_ttb_min < yellow_ttb_min`.
- **Nhãn sự cố:** `t_start ≤ t_end`; OVERLOAD bắt buộc có `t_breach` ∈ [`t_start`, `t_end`]; SENSOR không có `t_breach`.