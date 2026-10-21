-- Nhãn sự cố (đáp án), do task của Văn sinh ra
CREATE TABLE IF NOT EXISTS mart.eval_event_label (
    event_id       TEXT PRIMARY KEY,                 -- EV001...
    substation_id  SMALLINT NOT NULL REFERENCES mart.dim_substation(substation_id),
    event_type     TEXT NOT NULL CHECK (event_type IN ('OVERLOAD','SPIKE','SENSOR_FAULT')),
    t_start        TIMESTAMPTZ NOT NULL,
    t_breach       TIMESTAMPTZ,                      -- NULL nếu sự cố không vượt ngưỡng
    t_end          TIMESTAMPTZ NOT NULL,
    magnitude      DOUBLE PRECISION,                 -- vd: load_pct đỉnh
    scenario_id    TEXT,
    description    TEXT,
    CHECK (t_end > t_start),
    CHECK (t_breach IS NULL OR (t_breach BETWEEN t_start AND t_end))
);

-- Kết quả thực nghiệm 3 phương pháp: cùng cấu trúc fact_alert, thêm method và run_id
CREATE TABLE IF NOT EXISTS mart.eval_alert
    (LIKE mart.fact_alert INCLUDING DEFAULTS INCLUDING CONSTRAINTS);

ALTER TABLE mart.eval_alert
    ADD COLUMN method TEXT NOT NULL
        CHECK (method IN ('THRESHOLD_ONLY','THRESHOLD_ZSCORE','COMBINED_RANKED')),
    ADD COLUMN run_id TEXT NOT NULL,
    ADD PRIMARY KEY (alert_id),
    ADD FOREIGN KEY (substation_id) REFERENCES mart.dim_substation(substation_id);

CREATE UNIQUE INDEX IF NOT EXISTS ux_eval_alert_active
    ON mart.eval_alert (run_id, method, substation_id, alert_type) WHERE status <> 'RESOLVED';