-- ===== FACT: tổng hợp theo giờ =====
CREATE MATERIALIZED VIEW IF NOT EXISTS mart.fact_reading_hourly
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket('1 hour', ts) AS bucket,
       substation_id,
       avg(total_load)  AS avg_load,
       max(total_load)  AS max_load,
       avg(load_pct)    AS avg_load_pct,
       max(load_pct)    AS max_load_pct,
       avg(ot)          AS avg_ot,
       max(ot)          AS max_ot,
       count(*)         AS n_readings,
       sum(CASE WHEN dq_flag <> 0 THEN 1 ELSE 0 END) AS n_dq_issues
FROM staging.sensor_reading
GROUP BY bucket, substation_id
WITH NO DATA;

SELECT add_continuous_aggregate_policy('mart.fact_reading_hourly',
       start_offset => NULL, end_offset => INTERVAL '1 hour',
       schedule_interval => INTERVAL '1 minute');

-- ===== FACT: tổng hợp theo ngày =====
CREATE MATERIALIZED VIEW IF NOT EXISTS mart.fact_reading_daily
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket('1 day', ts) AS bucket,
       substation_id,
       avg(total_load)  AS avg_load,
       max(total_load)  AS max_load,
       avg(load_pct)    AS avg_load_pct,
       max(load_pct)    AS max_load_pct,
       avg(ot)          AS avg_ot,
       max(ot)          AS max_ot,
       count(*)         AS n_readings,
       sum(CASE WHEN dq_flag <> 0 THEN 1 ELSE 0 END) AS n_dq_issues
FROM staging.sensor_reading
GROUP BY bucket, substation_id
WITH NO DATA;

SELECT add_continuous_aggregate_policy('mart.fact_reading_daily',
       start_offset => NULL, end_offset => INTERVAL '1 day',
       schedule_interval => INTERVAL '5 minutes');

-- ===== FACT: cảnh báo (accumulating snapshot, 1 dòng = 1 cảnh báo) =====
CREATE TABLE IF NOT EXISTS mart.fact_alert (
    alert_id            BIGSERIAL PRIMARY KEY,
    substation_id       SMALLINT NOT NULL REFERENCES mart.dim_substation(substation_id),
    alert_type          TEXT NOT NULL CHECK (alert_type IN ('OVERLOAD','SPIKE','SENSOR_FAULT')),
    detection_layer     TEXT NOT NULL CHECK (detection_layer IN ('THRESHOLD','ZSCORE','TREND','DQ')),
    severity            TEXT NOT NULL CHECK (severity IN ('YELLOW','RED')),
    status              TEXT NOT NULL DEFAULT 'OPEN'
                        CHECK (status IN ('OPEN','ACKNOWLEDGED','RESOLVED')),
    -- Điểm ưu tiên (0–1) và 4 thành phần
    priority_score      NUMERIC(4,3) CHECK (priority_score BETWEEN 0 AND 1),
    score_load          NUMERIC(4,3) CHECK (score_load BETWEEN 0 AND 1),
    score_criticality   NUMERIC(4,3) CHECK (score_criticality BETWEEN 0 AND 1),
    score_rate          NUMERIC(4,3) CHECK (score_rate BETWEEN 0 AND 1),
    score_urgency       NUMERIC(4,3) CHECK (score_urgency BETWEEN 0 AND 1),
    -- Tình trạng hiện tại
    current_load_pct    DOUBLE PRECISION,
    peak_load_pct       DOUBLE PRECISION,
    current_ot          DOUBLE PRECISION,
    time_to_breach_min  DOUBLE PRECISION,
    -- Thời gian sự kiện (dùng để đánh giá với eval_event_label)
    first_ts            TIMESTAMPTZ NOT NULL,
    last_ts             TIMESTAMPTZ NOT NULL,
    -- Thời gian xử lý (dùng cho KPI vận hành)
    opened_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    escalated_at        TIMESTAMPTZ,
    acked_at            TIMESTAMPTZ,
    acked_by            TEXT,
    resolved_at         TIMESTAMPTZ,
    below_off_count     SMALLINT NOT NULL DEFAULT 0      -- đủ 2 thì RESOLVED
);

-- Chống trùng: mỗi (trạm, loại) chỉ có 1 cảnh báo chưa đóng
CREATE UNIQUE INDEX IF NOT EXISTS ux_alert_active
    ON mart.fact_alert (substation_id, alert_type) WHERE status <> 'RESOLVED';
CREATE INDEX IF NOT EXISTS ix_alert_rank
    ON mart.fact_alert (status, severity, priority_score DESC);

-- ===== VIEW: bản ghi mới nhất mỗi trạm (cho Grafana) =====
CREATE OR REPLACE VIEW mart.v_latest_reading AS
SELECT d.substation_id, d.display_name, d.criticality, d.rated_load,
       r.ts, r.total_load, r.load_pct, r.mufl, r.lufl, r.ot, r.dq_flag
FROM mart.dim_substation d
CROSS JOIN LATERAL (
    SELECT * FROM staging.sensor_reading s
    WHERE s.substation_id = d.substation_id
    ORDER BY s.ts DESC LIMIT 1
) r;