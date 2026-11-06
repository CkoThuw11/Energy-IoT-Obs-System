-- =====================================================================
-- reset_replay.sql – Xóa dữ liệu phát lại và cảnh báo để phát lại một sự cố trên DB sạch.
-- GIỮ: dim_*, param_threshold, eval.event_label, thủ tục ELT và job.
-- Dừng detector TRƯỚC khi chạy (detector giữ trạng thái trong bộ nhớ), chạy lại sau.
--
--   docker compose exec -T timescaledb psql -U energy -d energy -v ON_ERROR_STOP=1 < sql/reset_replay.sql
-- =====================================================================
BEGIN;
DELETE FROM fact_alert_action;
DELETE FROM fact_alert;
DELETE FROM replay_control;
DELETE FROM detector_checkpoint;
DELETE FROM cleaned;
DELETE FROM raw;
UPDATE etl_watermark SET last_ts = '1900-01-01', updated_at = now();
COMMIT;
CALL refresh_continuous_aggregate('agg_hourly', NULL, NULL);
SELECT (SELECT count(*) FROM raw) AS raw, (SELECT count(*) FROM cleaned) AS cleaned,
       (SELECT count(*) FROM fact_alert) AS alerts;
