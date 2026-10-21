CREATE TABLE IF NOT EXISTS raw.sensor_reading (
    ts               TIMESTAMPTZ      NOT NULL,
    substation_id    SMALLINT         NOT NULL,
    hufl             DOUBLE PRECISION,
    hull             DOUBLE PRECISION,
    mufl             DOUBLE PRECISION,
    mull             DOUBLE PRECISION,
    lufl             DOUBLE PRECISION,
    lull             DOUBLE PRECISION,
    ot               DOUBLE PRECISION,
    source_type      TEXT             NOT NULL,   -- REAL / DERIVED / SIMULATED
    scenario_id      TEXT,
    mapping_version  TEXT,
    ingested_at      TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY (substation_id, ts)               -- idempotency
);

SELECT create_hypertable('raw.sensor_reading', 'ts',
       chunk_time_interval => INTERVAL '7 days',
       if_not_exists => TRUE);