-- =====================================================================
-- schema.sql – Kho dữ liệu BI cảnh báo quá tải trạm biến áp (TimescaleDB)
-- Phiên bản: v2 (sped.md) – raw append-only, không dừng phát lại, thời hạn theo giờ dữ liệu
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
-- 2. RAW – số liệu nguyên trạng do producer ghi vào (APPEND-ONLY, sped.md §3)
--    Không khóa chính: phát lại cùng mốc → thêm dòng mới, giữ ingested_at để biết bản nào mới nhất.
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
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
SELECT create_hypertable('raw', by_range('ts', INTERVAL '7 days'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS ix_raw_station_ingested ON raw (station_id, ingested_at);

-- ---------------------------------------------------------------------
-- 3. CLEANED – 1 dòng / (trạm, ts): bản raw có ingested_at MỚI NHẤT + chỉ số tính sẵn
--    (thủ tục ELT ghi, ON CONFLICT DO UPDATE khi bản mới hơn – sql/elt.sql)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cleaned (
    ts           TIMESTAMP NOT NULL,
    station_id   TEXT NOT NULL REFERENCES dim_substation (station_id),
    hufl         DOUBLE PRECISION,
    hull         DOUBLE PRECISION,
    ot           DOUBLE PRECISION,
    s_h          DOUBLE PRECISION NOT NULL,                  -- √(HUFL² + HULL²)
    load_pct     DOUBLE PRECISION NOT NULL,                  -- s_h / rated_load × 100
    ingested_at  TIMESTAMPTZ NOT NULL,                       -- ingested_at của bản raw được chọn
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (station_id, ts)
);
SELECT create_hypertable('cleaned', by_range('ts', INTERVAL '7 days'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS ix_cleaned_station_ts ON cleaned (station_id, ts DESC);

-- Mốc ingested_at đã xử lý, tính RIÊNG từng trạm. ELT quét lại từ (mốc − 1 phút) để không sót
-- giao dịch của producer commit muộn hơn lần quét trước (ghi đè an toàn nhờ DO UPDATE).
CREATE TABLE IF NOT EXISTS etl_watermark (
    job_name         TEXT NOT NULL,                            -- vd 'raw_to_cleaned'
    station_id       TEXT NOT NULL REFERENCES dim_substation (station_id),
    last_ingested_at TIMESTAMPTZ NOT NULL DEFAULT '1900-01-01',
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
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
-- 5. FACT_ALERT – cảnh báo (detector ghi; Streamlit ghi thao tác người trực) – sped.md §4
--    Vòng đời: OPEN → ACK (Vàng: "Theo dõi") → ACTION_TAKEN (Đỏ: chuyển tải) → RESOLVED.
--    Đỏ không có bước nhận riêng: bấm "Xác nhận chuyển tải" ghi verdict = REAL, acked_* (nếu chưa có),
--    action_*; bấm "Báo giả" ghi verdict = FALSE_ALARM và đóng ngay.
--    Thời hạn theo GIỜ DỮ LIỆU: Đỏ = action_ts − escalated_ts ≤ 30′; Vàng = acked_ts − opened_ts ≤ 60′.
--    Đỏ bị bỏ lỡ = RESOLVED, không chuyển tải (action_ts rỗng), không phải báo giả.
--    Detector không ghi đè cột của người trực.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_alert (
    alert_id          BIGSERIAL PRIMARY KEY,
    run_id            TEXT NOT NULL DEFAULT 'LIVE' CHECK (run_id IN ('LIVE', 'HISTORY')),
    station_id        TEXT NOT NULL REFERENCES dim_substation (station_id),
    alert_type        TEXT NOT NULL CHECK (alert_type IN ('OVERLOAD', 'SENSOR')),
    severity          TEXT NOT NULL CHECK (severity IN ('RED', 'YELLOW', 'SENSOR')),
    trigger_rule      TEXT CHECK (trigger_rule IN ('THRESHOLD', 'TTB', 'STUCK', 'MISSING')),
    status            TEXT NOT NULL DEFAULT 'OPEN'
                      CHECK (status IN ('OPEN', 'ACK', 'ACTION_TAKEN', 'RESOLVED')),
    -- thời gian dữ liệu
    opened_ts         TIMESTAMP NOT NULL,
    escalated_ts      TIMESTAMP,                           -- lúc lên Đỏ (= opened_ts nếu mở thẳng Đỏ)
    last_ts           TIMESTAMP NOT NULL,                  -- bản ghi gần nhất đã cập nhật cảnh báo
    resolved_ts       TIMESTAMP,
    acked_ts          TIMESTAMP,                           -- mốc dữ liệu lúc bấm "Theo dõi" / xử lý
    action_ts         TIMESTAMP,                           -- mốc dữ liệu lúc chuyển tải lần đầu
    -- giờ thật
    opened_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    acked_at          TIMESTAMPTZ,
    acked_by          TEXT,
    verified_at       TIMESTAMPTZ,                         -- lúc ghi verdict (chuyển tải / báo giả)
    verdict           TEXT CHECK (verdict IN ('REAL', 'FALSE_ALARM')),
    action_done_at    TIMESTAMPTZ,                         -- lúc bấm "Xác nhận chuyển tải"
    root_cause        TEXT,                                -- SENSOR: 'SENSOR_MAINTENANCE'
    resolved_at       TIMESTAMPTZ,
    -- mức độ
    load_pct          DOUBLE PRECISION,
    peak_load_pct     DOUBLE PRECISION,
    ttb_min           DOUBLE PRECISION,                    -- NULL khi tải không tăng hoặc load_pct ≥ 100
    speed_pct_h       DOUBLE PRECISION,                    -- load_pct − load_pct 1 giờ trước
    below_close_count SMALLINT NOT NULL DEFAULT 0,         -- số lần liên tiếp < close_pct (hysteresis)
    received_load     DOUBLE PRECISION,                    -- lượng tải đang NHẬN về khi cảnh báo mở (trạm nhận)
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_type_severity CHECK ((alert_type = 'SENSOR') = (severity = 'SENSOR')),
    CONSTRAINT ck_ack           CHECK (status <> 'ACK' OR (acked_at IS NOT NULL AND acked_by IS NOT NULL)),
    CONSTRAINT ck_action        CHECK (status <> 'ACTION_TAKEN' OR (action_done_at IS NOT NULL AND action_ts IS NOT NULL)),
    CONSTRAINT ck_verdict       CHECK (verdict IS NULL OR verified_at IS NOT NULL),
    CONSTRAINT ck_resolved      CHECK ((status = 'RESOLVED') = (resolved_ts IS NOT NULL)),
    CONSTRAINT ck_escalated     CHECK (escalated_ts IS NULL OR severity = 'RED'),
    CONSTRAINT ck_time_order    CHECK (last_ts >= opened_ts AND (resolved_ts IS NULL OR resolved_ts >= opened_ts))
);
-- Chống trùng: mỗi (run, trạm, loại) chỉ 1 cảnh báo đang mở
CREATE UNIQUE INDEX IF NOT EXISTS ux_alert_one_open
    ON fact_alert (run_id, station_id, alert_type) WHERE status IN ('OPEN', 'ACK', 'ACTION_TAKEN');
CREATE INDEX IF NOT EXISTS ix_alert_run_station_ts ON fact_alert (run_id, station_id, opened_ts);

-- Biện pháp giảm tải – hiện chỉ dùng TRANSFER (chuyển tải sang trạm lân cận).
-- Dữ liệu là lịch sử nên lưới không đổi thật: tác dụng được MÔ PHỎNG trong v_load_sim
-- (trạm nguồn − amount, trạm nhận + amount) cho các mốc ts ∈ (effective_from_ts, effective_to_ts].
-- Trả tải (detector, theo TẢI ĐO của trạm nguồn):
--   SAFE    : < close_pct (85%) hai mốc liên tiếp sau khi chuyển
--   TIMEOUT : đã chuyển ≥ 2 giờ dữ liệu và < yellow_pct (90%)
--   Quá 2 giờ mà vẫn ≥ 90% → còn hiệu lực, dashboard hiện "Chuyển tải quá hạn".
CREATE TABLE IF NOT EXISTS fact_alert_action (
    action_id         BIGSERIAL PRIMARY KEY,
    alert_id          BIGINT NOT NULL REFERENCES fact_alert (alert_id),
    action_type       TEXT NOT NULL CHECK (action_type IN ('TRANSFER', 'DEMAND_RESPONSE', 'BACKUP', 'LOAD_SHED')),
    amount            DOUBLE PRECISION NOT NULL CHECK (amount > 0),       -- lượng chuyển, cùng đơn vị S_H
    target_station    TEXT REFERENCES dim_substation (station_id),        -- chỉ dùng cho TRANSFER
    effective_from_ts TIMESTAMP,                                          -- mốc dữ liệu lúc xác nhận
    effective_to_ts   TIMESTAMP,                                          -- mốc dữ liệu lúc trả tải (NULL = còn hiệu lực)
    return_reason     TEXT CHECK (return_reason IN ('SAFE', 'TIMEOUT')),
    done_by           TEXT NOT NULL,
    done_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_target_transfer CHECK ((action_type = 'TRANSFER') = (target_station IS NOT NULL)),
    CONSTRAINT ck_transfer_from   CHECK (action_type <> 'TRANSFER' OR effective_from_ts IS NOT NULL),
    CONSTRAINT ck_transfer_window CHECK (effective_to_ts IS NULL OR effective_to_ts > effective_from_ts),
    CONSTRAINT ck_return_reason   CHECK ((effective_to_ts IS NULL) = (return_reason IS NULL))
);
CREATE INDEX IF NOT EXISTS ix_alert_action_alert ON fact_alert_action (alert_id);

-- Trạng thái detector theo run (lịch sử 4 bản ghi, cảnh báo đang theo dõi); detector ghi
-- cùng transaction với fact_alert để khởi động lại không mất / không trùng
CREATE TABLE IF NOT EXISTS detector_checkpoint (
    run_id TEXT PRIMARY KEY,
    method TEXT NOT NULL,
    state  JSONB NOT NULL
);

-- ---------------------------------------------------------------------
-- 6. EVAL.EVENT_LABEL – đáp án (29 sự cố thật), cất ở schema riêng; detector không đọc
--    Khớp từng cột với event_label.csv
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
    detail      TEXT,                                      -- REAL_PEAK / DAY31_FILL / STUCK_HV
    CHECK (t_start <= t_end),
    CHECK ((type = 'OVERLOAD') = (t_breach IS NOT NULL)),
    CHECK (t_breach IS NULL OR t_breach BETWEEN t_start AND t_end)
);

-- (Các VIEW nằm trong 003_views.sql)

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