-- =====================================================================
-- schema.sql – Kho dữ liệu BI cảnh báo quá tải trạm biến áp (TimescaleDB)
-- Nhánh: feature/warehouse-schema
-- Quy ước thời gian:
--   *_ts : thời gian của DỮ LIỆU (TIMESTAMP)  → mọi phép tính phát hiện
--   *_at : GIỜ THẬT (TIMESTAMPTZ, now())      → chỉ đo con người phản ứng
-- Chạy lại nhiều lần không lỗi (IF NOT EXISTS / ON CONFLICT).
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ---------------------------------------------------------------------
-- 1. DIMENSION & THAM SỐ
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_substation (
    station_id          TEXT PRIMARY KEY,                       -- TRAM_Y, TRAM_2, TRAM_3, TRAM_4
    station_name        TEXT NOT NULL,
    source              TEXT NOT NULL CHECK (source IN ('ETTm1', 'ETTm2')),
    shift_days          SMALLINT NOT NULL DEFAULT 0,            -- Trạm 3: 7 ngày, Trạm 4: 14 ngày
    data_origin         TEXT NOT NULL CHECK (data_origin IN ('REAL', 'DERIVED')),
    importance          SMALLINT NOT NULL DEFAULT 2 CHECK (importance BETWEEN 1 AND 3),  -- 3 = quan trọng nhất
    neighbor_station_id TEXT REFERENCES dim_substation (station_id) DEFERRABLE INITIALLY DEFERRED,  -- trạm nhận chuyển tải
    note                TEXT
);

CREATE TABLE IF NOT EXISTS dim_customer_group (
    group_id       TEXT PRIMARY KEY,
    station_id     TEXT NOT NULL REFERENCES dim_substation (station_id),
    group_name     TEXT NOT NULL,
    customer_type  TEXT NOT NULL CHECK (customer_type IN ('HOSPITAL', 'INDUSTRIAL', 'COMMERCIAL', 'RESIDENTIAL', 'PUBLIC')),
    load_share_pct NUMERIC(5, 2) NOT NULL CHECK (load_share_pct BETWEEN 0 AND 100),  -- % tải của trạm
    is_priority    BOOLEAN NOT NULL DEFAULT FALSE,              -- giữ điện khi cắt giảm có kế hoạch
    note           TEXT NOT NULL DEFAULT 'SIMULATED'
);

-- Khớp từng cột với param_threshold.csv (Task 1.2)
CREATE TABLE IF NOT EXISTS param_threshold (
    station_id     TEXT PRIMARY KEY REFERENCES dim_substation (station_id),
    source         TEXT NOT NULL,
    p99_sh         DOUBLE PRECISION NOT NULL,
    k              DOUBLE PRECISION NOT NULL,
    rated_load     DOUBLE PRECISION NOT NULL CHECK (rated_load > 0),
    yellow_pct     SMALLINT NOT NULL,      -- 90
    red_pct        SMALLINT NOT NULL,      -- 100
    yellow_ttb_min SMALLINT NOT NULL,      -- 60
    red_ttb_min    SMALLINT NOT NULL,      -- 30
    close_pct      SMALLINT NOT NULL,      -- 85
    close_records  SMALLINT NOT NULL,      -- 2
    stuck_records  SMALLINT NOT NULL,      -- 4
    CHECK (close_pct < yellow_pct AND yellow_pct < red_pct),
    CHECK (red_ttb_min < yellow_ttb_min)
);

-- ---------------------------------------------------------------------
-- 2. RAW – số liệu nguyên trạng do producer ghi vào (không sửa, không xóa)
--    Không có cột nhãn nguồn/sự cố → detector không "nhìn trộm" đáp án
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw (
    ts          TIMESTAMP NOT NULL,
    station_id  TEXT NOT NULL REFERENCES dim_substation (station_id),
    hufl        DOUBLE PRECISION,
    hull        DOUBLE PRECISION,
    mufl        DOUBLE PRECISION,
    mull        DOUBLE PRECISION,
    lufl        DOUBLE PRECISION,
    lull        DOUBLE PRECISION,
    ot          DOUBLE PRECISION,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (station_id, ts)           -- producer: INSERT ... ON CONFLICT (station_id, ts) DO NOTHING
);
SELECT create_hypertable('raw', by_range('ts', INTERVAL '7 days'), if_not_exists => TRUE);

-- ---------------------------------------------------------------------
-- 3. CLEANED – raw + chỉ số tính sẵn (thủ tục ELT ghi vào)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cleaned (
    ts           TIMESTAMP NOT NULL,
    station_id   TEXT NOT NULL REFERENCES dim_substation (station_id),
    hufl         DOUBLE PRECISION,
    hull         DOUBLE PRECISION,
    mufl         DOUBLE PRECISION,
    mull         DOUBLE PRECISION,
    lufl         DOUBLE PRECISION,
    lull         DOUBLE PRECISION,
    ot           DOUBLE PRECISION,
    s_h          DOUBLE PRECISION NOT NULL,                  -- √(HUFL² + HULL²)
    load_pct     DOUBLE PRECISION NOT NULL,                  -- s_h / rated_load × 100
    quality_flag TEXT NOT NULL DEFAULT 'OK' CHECK (quality_flag IN ('OK', 'STUCK', 'MISSING')),
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (station_id, ts)
);
SELECT create_hypertable('cleaned', by_range('ts', INTERVAL '7 days'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS ix_cleaned_station_ts ON cleaned (station_id, ts DESC);

-- Mốc đã xử lý, tính RIÊNG từng trạm (producer ghi xen kẽ 4 trạm cùng ts;
-- một mốc chung có thể bỏ sót trạm chưa kịp ghi)
CREATE TABLE IF NOT EXISTS etl_watermark (
    job_name   TEXT NOT NULL,                                  -- vd 'raw_to_cleaned'
    station_id TEXT NOT NULL REFERENCES dim_substation (station_id),
    last_ts    TIMESTAMP NOT NULL DEFAULT '1900-01-01',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (job_name, station_id)
);

-- ---------------------------------------------------------------------
-- 4. AGG_HOURLY – tổng hợp theo giờ, kho tự cập nhật (continuous aggregate)
--    materialized_only = false: giờ đang chạy vẫn hiện trên dashboard
-- ---------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS agg_hourly
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 hour', ts) AS bucket,
       station_id,
       avg(load_pct) AS avg_load_pct,
       max(load_pct) AS max_load_pct,
       avg(ot)       AS avg_ot,
       max(ot)       AS max_ot,
       count(*)      AS n_records                          -- < 4 nghĩa là thiếu bản ghi
FROM cleaned
GROUP BY bucket, station_id
WITH NO DATA;

SELECT add_continuous_aggregate_policy('agg_hourly',
       start_offset      => NULL,                         -- dữ liệu là 2017–2018 nên làm mới toàn bộ
       end_offset        => INTERVAL '1 hour',
       schedule_interval => INTERVAL '1 minute',
       if_not_exists     => TRUE);

-- ---------------------------------------------------------------------
-- 5. FACT_ALERT – cảnh báo (detector ghi, FastAPI cập nhật ACK)
--    Vòng đời: OPEN → ACK → RESOLVED. RESOLVED mà acked_at rỗng = bị bỏ lỡ
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_alert (
    alert_id          BIGSERIAL PRIMARY KEY,
    run_id            TEXT NOT NULL DEFAULT 'LIVE',        -- LIVE / EVAL_A / EVAL_B / DRILL
    station_id        TEXT NOT NULL REFERENCES dim_substation (station_id),
    alert_type        TEXT NOT NULL CHECK (alert_type IN ('OVERLOAD', 'SENSOR')),
    severity          TEXT NOT NULL CHECK (severity IN ('RED', 'YELLOW', 'SENSOR')),
    trigger_rule      TEXT CHECK (trigger_rule IN ('THRESHOLD', 'TTB', 'STUCK', 'MISSING')),
    status            TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'ACK', 'RESOLVED')),
    -- thời gian dữ liệu
    opened_ts         TIMESTAMP NOT NULL,
    escalated_ts      TIMESTAMP,                           -- lúc lên Đỏ (= opened_ts nếu mở thẳng Đỏ) → lead time
    last_ts           TIMESTAMP NOT NULL,                  -- bản ghi gần nhất đã cập nhật cảnh báo
    resolved_ts       TIMESTAMP,
    -- giờ thật
    opened_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    acked_at          TIMESTAMPTZ,
    acked_by          TEXT,
    resolved_at       TIMESTAMPTZ,
    -- mức độ
    load_pct          DOUBLE PRECISION,
    peak_load_pct     DOUBLE PRECISION,
    ttb_min           DOUBLE PRECISION,                    -- NULL khi tải không tăng
    speed_pct_h       DOUBLE PRECISION,                    -- load_pct − load_pct 1 giờ trước
    below_close_count SMALLINT NOT NULL DEFAULT 0,         -- số lần liên tiếp < close_pct (hysteresis)
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_type_severity CHECK ((alert_type = 'SENSOR') = (severity = 'SENSOR')),
    CONSTRAINT ck_ack           CHECK (status <> 'ACK' OR (acked_at IS NOT NULL AND acked_by IS NOT NULL)),
    CONSTRAINT ck_resolved      CHECK ((status = 'RESOLVED') = (resolved_ts IS NOT NULL)),
    CONSTRAINT ck_escalated     CHECK (escalated_ts IS NULL OR severity = 'RED'),
    CONSTRAINT ck_time_order    CHECK (last_ts >= opened_ts AND (resolved_ts IS NULL OR resolved_ts >= opened_ts))
);
-- Chống trùng: mỗi (run, trạm, loại) chỉ 1 cảnh báo đang mở
CREATE UNIQUE INDEX IF NOT EXISTS ux_alert_one_open
    ON fact_alert (run_id, station_id, alert_type) WHERE status IN ('OPEN', 'ACK');
CREATE INDEX IF NOT EXISTS ix_alert_run_station_ts ON fact_alert (run_id, station_id, opened_ts);

-- ---------------------------------------------------------------------
-- 6. EVAL.EVENT_LABEL – đáp án, cất ở schema riêng; detector không đọc
--    Khớp từng cột với event_label.csv (Task 1.3)
-- ---------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS eval;
CREATE TABLE IF NOT EXISTS eval.event_label (
    event_id    TEXT PRIMARY KEY,
    station_id  TEXT NOT NULL REFERENCES dim_substation (station_id),
    type        TEXT NOT NULL CHECK (type IN ('OVERLOAD', 'SENSOR')),
    t_start     TIMESTAMP NOT NULL,
    t_breach    TIMESTAMP,                                 -- chỉ OVERLOAD
    t_end       TIMESTAMP NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('REAL', 'SIMULATED')),
    detail      TEXT,                                      -- INJECTED_RAMP / REAL_PEAK / DAY31_FILL / STUCK_HV
    CHECK (t_start <= t_end),
    CHECK ((type = 'OVERLOAD') = (t_breach IS NOT NULL)),
    CHECK (t_breach IS NULL OR t_breach BETWEEN t_start AND t_end)
);

-- ---------------------------------------------------------------------
-- 7. VIEW cho dashboard
-- ---------------------------------------------------------------------
-- Màn hình 1: số liệu mới nhất của mỗi trạm
CREATE OR REPLACE VIEW v_station_latest AS
SELECT DISTINCT ON (c.station_id)
       c.station_id, s.station_name, c.ts, c.s_h, c.load_pct, c.ot, c.quality_flag,
       p.rated_load,
       CASE WHEN c.load_pct >= p.red_pct    THEN 'RED'
            WHEN c.load_pct >= p.yellow_pct THEN 'YELLOW'
            ELSE 'NORMAL' END AS load_level
FROM cleaned c
JOIN dim_substation  s USING (station_id)
JOIN param_threshold p USING (station_id)
ORDER BY c.station_id, c.ts DESC;

-- Màn hình 2: hàng đợi cảnh báo, xếp Đỏ → Vàng → SENSOR, rồi TTB ngắn, rồi load_pct cao
CREATE OR REPLACE VIEW v_alert_queue AS
SELECT a.*,
       s.station_name,
       CASE a.severity WHEN 'RED' THEN 1 WHEN 'YELLOW' THEN 2 ELSE 3 END       AS severity_rank,
       (a.status = 'RESOLVED' AND a.acked_at IS NULL)                        AS is_missed,
       EXTRACT(EPOCH FROM (a.acked_at - a.opened_at))                        AS response_sec
FROM fact_alert a
JOIN dim_substation s USING (station_id)
ORDER BY (a.status = 'RESOLVED'),                                            -- đang mở lên trước
         severity_rank, a.ttb_min ASC NULLS LAST, a.load_pct DESC NULLS LAST;

-- ---------------------------------------------------------------------
-- 8. DỮ LIỆU DIMENSION (SIMULATED – nhóm tự đặt)
-- ---------------------------------------------------------------------
INSERT INTO dim_substation (station_id, station_name, source, shift_days, data_origin, importance, neighbor_station_id, note) VALUES
    ('TRAM_Y', 'Trạm Y', 'ETTm1',  0, 'REAL',    3, 'TRAM_3', 'Sát giới hạn (k = 1,05); nhân vật chính Red Alert'),
    ('TRAM_2', 'Trạm 2', 'ETTm2',  0, 'REAL',    2, 'TRAM_4', NULL),
    ('TRAM_3', 'Trạm 3', 'ETTm1',  7, 'DERIVED', 2, 'TRAM_Y', 'Dư công suất (k = 1,43); nhận chuyển tải từ Trạm Y'),
    ('TRAM_4', 'Trạm 4', 'ETTm2', 14, 'DERIVED', 1, 'TRAM_2', NULL)
ON CONFLICT (station_id) DO NOTHING;

INSERT INTO dim_customer_group (group_id, station_id, group_name, customer_type, load_share_pct, is_priority) VALUES
    ('Y_HOS', 'TRAM_Y', 'Bệnh viện khu vực',       'HOSPITAL',    10, TRUE),
    ('Y_IND', 'TRAM_Y', 'Khu công nghiệp',         'INDUSTRIAL',  45, FALSE),
    ('Y_RES', 'TRAM_Y', 'Dân cư',                  'RESIDENTIAL', 45, FALSE),
    ('2_PUB', 'TRAM_2', 'Chiếu sáng, cấp nước',    'PUBLIC',      15, TRUE),
    ('2_COM', 'TRAM_2', 'Thương mại',              'COMMERCIAL',  35, FALSE),
    ('2_RES', 'TRAM_2', 'Dân cư',                  'RESIDENTIAL', 50, FALSE),
    ('3_COM', 'TRAM_3', 'Thương mại',              'COMMERCIAL',  40, FALSE),
    ('3_RES', 'TRAM_3', 'Dân cư',                  'RESIDENTIAL', 60, FALSE),
    ('4_IND', 'TRAM_4', 'Xưởng sản xuất',          'INDUSTRIAL',  30, FALSE),
    ('4_RES', 'TRAM_4', 'Dân cư',                  'RESIDENTIAL', 70, FALSE)
ON CONFLICT (group_id) DO NOTHING;

INSERT INTO etl_watermark (job_name, station_id)
SELECT 'raw_to_cleaned', station_id FROM dim_substation
ON CONFLICT DO NOTHING;