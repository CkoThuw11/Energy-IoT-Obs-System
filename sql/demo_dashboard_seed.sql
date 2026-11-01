-- =====================================================================
-- demo_dashboard_seed.sql – DỮ LIỆU GIẢ để thử dashboard khi chưa chạy producer/detector
-- Nhánh: feature/dashboard
--   - 1 ngày cleaned (96 bản ghi/trạm) bắt đầu 2017-11-01; Trạm Y tăng tải về cuối ngày
--   - 4 cảnh báo mẫu, ghi run_id = 'LIVE' (dashboard chỉ đọc 'LIVE')
--
-- Chạy:   docker compose exec -T timescaledb psql -U energy -d energy < sql/demo_dashboard_seed.sql
-- Xóa:    docker compose exec -T timescaledb psql -U energy -d energy -c \
--           "DELETE FROM fact_alert WHERE run_id='LIVE'; DELETE FROM cleaned WHERE ts::date='2017-11-01';"
-- KHÔNG chạy trên DB đang dùng thật: file này xóa cleaned ngày 2017-11-01 và fact_alert LIVE.
-- =====================================================================
BEGIN;

DELETE FROM fact_alert WHERE run_id = 'LIVE';
DELETE FROM cleaned WHERE ts >= '2017-11-01' AND ts < '2017-11-02';

-- Tải dạng sin theo giờ trong ngày; Trạm Y dốc lên từ 20:00 để có Vàng → Đỏ;
-- Trạm 4 đứng yên 6 giờ cuối (giả lỗi cảm biến)
INSERT INTO cleaned (ts, station_id, hufl, hull, ot, s_h, load_pct, quality_flag)
SELECT ts, p.station_id, s_h, 0, ot, s_h, s_h / p.rated_load * 100, 'OK'
FROM param_threshold p
CROSS JOIN generate_series(TIMESTAMP '2017-11-01 00:00', TIMESTAMP '2017-11-01 23:45', INTERVAL '15 minutes') AS ts
CROSS JOIN LATERAL (
    SELECT extract(epoch FROM ts - TIMESTAMP '2017-11-01') / 3600.0 AS h
) t
CROSS JOIN LATERAL (
    SELECT p.rated_load * (
             CASE p.station_id
               WHEN 'TRAM_Y' THEN 0.70 + 0.08 * sin(t.h / 24 * 2 * pi()) + greatest(t.h - 20, 0) * 0.075
               WHEN 'TRAM_2' THEN 0.60 + 0.10 * sin(t.h / 24 * 2 * pi())
               WHEN 'TRAM_3' THEN 0.45 + 0.05 * sin(t.h / 24 * 2 * pi())
               ELSE CASE WHEN t.h >= 18 THEN 0.55 ELSE 0.55 + 0.08 * sin(t.h / 24 * 2 * pi()) END
             END) AS s_h,
           35 + 10 * sin(t.h / 24 * 2 * pi()) AS ot
) v;

-- Cảnh báo mẫu: 1 Đỏ đang mở, 1 Vàng đã ACK, 1 SENSOR đang mở, 1 Vàng đã tự đóng không ai nhận
INSERT INTO fact_alert (run_id, station_id, alert_type, severity, trigger_rule, status,
                        opened_ts, escalated_ts, last_ts, resolved_ts,
                        opened_at, acked_at, acked_by,
                        load_pct, peak_load_pct, ttb_min, speed_pct_h)
VALUES
  ('LIVE', 'TRAM_Y', 'OVERLOAD', 'RED',    'TTB',       'OPEN',
   '2017-11-01 22:00', '2017-11-01 23:00', '2017-11-01 23:45', NULL,
   now() - INTERVAL '20 minutes', NULL, NULL, 98.5, 98.5, 5, 30),
  ('LIVE', 'TRAM_2', 'OVERLOAD', 'YELLOW', 'THRESHOLD', 'ACK',
   '2017-11-01 06:00', NULL, '2017-11-01 23:45', NULL,
   now() - INTERVAL '30 minutes', now() - INTERVAL '25 minutes', 'demo', 91.0, 92.0, 45, 12),
  ('LIVE', 'TRAM_4', 'SENSOR',   'SENSOR', 'STUCK',     'OPEN',
   '2017-11-01 19:00', NULL, '2017-11-01 23:45', NULL,
   now() - INTERVAL '10 minutes', NULL, NULL, 55.0, 55.0, NULL, 0),
  ('LIVE', 'TRAM_3', 'OVERLOAD', 'YELLOW', 'THRESHOLD', 'RESOLVED',
   '2017-11-01 12:00', NULL, '2017-11-01 13:00', '2017-11-01 13:00',
   now() - INTERVAL '40 minutes', NULL, NULL, 84.0, 90.5, NULL, -3);

COMMIT;

CALL refresh_continuous_aggregate('agg_hourly', '2017-11-01', '2017-11-02');
