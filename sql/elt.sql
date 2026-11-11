-- =====================================================================
-- elt.sql - Thủ tục raw -> cleaned (v2, sped.md §3), chạy tự động mỗi 10 giây (add_job)
-- Chạy lại nhiều lần không lỗi, không tạo job trùng.
--
-- Nạp:
--   docker compose exec -T timescaledb psql -U energy -d energy -v ON_ERROR_STOP=1 < sql/elt.sql
--
-- Phụ thuộc: db/init/001_schema.sql (raw, cleaned, etl_watermark, param_threshold, agg_hourly)
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Thủ tục raw -> cleaned
--    * raw là append-only: một (trạm, ts) có thể có nhiều bản → lấy bản ingested_at MỚI NHẤT.
--    * Chỉ đọc dòng raw có ingested_at > (watermark của trạm − 1 phút). Lùi 1 phút để không sót
--      giao dịch của producer bắt đầu trước nhưng commit sau lần quét trước.
--    * S_H = sqrt(HUFL^2 + HULL^2); load_pct = S_H / rated_load * 100.
--    * ON CONFLICT DO UPDATE chỉ khi bản mới hơn → quét lặp không đổi dữ liệu, bản sửa muộn ghi đè bản cũ.
--    * Chữ ký (job_id, config) là bắt buộc để add_job gọi được.
-- ---------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE sp_raw_to_cleaned(job_id INT DEFAULT NULL, config JSONB DEFAULT NULL)
LANGUAGE plpgsql
AS $$
BEGIN
    WITH src AS (
        SELECT DISTINCT ON (r.station_id, r.ts)
               r.ts, r.station_id, r.hufl, r.hull, r.ot, r.ingested_at,
               sqrt(r.hufl ^ 2 + r.hull ^ 2)                          AS s_h,
               sqrt(r.hufl ^ 2 + r.hull ^ 2) / p.rated_load * 100     AS load_pct
        FROM raw r
        JOIN etl_watermark   w ON w.job_name = 'raw_to_cleaned' AND w.station_id = r.station_id
        JOIN param_threshold p ON p.station_id = r.station_id
        WHERE r.ingested_at > w.last_ingested_at - INTERVAL '1 minute'
        ORDER BY r.station_id, r.ts, r.ingested_at DESC
    ),
    ins AS (
        INSERT INTO cleaned (ts, station_id, hufl, hull, ot, s_h, load_pct, ingested_at)
        SELECT ts, station_id, hufl, hull, ot, s_h, load_pct, ingested_at
        FROM src
        ON CONFLICT (station_id, ts) DO UPDATE
           SET hufl = EXCLUDED.hufl, hull = EXCLUDED.hull, ot = EXCLUDED.ot,
               s_h = EXCLUDED.s_h, load_pct = EXCLUDED.load_pct,
               ingested_at = EXCLUDED.ingested_at, processed_at = now()
         WHERE EXCLUDED.ingested_at > cleaned.ingested_at
    )
    UPDATE etl_watermark w
       SET last_ingested_at = m.max_ingested, updated_at = now()
      FROM (SELECT station_id, max(ingested_at) AS max_ingested FROM src GROUP BY station_id) m
     WHERE w.job_name = 'raw_to_cleaned'
       AND w.station_id = m.station_id
       AND m.max_ingested > w.last_ingested_at;
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
