CREATE TABLE IF NOT EXISTS staging.sensor_reading (
    ts               TIMESTAMPTZ NOT NULL,
    substation_id    SMALLINT    NOT NULL REFERENCES mart.dim_substation(substation_id),
    hufl DOUBLE PRECISION, hull DOUBLE PRECISION,
    mufl DOUBLE PRECISION, mull DOUBLE PRECISION,
    lufl DOUBLE PRECISION, lull DOUBLE PRECISION,
    ot   DOUBLE PRECISION,
    total_load       DOUBLE PRECISION,           -- = HUFL
    load_pct         DOUBLE PRECISION,           -- = total_load / rated_load
    balance_gap      DOUBLE PRECISION,           -- = HUFL - (MUFL + LUFL)
    dq_flag          SMALLINT    NOT NULL DEFAULT 0,
        -- bitmask: 0 = OK, 1 = có NULL, 2 = giá trị âm, 4 = lệch cân bằng vượt ngưỡng
    source_type      TEXT NOT NULL CHECK (source_type IN ('REAL','DERIVED','SIMULATED')),
    scenario_id      TEXT,
    mapping_version  TEXT,
    ingested_at      TIMESTAMPTZ NOT NULL,       -- thời điểm ghi vào Raw (đo độ trễ)
    processed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (substation_id, ts)
);

SELECT create_hypertable('staging.sensor_reading', 'ts',
       chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);

-- Watermark cho ELT tăng dần (theo thời điểm ghi vào Raw)
CREATE TABLE IF NOT EXISTS staging.etl_watermark (
    job_name          TEXT PRIMARY KEY,
    last_ingested_at  TIMESTAMPTZ NOT NULL DEFAULT '-infinity',
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO staging.etl_watermark (job_name) VALUES ('raw_to_staging')
ON CONFLICT DO NOTHING;

-- Raw cần index theo ingested_at để ELT đọc phần dữ liệu mới nhanh
CREATE INDEX IF NOT EXISTS ix_raw_ingested_at ON raw.sensor_reading (ingested_at);