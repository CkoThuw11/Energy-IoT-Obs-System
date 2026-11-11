-- =====================================================================
-- check_pipeline.sql - Kiểm tra nhanh pipeline cho MỘT ngày dữ liệu (v2)
--
--   docker compose exec -T timescaledb psql -U energy -d energy -v day=2017-11-01 < sql/check_pipeline.sql
--
-- Phát đủ 1 ngày thì mỗi trạm phải có: số MỐC trong raw = 96, cleaned = 96, agg_hourly = 24.
-- raw là append-only: phát lại cùng ngày làm raw_rows tăng (mỗi lần +96) nhưng cleaned vẫn 96.
-- (cleaned cần chờ job 10 giây chạy xong sau khi producer kết thúc)
-- =====================================================================
\set ON_ERROR_STOP on

WITH c AS (
    SELECT s.station_id,
           (SELECT count(*) FROM raw r WHERE r.station_id = s.station_id
               AND r.ts >= :'day'::timestamp AND r.ts < :'day'::timestamp + INTERVAL '1 day')            AS raw_rows,
           (SELECT count(DISTINCT r.ts) FROM raw r WHERE r.station_id = s.station_id
               AND r.ts >= :'day'::timestamp AND r.ts < :'day'::timestamp + INTERVAL '1 day')            AS raw_ts,
           (SELECT count(*) FROM cleaned c WHERE c.station_id = s.station_id
               AND c.ts >= :'day'::timestamp AND c.ts < :'day'::timestamp + INTERVAL '1 day')            AS cleaned_rows,
           (SELECT count(*) FROM agg_hourly a WHERE a.station_id = s.station_id
               AND a.bucket >= :'day'::timestamp AND a.bucket < :'day'::timestamp + INTERVAL '1 day')    AS agg_hourly_rows
    FROM dim_substation s
)
SELECT *, CASE WHEN raw_ts = 96 AND cleaned_rows = 96 AND agg_hourly_rows = 24 THEN 'PASS' ELSE 'FAIL' END AS ket_qua
FROM c ORDER BY station_id;

-- Số dòng toàn bảng: phát lại thì raw_total tăng, cleaned_total không đổi
SELECT (SELECT count(*) FROM raw)     AS raw_total,
       (SELECT count(*) FROM cleaned) AS cleaned_total;

-- Mốc ingested_at đã xử lý của từng trạm
SELECT station_id, last_ingested_at, updated_at FROM etl_watermark WHERE job_name = 'raw_to_cleaned' ORDER BY station_id;
