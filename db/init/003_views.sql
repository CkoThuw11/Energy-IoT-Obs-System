-- =====================================================================
-- 003_views.sql – VIEW cho dashboard (v2, sped.md §4–5)
-- Chạy sau 001_schema.sql. Chạy lại nhiều lần không lỗi (DROP ... CASCADE rồi tạo lại).
-- Nâng cấp DB cũ: chạy sql/migrate_v2.sql rồi file này.
-- =====================================================================
DROP VIEW IF EXISTS v_transfer_status, v_alert_queue, v_station_latest, v_load_sim, v_data_clock CASCADE;

-- Đồng hồ dữ liệu: mốc dữ liệu mới nhất của từng trạm (mọi thời hạn tính theo đồng hồ này)
CREATE VIEW v_data_clock AS
SELECT station_id, max(ts) AS now_ts FROM cleaned GROUP BY station_id;

-- Tải sau xử lý (MÔ PHỎNG): s_h đo − chuyển đi + nhận về, theo từng run_id.
-- cleaned giữ nguyên số đo thật; detector xét quá tải trên load_pct_sim.
CREATE VIEW v_load_sim AS
SELECT r.run_id, c.ts, c.station_id, c.s_h, c.load_pct, c.ot,
       COALESCE(t.out_amt, 0)                                   AS transfer_out,
       COALESCE(t.in_amt, 0)                                    AS transfer_in,
       c.s_h - COALESCE(t.out_amt, 0) + COALESCE(t.in_amt, 0)   AS s_h_sim,
       (c.s_h - COALESCE(t.out_amt, 0) + COALESCE(t.in_amt, 0)) / p.rated_load * 100 AS load_pct_sim
FROM cleaned c
CROSS JOIN (VALUES ('LIVE'), ('HISTORY')) AS r (run_id)
JOIN param_threshold p USING (station_id)
LEFT JOIN LATERAL (
    SELECT sum(x.amount) FILTER (WHERE a.station_id = c.station_id)     AS out_amt,
           sum(x.amount) FILTER (WHERE x.target_station = c.station_id) AS in_amt
    FROM fact_alert_action x
    JOIN fact_alert a USING (alert_id)
    WHERE a.run_id = r.run_id AND x.action_type = 'TRANSFER'
      AND (a.station_id = c.station_id OR x.target_station = c.station_id)
      AND c.ts > x.effective_from_ts
      AND (x.effective_to_ts IS NULL OR c.ts <= x.effective_to_ts)
) t ON TRUE;

-- Số liệu mới nhất của mỗi trạm, theo từng run_id (dashboard lọc WHERE run_id = 'LIVE').
-- s_h / load_pct = số ĐO; s_h_sim / load_pct_sim = sau chuyển tải (mô phỏng).
-- load_level = mức theo CẢNH BÁO ĐANG MỞ của run đó: RED > YELLOW > SENSOR > NORMAL.
-- speed_pct_h = load_pct − load_pct 1 giờ trước, trên TẢI ĐO.
CREATE VIEW v_station_latest AS
SELECT v.run_id, v.station_id, s.station_name, v.ts, v.s_h, v.load_pct, v.ot,
       v.s_h_sim, v.load_pct_sim, v.transfer_out, v.transfer_in,
       p.rated_load,
       v.load_pct - h.load_pct AS speed_pct_h,
       CASE COALESCE(min(CASE a.severity WHEN 'RED' THEN 1 WHEN 'YELLOW' THEN 2 WHEN 'SENSOR' THEN 3 END), 4)
            WHEN 1 THEN 'RED' WHEN 2 THEN 'YELLOW' WHEN 3 THEN 'SENSOR' ELSE 'NORMAL' END AS load_level,
       min(a.ttb_min) FILTER (WHERE a.alert_type = 'OVERLOAD') AS ttb_min
FROM v_data_clock k
JOIN v_load_sim v ON v.station_id = k.station_id AND v.ts = k.now_ts
JOIN dim_substation  s ON s.station_id = k.station_id
JOIN param_threshold p ON p.station_id = k.station_id
LEFT JOIN v_load_sim h ON h.run_id = v.run_id AND h.station_id = v.station_id AND h.ts = v.ts - INTERVAL '1 hour'
LEFT JOIN fact_alert a ON a.run_id = v.run_id AND a.station_id = v.station_id
                      AND a.status IN ('OPEN', 'ACK', 'ACTION_TAKEN')
GROUP BY v.run_id, v.station_id, s.station_name, v.ts, v.s_h, v.load_pct, v.ot,
         v.s_h_sim, v.load_pct_sim, v.transfer_out, v.transfer_in, p.rated_load, h.load_pct;

-- Hàng đợi cảnh báo + thời hạn theo GIỜ DỮ LIỆU.
--   clock_start_ts : Đỏ = lúc lên Đỏ; Vàng = lúc mở
--   handled_ts     : Đỏ = mốc chuyển tải (action_ts); Vàng = mốc "Theo dõi" (acked_ts)
--   deadline_min   : Đỏ 30, Vàng 60, Cảm biến không có thời hạn
--   data_wait_min  : (handled_ts hoặc đồng hồ dữ liệu) − clock_start_ts
--   is_overdue     : đang mở, chưa xử lý, data_wait_min > deadline_min
--   is_missed      : Đỏ đã đóng, không chuyển tải, không phải báo giả
--   is_late        : Đỏ có chuyển tải nhưng sau 30 phút dữ liệu
-- Xếp: đang mở trước → mức (Đỏ, Vàng, Cảm biến) → TTB theo khung 10 phút → trạm có khách ưu tiên → tải cao.
CREATE VIEW v_alert_queue AS
WITH q AS (
    SELECT a.*,
           s.station_name,
           k.now_ts AS data_now_ts,
           CASE a.severity WHEN 'RED' THEN 1 WHEN 'YELLOW' THEN 2 ELSE 3 END       AS severity_rank,
           EXISTS (SELECT 1 FROM dim_customer_group g
                    WHERE g.station_id = a.station_id AND g.is_priority)          AS has_priority_customer,
           CASE a.severity WHEN 'RED' THEN a.escalated_ts WHEN 'YELLOW' THEN a.opened_ts END AS clock_start_ts,
           CASE a.severity WHEN 'RED' THEN a.action_ts    WHEN 'YELLOW' THEN a.acked_ts  END AS handled_ts,
           CASE a.severity WHEN 'RED' THEN 30             WHEN 'YELLOW' THEN 60          END AS deadline_min
    FROM fact_alert a
    JOIN dim_substation s USING (station_id)
    LEFT JOIN v_data_clock k USING (station_id)
)
SELECT q.*,
       EXTRACT(EPOCH FROM (COALESCE(q.handled_ts, LEAST(COALESCE(q.resolved_ts, q.data_now_ts), q.data_now_ts))
                           - q.clock_start_ts)) / 60                                  AS data_wait_min,
       (q.status <> 'RESOLVED' AND q.handled_ts IS NULL AND q.deadline_min IS NOT NULL
        AND q.data_now_ts - q.clock_start_ts > make_interval(mins => q.deadline_min)) AS is_overdue,
       (q.severity = 'RED' AND q.status = 'RESOLVED' AND q.action_ts IS NULL
        AND q.verdict IS DISTINCT FROM 'FALSE_ALARM')                                AS is_missed,
       (q.severity = 'RED' AND q.action_ts - q.escalated_ts > INTERVAL '30 minutes') AS is_late,
       EXTRACT(EPOCH FROM (q.acked_at - q.opened_at))                                AS response_sec
FROM q
ORDER BY (q.status = 'RESOLVED'),                                              -- đang mở lên trước
         q.severity_rank,
         floor(coalesce(q.ttb_min, 9999) / 10),                                -- TTB chênh ≤ 10 phút coi như bằng
         q.has_priority_customer DESC,
         q.load_pct DESC NULLS LAST;

-- Trạng thái từng lần chuyển tải (theo giờ dữ liệu của trạm nguồn).
--   is_overdue: còn hiệu lực và đã chuyển ≥ 2 giờ (detector chưa trả vì tải đo vẫn ≥ 90%)
CREATE VIEW v_transfer_status AS
SELECT x.*, a.run_id, a.station_id AS source_station, k.now_ts AS data_now_ts,
       EXTRACT(EPOCH FROM (COALESCE(x.effective_to_ts, k.now_ts) - x.effective_from_ts)) / 60 AS active_min,
       (x.effective_to_ts IS NULL AND k.now_ts - x.effective_from_ts >= INTERVAL '2 hours')   AS is_overdue
FROM fact_alert_action x
JOIN fact_alert a USING (alert_id)
LEFT JOIN v_data_clock k ON k.station_id = a.station_id;
