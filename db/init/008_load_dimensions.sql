-- =============================================================================
-- 008_load_dimensions.sql
-- Nạp dữ liệu chiều (dimension seed data) vào schema mart.
--
-- Ghi chú nguồn gốc dữ liệu:
--   attr_source = 'SIMULATED' → dữ liệu do nhóm tự tạo (không lấy từ nguồn thực tế).
--   rated_load  = NULL        → để trống, chờ Thiện điền sau khi hiệu chuẩn P99
--                               trên 16 tháng đầu của ETT (trong notebook EDA).
--
-- Thiết kế hệ số định mức (rating_factor):
--   S1 / Trạm Y : 1.10 → định mức ≈ P99 × 1.10  (đây là trạm có sự cố Red Alert)
--   S2 / Trạm 2 : 1.10 → tương đương Trạm Y
--   S3 / Trạm 3 : 1.35 → định mức ≈ P99 × 1.35  (sức chứa lớn, luôn còn dư
--                          công suất → ưu tiên nhận chuyển tải khi lưới quá tải)
--   S4 / Trạm 4 : 1.15 → dự phòng thứ cấp
--
-- Thiết kế nhóm khách hàng (dim_customer_group):
--   Mỗi trạm có 3 nhóm: công nghiệp (M), thương mại (L), dân dụng (L).
--   load_share tổng = 1.000 cho mỗi trạm.
--   shed_priority: 1 = sa thải trước (dân dụng), 3 = bảo vệ (công nghiệp).
--   is_critical = TRUE  → không được sa thải (nhóm công nghiệp nặng).
--
-- Cách chạy:
--   Trong container:
--     docker compose exec timescaledb \
--       psql -U $POSTGRES_USER -d $POSTGRES_DB \
--       -f /docker-entrypoint-initdb.d/008_load_dimensions.sql
--
--   Hoặc trực tiếp từ host (cần psql):
--     psql -h localhost -U <user> -d <db> -f db/init/008_load_dimensions.sql
--
--   Hoặc chạy script Python:
--     python data/load_dimensions.py
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. dim_substation
-- ---------------------------------------------------------------------------
-- Xóa và nạp lại hoàn toàn để đảm bảo idempotent.
-- RESTART IDENTITY CASCADE sẽ reset sequence và xóa các bảng phụ thuộc FK.
TRUNCATE mart.dim_substation RESTART IDENTITY CASCADE;

INSERT INTO mart.dim_substation
    (substation_id, substation_code, display_name,
     source_series, reading_source_type,
     rating_factor, rated_load,
     criticality, neighbor_substation_id, attr_source)
VALUES
-- S1 – Trạm Y: trạm REAL từ ETTm1, hệ số thấp, là trạm xảy ra Red Alert
    (1, 'S1', 'Trạm Y',  'ETTm1', 'REAL',    1.10, NULL, 3, 3, 'SIMULATED'),
-- S2 – Trạm 2: trạm REAL từ ETTm2, tải quan trọng (criticality=4)
    (2, 'S2', 'Trạm 2',  'ETTm2', 'REAL',    1.10, NULL, 4, 4, 'SIMULATED'),
-- S3 – Trạm 3: DERIVED từ ETTm1 (dịch 7 ngày + nhiễu), hệ số CAO nhất (1.35)
--   → định mức lớn → luôn còn dư công suất → ưu tiên nhận chuyển tải từ Trạm Y
    (3, 'S3', 'Trạm 3',  'ETTm1', 'DERIVED', 1.35, NULL, 2, 1, 'SIMULATED'),
-- S4 – Trạm 4: DERIVED từ ETTm2 (dịch 14 ngày + nhiễu), dự phòng thứ cấp
    (4, 'S4', 'Trạm 4',  'ETTm2', 'DERIVED', 1.15, NULL, 3, 2, 'SIMULATED');

-- ---------------------------------------------------------------------------
-- 2. dim_customer_group
-- ---------------------------------------------------------------------------
TRUNCATE mart.dim_customer_group RESTART IDENTITY;

INSERT INTO mart.dim_customer_group
    (customer_group_id, substation_id, voltage_side, group_name,
     load_share, shed_priority, is_critical, attr_source)
VALUES
-- Trạm Y (S1): SIMULATED – phân bổ tải theo phía trung / hạ áp
    (1,  1, 'M', 'Công nghiệp nặng (Trạm Y – trung áp)',       0.420, 3, TRUE,  'SIMULATED'),
    (2,  1, 'L', 'Thương mại & dịch vụ (Trạm Y – hạ áp)',     0.340, 2, FALSE, 'SIMULATED'),
    (3,  1, 'L', 'Dân dụng (Trạm Y – hạ áp)',                  0.240, 1, FALSE, 'SIMULATED'),
-- Trạm 2 (S2): SIMULATED
    (4,  2, 'M', 'Công nghiệp nhẹ (Trạm 2 – trung áp)',        0.380, 3, TRUE,  'SIMULATED'),
    (5,  2, 'L', 'Thương mại (Trạm 2 – hạ áp)',                0.360, 2, FALSE, 'SIMULATED'),
    (6,  2, 'L', 'Dân dụng (Trạm 2 – hạ áp)',                  0.260, 1, FALSE, 'SIMULATED'),
-- Trạm 3 (S3): SIMULATED – trạm nhận chuyển tải, tải công nghiệp chiếm tỷ trọng cao
    (7,  3, 'M', 'Công nghiệp nặng (Trạm 3 – trung áp)',       0.450, 3, TRUE,  'SIMULATED'),
    (8,  3, 'L', 'Thương mại & dịch vụ (Trạm 3 – hạ áp)',     0.330, 2, FALSE, 'SIMULATED'),
    (9,  3, 'L', 'Dân dụng (Trạm 3 – hạ áp)',                  0.220, 1, FALSE, 'SIMULATED'),
-- Trạm 4 (S4): SIMULATED
    (10, 4, 'M', 'Công nghiệp nhẹ (Trạm 4 – trung áp)',        0.400, 3, TRUE,  'SIMULATED'),
    (11, 4, 'L', 'Thương mại (Trạm 4 – hạ áp)',                0.350, 2, FALSE, 'SIMULATED'),
    (12, 4, 'L', 'Dân dụng (Trạm 4 – hạ áp)',                  0.250, 1, FALSE, 'SIMULATED');

-- ---------------------------------------------------------------------------
-- 3. scenario_mapping
-- ---------------------------------------------------------------------------
TRUNCATE mart.scenario_mapping;

INSERT INTO mart.scenario_mapping
    (substation_id, mapping_version, source_series, shift_days, noise_pct, random_seed)
VALUES
-- S1/S2: dữ liệu gốc, không dịch, không nhiễu
    (1, 'v1', 'ETTm1',  0, 0.00, NULL),
    (2, 'v1', 'ETTm2',  0, 0.00, NULL),
-- S3: dịch 7 ngày + nhiễu 2.5%, seed cố định để tái lập
    (3, 'v1', 'ETTm1',  7, 2.50, 42),
-- S4: dịch 14 ngày + nhiễu 3%, seed cố định
    (4, 'v1', 'ETTm2', 14, 3.00, 99);

COMMIT;

-- Kiểm tra nhanh sau khi nạp
SELECT
    s.substation_code,
    s.display_name,
    s.reading_source_type,
    s.rating_factor,
    s.criticality,
    ns.substation_code AS neighbor_code,
    sm.shift_days,
    sm.noise_pct,
    (SELECT COUNT(*) FROM mart.dim_customer_group cg
     WHERE cg.substation_id = s.substation_id) AS n_customer_groups
FROM mart.dim_substation s
LEFT JOIN mart.dim_substation ns ON ns.substation_id = s.neighbor_substation_id
LEFT JOIN mart.scenario_mapping sm
       ON sm.substation_id = s.substation_id AND sm.mapping_version = 'v1'
ORDER BY s.substation_id;
