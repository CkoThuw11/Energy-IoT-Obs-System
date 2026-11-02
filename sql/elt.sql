-- =====================================================================
-- elt.sql - Thủ tục raw -> cleaned, chạy tự động mỗi 10 giây (add_job)
-- Nhánh: feature/data-pipeline
-- Chạy lại nhiều lần không lỗi, không tạo job trùng.
--
-- Nạp:
--   docker compose exec -T timescaledb psql -U energy -d energy -v ON_ERROR_STOP=1 < sql/elt.sql
--
-- Phụ thuộc: db/init/001_schema.sql (raw, cleaned, etl_watermark, param_threshold, agg_hourly)
-- agg_hourly (continuous aggregate theo giờ) đã được tạo trong 001_schema.sql
-- và tự làm mới bằng policy; file này không tạo lại.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Thủ tục raw -> cleaned
--    * Chỉ xử lý dòng MỚI: raw.ts > mốc đã xử lý của TRẠM đó (etl_watermark).
--    * S_H = sqrt(HUFL^2 + HULL^2); load_pct = S_H / rated_load * 100.
--    * Mốc tính riêng từng trạm, chỉ tiến lên theo các dòng đã đọc được.
--    * ON CONFLICT DO NOTHING: chạy lặp hoặc chạy chồng không sinh dòng trùng.
--    * Chữ ký (job_id, config) là bắt buộc để add_job gọi được.
-- ---------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE sp_raw_to_cleaned(job_id INT DEFAULT NULL, config JSONB DEFAULT NULL)
LANGUAGE plpgsql
AS $$
BEGIN
    WITH src AS (
        SELECT r.ts, r.station_id,
               r.hufl, r.hull, r.mufl, r.mull, r.lufl, r.lull, r.ot,
               sqrt(r.hufl ^ 2 + r.hull ^ 2)                          AS s_h,
               sqrt(r.hufl ^ 2 + r.hull ^ 2) / p.rated_load * 100     AS load_pct
        FROM raw r
        JOIN etl_watermark   w ON w.job_name = 'raw_to_cleaned' AND w.station_id = r.station_id
        JOIN param_threshold p ON p.station_id = r.station_id
        WHERE r.ts > w.last_ts
    ),
    ins AS (
        INSERT INTO cleaned (ts, station_id, hufl, hull, mufl, mull, lufl, lull, ot, s_h, load_pct)
        SELECT ts, station_id, hufl, hull, mufl, mull, lufl, lull, ot, s_h, load_pct
        FROM src
        ON CONFLICT (station_id, ts) DO NOTHING
    )
    UPDATE etl_watermark w
       SET last_ts = m.max_ts, updated_at = now()
      FROM (SELECT station_id, max(ts) AS max_ts FROM src GROUP BY station_id) m
     WHERE w.job_name = 'raw_to_cleaned'
       AND w.station_id = m.station_id
       AND m.max_ts > w.last_ts;
END;
$$;

-- ---------------------------------------------------------------------
-- 2. Đăng ký chạy tự động mỗi 10 giây (chỉ tạo nếu chưa có)
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM timescaledb_information.jobs WHERE proc_name = 'sp_raw_to_cleaned') THEN
        PERFORM add_job('sp_raw_to_cleaned', INTERVAL '10 seconds', initial_start => now());
    END IF;
END;
$$;

-- Xem job:   SELECT job_id, proc_name, schedule_interval, scheduled FROM timescaledb_information.jobs;
-- Xem lỗi:   SELECT * FROM timescaledb_information.job_errors ORDER BY start_time DESC LIMIT 5;
-- Chạy tay:  CALL sp_raw_to_cleaned();
-- Dừng job:  SELECT alter_job(<job_id>, scheduled => false);
