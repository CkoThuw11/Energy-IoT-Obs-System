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
-- 5. FACT_ALERT – cảnh báo (detector ghi; Streamlit ghi các bước của người trực)
--    Vòng đời: OPEN → ACK → ACTION_TAKEN → RESOLVED, khớp quy trình 3 bước
--    ① Nhận (ACK + verdict REAL, hoặc Báo giả) → ② Chuyển tải (ACTION_TAKEN) → ③ Theo dõi.
--    Detector mở / cập nhật / đóng theo dữ liệu; người trực chỉ ghi acked_*,
--    verified_at, verdict, action_done_at, root_cause (detector không ghi đè).
--    Báo giả: verdict = 'FALSE_ALARM', status = 'RESOLVED', resolved_ts = last_ts.
--    RED + RESOLVED mà acked_at rỗng = bị bỏ lỡ
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_alert (
    alert_id          BIGSERIAL PRIMARY KEY,
    run_id            TEXT NOT NULL DEFAULT 'LIVE',        -- LIVE (vận hành / phát lại) / EVAL_A / EVAL_B
    station_id        TEXT NOT NULL REFERENCES dim_substation (station_id),
    alert_type        TEXT NOT NULL CHECK (alert_type IN ('OVERLOAD', 'SENSOR')),
    severity          TEXT NOT NULL CHECK (severity IN ('RED', 'YELLOW', 'SENSOR')),
    trigger_rule      TEXT CHECK (trigger_rule IN ('THRESHOLD', 'TTB', 'STUCK', 'MISSING')),
    status            TEXT NOT NULL DEFAULT 'OPEN'
                      CHECK (status IN ('OPEN', 'ACK', 'ACTION_TAKEN', 'RESOLVED')),
    -- thời gian dữ liệu
    opened_ts         TIMESTAMP NOT NULL,
    escalated_ts      TIMESTAMP,                           -- lúc lên Đỏ (= opened_ts nếu mở thẳng Đỏ) → lead time
    last_ts           TIMESTAMP NOT NULL,                  -- bản ghi gần nhất đã cập nhật cảnh báo
    resolved_ts       TIMESTAMP,
    -- giờ thật
    opened_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    acked_at          TIMESTAMPTZ,
    acked_by          TEXT,
    verified_at       TIMESTAMPTZ,                         -- ① Nhận (xác nhận quá tải thật) hoặc Báo giả
    verdict           TEXT CHECK (verdict IN ('REAL', 'FALSE_ALARM')),
    action_done_at    TIMESTAMPTZ,                         -- ② Chuyển tải: bấm "Xác nhận chuyển tải"
    root_cause        TEXT,                                -- ③ Theo dõi (SENSOR: 'SENSOR_MAINTENANCE')
    resolved_at       TIMESTAMPTZ,
    -- mức độ
    load_pct          DOUBLE PRECISION,
    peak_load_pct     DOUBLE PRECISION,
    ttb_min           DOUBLE PRECISION,                    -- NULL khi tải không tăng hoặc load_pct ≥ 100
    speed_pct_h       DOUBLE PRECISION,                    -- load_pct − load_pct 1 giờ trước
    below_close_count SMALLINT NOT NULL DEFAULT 0,         -- số lần liên tiếp < close_pct (hysteresis)
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_type_severity CHECK ((alert_type = 'SENSOR') = (severity = 'SENSOR')),
    CONSTRAINT ck_ack           CHECK (status <> 'ACK' OR (acked_at IS NOT NULL AND acked_by IS NOT NULL)),
    CONSTRAINT ck_action        CHECK (status <> 'ACTION_TAKEN' OR action_done_at IS NOT NULL),
    CONSTRAINT ck_verdict       CHECK (verdict IS NULL OR verified_at IS NOT NULL),
    CONSTRAINT ck_resolved      CHECK ((status = 'RESOLVED') = (resolved_ts IS NOT NULL)),
    CONSTRAINT ck_escalated     CHECK (escalated_ts IS NULL OR severity = 'RED'),
    CONSTRAINT ck_time_order    CHECK (last_ts >= opened_ts AND (resolved_ts IS NULL OR resolved_ts >= opened_ts))
);
-- Chống trùng: mỗi (run, trạm, loại) chỉ 1 cảnh báo đang mở
CREATE UNIQUE INDEX IF NOT EXISTS ux_alert_one_open
    ON fact_alert (run_id, station_id, alert_type) WHERE status IN ('OPEN', 'ACK', 'ACTION_TAKEN');
CREATE INDEX IF NOT EXISTS ix_alert_run_station_ts ON fact_alert (run_id, station_id, opened_ts);

-- Biện pháp giảm tải ở bước ② – hiện chỉ dùng TRANSFER (chuyển tải sang trạm lân cận).
-- Dữ liệu là lịch sử nên lưới không đổi thật: tác dụng được MÔ PHỎNG trong v_load_sim
-- (trạm nguồn − amount, trạm nhận + amount) cho các mốc ts ∈ (effective_from_ts, effective_to_ts].
-- effective_to_ts do detector đặt khi TẢI ĐO của trạm nguồn < close_pct hai lần liên tiếp (trả tải).
CREATE TABLE IF NOT EXISTS fact_alert_action (
    action_id         BIGSERIAL PRIMARY KEY,
    alert_id          BIGINT NOT NULL REFERENCES fact_alert (alert_id),
    action_type       TEXT NOT NULL CHECK (action_type IN ('TRANSFER', 'DEMAND_RESPONSE', 'BACKUP', 'LOAD_SHED')),
    amount            DOUBLE PRECISION NOT NULL CHECK (amount > 0),       -- lượng chuyển, cùng đơn vị S_H
    target_station    TEXT REFERENCES dim_substation (station_id),        -- chỉ dùng cho TRANSFER
    effective_from_ts TIMESTAMP,                                          -- mốc dữ liệu lúc xác nhận
    effective_to_ts   TIMESTAMP,                                          -- mốc dữ liệu lúc trả tải (NULL = còn hiệu lực)
    done_by           TEXT NOT NULL,
    done_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_target_transfer CHECK ((action_type = 'TRANSFER') = (target_station IS NOT NULL)),
    CONSTRAINT ck_transfer_from   CHECK (action_type <> 'TRANSFER' OR effective_from_ts IS NOT NULL),
    CONSTRAINT ck_transfer_window CHECK (effective_to_ts IS NULL OR effective_to_ts > effective_from_ts)
);
CREATE INDEX IF NOT EXISTS ix_alert_action_alert ON fact_alert_action (alert_id);

-- Điều khiển phát lại: detector bật paused khi một cảnh báo chuyển sang Đỏ (chỉ LIVE);
-- producer đọc paused trước mỗi mốc ts; Streamlit tắt paused khi mọi Đỏ đã xử lý (không hiện trên UI)
CREATE TABLE IF NOT EXISTS replay_control (
    run_id       TEXT PRIMARY KEY,
    paused       BOOLEAN NOT NULL DEFAULT FALSE,
    paused_at_ts TIMESTAMP,                                -- mốc dữ liệu lúc dừng
    reason       TEXT,                                     -- alert_id gây dừng
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Trạng thái detector theo run (lịch sử 4 bản ghi, cảnh báo đang theo dõi); detector ghi
-- cùng transaction với fact_alert để khởi động lại không mất / không trùng
CREATE TABLE IF NOT EXISTS detector_checkpoint (
    run_id TEXT PRIMARY KEY,
    method TEXT NOT NULL,
    state  JSONB NOT NULL
);

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
-- Tải sau xử lý (MÔ PHỎNG): s_h đo − chuyển đi + nhận về, theo từng run_id.
-- cleaned giữ nguyên số đo thật; detector LIVE xét quá tải trên load_pct_sim.
CREATE OR REPLACE VIEW v_load_sim AS
SELECT r.run_id, c.ts, c.station_id, c.s_h, c.load_pct, c.ot, c.quality_flag,
       COALESCE(t.out_amt, 0)                                   AS transfer_out,
       COALESCE(t.in_amt, 0)                                    AS transfer_in,
       c.s_h - COALESCE(t.out_amt, 0) + COALESCE(t.in_amt, 0)   AS s_h_sim,
       (c.s_h - COALESCE(t.out_amt, 0) + COALESCE(t.in_amt, 0)) / p.rated_load * 100 AS load_pct_sim
FROM cleaned c
CROSS JOIN (VALUES ('LIVE'), ('EVAL_A'), ('EVAL_B')) AS r (run_id)
JOIN param_threshold p USING (station_id)
LEFT JOIN LATERAL (
    SELECT sum(x.amount) FILTER (WHERE a.station_id = c.station_id)     AS out_amt,
           sum(x.amount) FILTER (WHERE x.target_station = c.station_id) AS in_amt
    FROM fact_alert_action x
    JOIN fact_alert a USING (alert_id)
    WHERE a.run_id = r.run_id AND x.action_type = 'TRANSFER'
      AND (a.station_id = c.station_id OR x.target_station = c.station_id)
      AND c.ts > x.effective_from_ts
      AND (x.effective_to_ts IS NULL OR c.ts <= x.effective_to_ts)
) t ON TRUE;

-- Số liệu mới nhất của mỗi trạm, theo từng run_id (dashboard lọc WHERE run_id = 'LIVE').
-- s_h / load_pct = số ĐO; s_h_sim / load_pct_sim = sau chuyển tải (mô phỏng, = số đo nếu không chuyển).
-- load_level = mức của trạm theo CẢNH BÁO ĐANG MỞ của run đó: RED > YELLOW > SENSOR > NORMAL.
-- speed_pct_h = load_pct − load_pct 1 giờ (4 bản ghi) trước, trên TẢI ĐO (như detector: chuyển tải
-- là khoảng bù cố định, không phải phụ tải tăng).
CREATE OR REPLACE VIEW v_station_latest AS
WITH latest AS (
    SELECT station_id, max(ts) AS ts FROM cleaned GROUP BY station_id
)
SELECT v.run_id, v.station_id, s.station_name, v.ts, v.s_h, v.load_pct, v.ot, v.quality_flag,
       v.s_h_sim, v.load_pct_sim, v.transfer_out, v.transfer_in,
       p.rated_load,
       v.load_pct - h.load_pct AS speed_pct_h,
       CASE COALESCE(min(CASE a.severity WHEN 'RED' THEN 1 WHEN 'YELLOW' THEN 2 WHEN 'SENSOR' THEN 3 END), 4)
            WHEN 1 THEN 'RED' WHEN 2 THEN 'YELLOW' WHEN 3 THEN 'SENSOR' ELSE 'NORMAL' END AS load_level,
       min(a.ttb_min) FILTER (WHERE a.alert_type = 'OVERLOAD') AS ttb_min
FROM latest l
JOIN v_load_sim v ON v.station_id = l.station_id AND v.ts = l.ts
JOIN dim_substation  s ON s.station_id = l.station_id
JOIN param_threshold p ON p.station_id = l.station_id
LEFT JOIN v_load_sim h ON h.run_id = v.run_id AND h.station_id = v.station_id AND h.ts = v.ts - INTERVAL '1 hour'
LEFT JOIN fact_alert a ON a.run_id = v.run_id AND a.station_id = v.station_id
                      AND a.status IN ('OPEN', 'ACK', 'ACTION_TAKEN')
GROUP BY v.run_id, v.station_id, s.station_name, v.ts, v.s_h, v.load_pct, v.ot, v.quality_flag,
         v.s_h_sim, v.load_pct_sim, v.transfer_out, v.transfer_in, p.rated_load, h.load_pct;

-- Hàng đợi cảnh báo. Xếp: đang mở trước → mức (Đỏ, Vàng, SENSOR) → TTB theo khung 10 phút
-- → trạm có khách hàng ưu tiên → load_pct cao.
-- is_escalated: Đỏ còn OPEN quá 3 phút giờ thật, Vàng còn OPEN quá 15 phút; SENSOR không leo thang.
-- is_missed: Đỏ đã đóng mà chưa ai nhận.
CREATE OR REPLACE VIEW v_alert_queue AS
SELECT a.*,
       s.station_name,
       CASE a.severity WHEN 'RED' THEN 1 WHEN 'YELLOW' THEN 2 ELSE 3 END       AS severity_rank,
       EXISTS (SELECT 1 FROM dim_customer_group g
                WHERE g.station_id = a.station_id AND g.is_priority)          AS has_priority_customer,
       (a.status = 'OPEN' AND (
            (a.severity = 'RED'    AND now() - a.opened_at > INTERVAL '3 minutes') OR
            (a.severity = 'YELLOW' AND now() - a.opened_at > INTERVAL '15 minutes'))) AS is_escalated,
       (a.severity = 'RED' AND a.status = 'RESOLVED' AND a.acked_at IS NULL)   AS is_missed,
       EXTRACT(EPOCH FROM (a.acked_at - a.opened_at))                        AS response_sec
FROM fact_alert a
JOIN dim_substation s USING (station_id)
ORDER BY (a.status = 'RESOLVED'),                                            -- đang mở lên trước
         severity_rank,
         floor(coalesce(a.ttb_min, 9999) / 10),                              -- TTB chênh ≤ 10 phút coi như bằng
         has_priority_customer DESC,
         a.load_pct DESC NULLS LAST;

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