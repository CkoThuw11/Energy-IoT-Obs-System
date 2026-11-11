-- =====================================================================
-- migrate_v2.sql – Nâng cấp DB schema v1 → v2 tại chỗ (sped.md D3), giữ dimension / tham số / đáp án.
-- Sau file này chạy db/init/003_views.sql, sql/elt.sql, rồi sql/reset_replay.sql (dữ liệu phát lại cũ
-- được tạo bằng kịch bản có bơm lỗi nên phải xóa).
--
--   P="docker compose exec -T timescaledb psql -U energy -d energy -v ON_ERROR_STOP=1 -q"
--   $P < sql/migrate_v2.sql && $P < db/init/003_views.sql && $P < sql/elt.sql && $P < sql/reset_replay.sql
-- Chạy lại nhiều lần không lỗi.
-- =====================================================================
BEGIN;

-- View cũ phụ thuộc cột sắp xóa (kể cả schema gold của Power BI – dựng lại ở T9)
DROP VIEW IF EXISTS v_alert_queue, v_station_latest, v_load_sim CASCADE;
DROP SCHEMA IF EXISTS gold CASCADE;

-- 1. Bỏ điều khiển dừng phát lại
DROP TABLE IF EXISTS replay_control;

-- 2. RAW append-only
ALTER TABLE raw DROP CONSTRAINT IF EXISTS raw_pkey;
CREATE INDEX IF NOT EXISTS ix_raw_station_ingested ON raw (station_id, ingested_at);

-- 3. CLEANED: bỏ quality_flag và 4 cột tải trung/hạ áp; thêm ingested_at
ALTER TABLE cleaned DROP COLUMN IF EXISTS quality_flag,
                    DROP COLUMN IF EXISTS mufl, DROP COLUMN IF EXISTS mull,
                    DROP COLUMN IF EXISTS lufl, DROP COLUMN IF EXISTS lull;
ALTER TABLE cleaned ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ;
UPDATE cleaned SET ingested_at = processed_at WHERE ingested_at IS NULL;
ALTER TABLE cleaned ALTER COLUMN ingested_at SET NOT NULL;

-- 4. Watermark theo ingested_at
ALTER TABLE etl_watermark ADD COLUMN IF NOT EXISTS last_ingested_at TIMESTAMPTZ NOT NULL DEFAULT '1900-01-01';
ALTER TABLE etl_watermark DROP COLUMN IF EXISTS last_ts;

-- 5. FACT_ALERT: chỉ còn LIVE / HISTORY; thêm mốc dữ liệu của thao tác và lượng tải nhận về
DELETE FROM fact_alert_action WHERE alert_id IN (SELECT alert_id FROM fact_alert WHERE run_id NOT IN ('LIVE', 'HISTORY'));
DELETE FROM fact_alert WHERE run_id NOT IN ('LIVE', 'HISTORY');
DELETE FROM detector_checkpoint WHERE run_id NOT IN ('LIVE', 'HISTORY');
ALTER TABLE fact_alert DROP CONSTRAINT IF EXISTS fact_alert_run_id_check;
ALTER TABLE fact_alert ADD CONSTRAINT fact_alert_run_id_check CHECK (run_id IN ('LIVE', 'HISTORY'));
ALTER TABLE fact_alert ADD COLUMN IF NOT EXISTS acked_ts      TIMESTAMP,
                       ADD COLUMN IF NOT EXISTS action_ts     TIMESTAMP,
                       ADD COLUMN IF NOT EXISTS received_load DOUBLE PRECISION;
-- Dữ liệu v1: mốc dữ liệu của thao tác lấy gần đúng bằng last_ts / effective_from_ts
UPDATE fact_alert SET acked_ts = last_ts WHERE acked_at IS NOT NULL AND acked_ts IS NULL;
UPDATE fact_alert a SET action_ts = (SELECT min(x.effective_from_ts) FROM fact_alert_action x WHERE x.alert_id = a.alert_id)
 WHERE action_done_at IS NOT NULL AND action_ts IS NULL;
ALTER TABLE fact_alert DROP CONSTRAINT IF EXISTS ck_action;
ALTER TABLE fact_alert ADD CONSTRAINT ck_action
    CHECK (status <> 'ACTION_TAKEN' OR (action_done_at IS NOT NULL AND action_ts IS NOT NULL));

-- 6. FACT_ALERT_ACTION: lý do trả tải
ALTER TABLE fact_alert_action ADD COLUMN IF NOT EXISTS return_reason TEXT;
ALTER TABLE fact_alert_action DROP CONSTRAINT IF EXISTS fact_alert_action_return_reason_check;
ALTER TABLE fact_alert_action ADD CONSTRAINT fact_alert_action_return_reason_check
    CHECK (return_reason IN ('SAFE', 'TIMEOUT'));
UPDATE fact_alert_action SET return_reason = 'SAFE' WHERE effective_to_ts IS NOT NULL AND return_reason IS NULL;
ALTER TABLE fact_alert_action DROP CONSTRAINT IF EXISTS ck_return_reason;
ALTER TABLE fact_alert_action ADD CONSTRAINT ck_return_reason
    CHECK ((effective_to_ts IS NULL) = (return_reason IS NULL));

-- 7. Đáp án: chỉ còn 29 sự cố thật (bỏ các sự cố bơm)
DELETE FROM eval.event_label WHERE source_type = 'SIMULATED';

COMMIT;
