-- ===== DIMENSION =====
CREATE TABLE IF NOT EXISTS mart.dim_substation (
    substation_id          SMALLINT PRIMARY KEY,
    substation_code        TEXT NOT NULL UNIQUE,                 -- S1..S4
    display_name           TEXT NOT NULL,                        -- 'Trạm Y', 'Trạm 2'...
    source_series          TEXT NOT NULL CHECK (source_series IN ('ETTm1','ETTm2')),
    reading_source_type    TEXT NOT NULL CHECK (reading_source_type IN ('REAL','DERIVED')),
    rating_factor          NUMERIC(4,2) NOT NULL CHECK (rating_factor > 0),
    rated_load             DOUBLE PRECISION,                     -- P99(HUFL, 16 tháng) × rating_factor, Thiện điền
    criticality            SMALLINT NOT NULL CHECK (criticality BETWEEN 1 AND 5),
    neighbor_substation_id SMALLINT REFERENCES mart.dim_substation(substation_id)
                           DEFERRABLE INITIALLY DEFERRED,        -- cho phép nạp CSV không cần đúng thứ tự
    attr_source            TEXT NOT NULL DEFAULT 'SIMULATED'
);

CREATE TABLE IF NOT EXISTS mart.dim_customer_group (
    customer_group_id  SMALLINT PRIMARY KEY,
    substation_id      SMALLINT NOT NULL REFERENCES mart.dim_substation(substation_id),
    voltage_side       CHAR(1)  NOT NULL CHECK (voltage_side IN ('M','L')),
    group_name         TEXT     NOT NULL,
    load_share         NUMERIC(4,3) NOT NULL CHECK (load_share > 0 AND load_share <= 1),
    shed_priority      SMALLINT NOT NULL,                        -- 1 = sa thải trước
    is_critical        BOOLEAN  NOT NULL DEFAULT FALSE,          -- TRUE = không được sa thải
    attr_source        TEXT     NOT NULL DEFAULT 'SIMULATED'
);

-- ===== THAM CHIẾU =====
CREATE TABLE IF NOT EXISTS mart.scenario_mapping (
    substation_id    SMALLINT NOT NULL REFERENCES mart.dim_substation(substation_id),
    mapping_version  TEXT     NOT NULL,
    source_series    TEXT     NOT NULL,
    shift_days       SMALLINT NOT NULL DEFAULT 0,
    noise_pct        NUMERIC(4,2) NOT NULL DEFAULT 0,
    random_seed      INT,
    PRIMARY KEY (substation_id, mapping_version)
);

CREATE TABLE IF NOT EXISTS mart.param_threshold (
    substation_id  SMALLINT NOT NULL REFERENCES mart.dim_substation(substation_id),
    metric         TEXT     NOT NULL CHECK (metric IN ('load_pct','balance_gap')),
    severity       TEXT     NOT NULL CHECK (severity IN ('YELLOW','RED')),
    on_value       DOUBLE PRECISION NOT NULL,
    off_value      DOUBLE PRECISION NOT NULL,
    calib_from     TIMESTAMPTZ NOT NULL,
    calib_to       TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (substation_id, metric, severity),
    CHECK (off_value < on_value)
);

CREATE TABLE IF NOT EXISTS mart.param_profile (
    substation_id  SMALLINT NOT NULL REFERENCES mart.dim_substation(substation_id),
    metric         TEXT     NOT NULL DEFAULT 'load_pct',
    day_of_week    SMALLINT NOT NULL CHECK (day_of_week BETWEEN 1 AND 7),   -- ISODOW: 1 = Thứ 2
    hour_of_day    SMALLINT NOT NULL CHECK (hour_of_day BETWEEN 0 AND 23),
    mean_value     DOUBLE PRECISION NOT NULL,
    std_value      DOUBLE PRECISION NOT NULL CHECK (std_value >= 0),
    n_samples      INT NOT NULL,
    PRIMARY KEY (substation_id, metric, day_of_week, hour_of_day)
);