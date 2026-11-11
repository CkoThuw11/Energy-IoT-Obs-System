-- =====================================================================
-- gold_views.sql – Lớp GOLD (VIEW) cho Power BI, v2 (sped.md §5.2) – KHÔNG còn A/B testing.
--
-- Cài trên CẢ HAI database (cùng định nghĩa):
--   energy_history : 8 tháng + detector chạy một lượt (run HISTORY) → tải lưới, sự cố, cảm biến
--   energy         : các lần phát lại có người trực (run LIVE)      → vận hành, chuyển tải
--   docker exec -i energy-timescaledb psql -U energy -d <db> -v ON_ERROR_STOP=1 -q < powerbi/gold_views.sql
-- Power BI Import + Refresh tay. Cột TRUE/FALSE trả về 1/0 (SUM = số dòng đạt).
-- Ghép cảnh báo ↔ sự cố: cùng trạm, cùng loại, cảnh báo ĐANG MỞ tại một thời điểm trong [t_start, t_end + 1 giờ].
-- =====================================================================
DROP SCHEMA IF EXISTS gold CASCADE;
CREATE SCHEMA gold;

-- ---------------------------------------------------------------------
-- DIMENSION
-- ---------------------------------------------------------------------
CREATE VIEW gold.dim_station AS
SELECT s.station_id, s.station_name, s.source, s.data_origin, s.importance,
       s.neighbor_station_id, n.station_name AS neighbor_station_name,
       p.rated_load, p.k, p.p99_sh, p.yellow_pct, p.red_pct, p.close_pct,
       EXISTS (SELECT 1 FROM dim_customer_group g
                WHERE g.station_id = s.station_id AND g.is_priority)::int AS has_priority_customer
FROM dim_substation s
JOIN param_threshold p USING (station_id)
LEFT JOIN dim_substation n ON n.station_id = s.neighbor_station_id;

CREATE VIEW gold.dim_customer_group AS
SELECT group_id, station_id, group_name, customer_type, load_share_pct, is_priority::int AS is_priority
FROM dim_customer_group;

-- Kỳ kiểm thử 8 tháng
CREATE VIEW gold.dim_date AS
SELECT d::date                            AS date,
       EXTRACT(YEAR  FROM d)::int         AS year,
       EXTRACT(MONTH FROM d)::int         AS month_no,
       to_char(d, 'YYYY-MM')              AS year_month,
       EXTRACT(DAY FROM d)::int           AS day_of_month,
       EXTRACT(ISODOW FROM d)::int        AS weekday_no,                  -- 1 = Thứ Hai
       (ARRAY['T2','T3','T4','T5','T6','T7','CN'])[EXTRACT(ISODOW FROM d)::int] AS weekday_name,
       (EXTRACT(ISODOW FROM d) >= 6)::int AS is_weekend,
       CASE WHEN EXTRACT(MONTH FROM d) IN (11, 12, 1, 2) THEN 'Đông' ELSE 'Xuân–Hè' END AS season
FROM generate_series(DATE '2017-11-01', DATE '2018-06-26', INTERVAL '1 day') d;

-- ---------------------------------------------------------------------
-- 1. LƯỚI ĐIỆN CHỊU TẢI THẾ NÀO – 1 dòng / giờ / trạm
-- ---------------------------------------------------------------------
CREATE VIEW gold.fact_load_hourly AS
SELECT h.bucket AS hour_ts, h.bucket::date AS date, h.station_id,
       EXTRACT(HOUR FROM h.bucket)::int   AS hour_of_day,
       EXTRACT(ISODOW FROM h.bucket)::int AS weekday_no,
       to_char(h.bucket, 'YYYY-MM')       AS year_month,
       round(h.avg_load_pct::numeric, 2) AS avg_load_pct,
       round(h.max_load_pct::numeric, 2) AS max_load_pct,
       round(h.avg_ot::numeric, 2)       AS avg_ot,
       round(h.max_ot::numeric, 2)       AS max_ot,
       h.n_records,
       CASE WHEN h.max_load_pct >= p.red_pct    THEN '4. ≥ 100%'
            WHEN h.max_load_pct >= p.yellow_pct THEN '3. 90–100%'
            WHEN h.max_load_pct >= p.close_pct  THEN '2. 85–90%'
            ELSE '1. < 85%' END                AS load_band,
       (h.max_load_pct >= p.yellow_pct)::int   AS is_ge90_hour,
       (h.max_load_pct >= p.red_pct)::int      AS is_overload_hour
FROM agg_hourly h
JOIN param_threshold p USING (station_id);

-- ---------------------------------------------------------------------
-- 2. SỰ CỐ – đợt quá tải thật (đáp án) với thời lượng, đỉnh và số phút ≥ 100%
-- ---------------------------------------------------------------------
CREATE VIEW gold.fact_overload_episode AS
SELECT e.event_id, e.station_id, e.detail, e.t_start, e.t_breach, e.t_end, e.t_start::date AS date,
       to_char(e.t_start, 'YYYY-MM') AS year_month,
       round(EXTRACT(EPOCH FROM (e.t_end - e.t_start)) / 60)  AS duration_min,
       round(max(c.load_pct)::numeric, 2)                     AS peak_load_pct,
       count(*) FILTER (WHERE c.load_pct >= p.red_pct) * 15   AS minutes_ge100,
       count(*) FILTER (WHERE c.load_pct >= p.yellow_pct) * 15 AS minutes_ge90
FROM eval.event_label e
JOIN param_threshold p USING (station_id)
LEFT JOIN cleaned c ON c.station_id = e.station_id AND c.ts BETWEEN e.t_start AND e.t_end
WHERE e.type = 'OVERLOAD'
GROUP BY e.event_id, e.station_id, e.detail, e.t_start, e.t_breach, e.t_end;

-- ---------------------------------------------------------------------
-- 3. CẢNH BÁO – 1 dòng / cảnh báo (mọi run), kèm kết quả xử lý theo GIỜ DỮ LIỆU
-- ---------------------------------------------------------------------
CREATE VIEW gold.fact_alert AS
SELECT q.alert_id, q.run_id, q.station_id, q.alert_type, q.severity, q.trigger_rule, q.status,
       q.severity_rank,
       q.opened_ts, q.escalated_ts, q.resolved_ts, q.acked_ts, q.action_ts,
       q.opened_ts::date AS date, to_char(q.opened_ts, 'YYYY-MM') AS year_month,
       EXTRACT(HOUR FROM q.opened_ts)::int AS open_hour,
       round(EXTRACT(EPOCH FROM (q.resolved_ts - q.opened_ts)) / 60) AS duration_min,
       round(q.peak_load_pct::numeric, 2) AS peak_load_pct,
       round(q.received_load::numeric, 2) AS received_load,
       (q.received_load > 0)::int         AS opened_while_receiving,
       q.acked_by, q.verdict,
       q.opened_at AT TIME ZONE 'Asia/Ho_Chi_Minh' AS opened_at_local,
       (q.opened_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS real_date,
       -- xử lý theo giờ dữ liệu
       round(q.data_wait_min::numeric, 1)   AS handle_min,            -- Đỏ: đến chuyển tải; Vàng: đến theo dõi
       q.deadline_min,
       (q.handled_ts IS NOT NULL)::int      AS is_handled,
       (q.handled_ts IS NOT NULL AND q.data_wait_min <= q.deadline_min)::int AS is_on_time,
       COALESCE(q.is_late, false)::int      AS is_late,
       q.is_missed::int                     AS is_missed,
       (q.verdict = 'FALSE_ALARM')::int     AS is_false_alarm_verdict,
       -- đối chiếu đáp án
       (SELECT string_agg(e.event_id, ',' ORDER BY e.event_id) FROM eval.event_label e
         WHERE e.station_id = q.station_id AND e.type = q.alert_type
           AND q.opened_ts <= e.t_end + INTERVAL '1 hour'
           AND (q.resolved_ts IS NULL OR q.resolved_ts >= e.t_start))      AS matched_events,
       (NOT EXISTS (SELECT 1 FROM eval.event_label e
         WHERE e.station_id = q.station_id AND e.type = q.alert_type
           AND q.opened_ts <= e.t_end + INTERVAL '1 hour'
           AND (q.resolved_ts IS NULL OR q.resolved_ts >= e.t_start)))::int AS is_unmatched
FROM v_alert_queue q;

-- ---------------------------------------------------------------------
-- 4. CHUYỂN TẢI (LIVE) – 1 dòng / lần chuyển
--    receiver_pushed: trạm nhận mở cảnh báo Vàng/Đỏ trong lúc đang nhận tải của lần chuyển này
-- ---------------------------------------------------------------------
CREATE VIEW gold.fact_transfer AS
SELECT t.action_id, t.alert_id, t.run_id, t.source_station, t.target_station,
       round(t.amount::numeric, 2) AS amount,
       t.effective_from_ts, t.effective_to_ts, t.effective_from_ts::date AS date,
       t.return_reason, round(t.active_min::numeric) AS active_min,
       (t.return_reason = 'SAFE')::int    AS is_safe_return,
       (t.return_reason = 'TIMEOUT')::int AS is_timeout_return,
       t.is_overdue::int                  AS is_overdue,
       (t.effective_to_ts IS NULL)::int   AS is_active,
       t.done_by,
       (SELECT max(CASE r.severity WHEN 'RED' THEN 2 WHEN 'YELLOW' THEN 1 ELSE 0 END)
          FROM fact_alert r
         WHERE r.run_id = t.run_id AND r.station_id = t.target_station AND r.alert_type = 'OVERLOAD'
           AND r.opened_ts > t.effective_from_ts
           AND (t.effective_to_ts IS NULL OR r.opened_ts <= t.effective_to_ts)) AS receiver_level,
       COALESCE((SELECT bool_or(true) FROM fact_alert r
                  WHERE r.run_id = t.run_id AND r.station_id = t.target_station AND r.alert_type = 'OVERLOAD'
                    AND r.opened_ts > t.effective_from_ts
                    AND (t.effective_to_ts IS NULL OR r.opened_ts <= t.effective_to_ts)), false)::int AS receiver_pushed
FROM v_transfer_status t;

-- ---------------------------------------------------------------------
-- 5. THIẾT BỊ ĐO – lỗi cảm biến trong đáp án + cảnh báo SENSOR của detector
-- ---------------------------------------------------------------------
CREATE VIEW gold.fact_sensor_fault AS
SELECT e.event_id, e.station_id, e.detail, e.t_start, e.t_end, e.t_start::date AS date,
       to_char(e.t_start, 'YYYY-MM') AS year_month,
       EXTRACT(DAY FROM e.t_start)::int  AS start_day_of_month,
       EXTRACT(HOUR FROM e.t_start)::int AS start_hour,
       round(EXTRACT(EPOCH FROM (e.t_end - e.t_start)) / 3600, 2) AS duration_hours,
       d.first_open_ts,
       (d.first_open_ts IS NOT NULL)::int AS is_detected,
       round(EXTRACT(EPOCH FROM (d.first_open_ts - e.t_start)) / 60) AS detect_delay_min,
       d.run_id
FROM eval.event_label e
LEFT JOIN LATERAL (
    SELECT min(a.opened_ts) AS first_open_ts, min(a.run_id) AS run_id
    FROM fact_alert a
    WHERE a.station_id = e.station_id AND a.alert_type = 'SENSOR'
      AND a.opened_ts <= e.t_end + INTERVAL '1 hour'
      AND (a.resolved_ts IS NULL OR a.resolved_ts >= e.t_start)
) d ON TRUE
WHERE e.type = 'SENSOR';
