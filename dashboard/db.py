"""Kết nối DB và mọi thao tác đọc/ghi của dashboard (v2, sped.md §4).

Mọi mốc giờ thật ghi bằng now() phía DB; dashboard không gửi giờ máy.
Mốc DỮ LIỆU của thao tác (acked_ts, action_ts, effective_from_ts) = mốc dữ liệu mới nhất của trạm lúc bấm.
Dashboard chỉ làm việc với run LIVE.
"""

from __future__ import annotations

import os
import threading
from typing import Any

import pandas as pd
import psycopg
import streamlit as st
from psycopg.rows import dict_row

RUN_ID = "LIVE"


class StaleAlert(Exception):
    """Một lệnh bắt buộc không đổi dòng nào (cảnh báo đã bị xử lý / đóng, vượt sức nhận): rollback."""


class Db:
    """Một kết nối autocommit dùng chung, khóa để các phiên không chen lệnh nhau."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.lock = threading.Lock()
        self.conn = self._connect()

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.url, autocommit=True, row_factory=dict_row)

    def _ensure(self) -> None:
        if self.conn.closed or self.conn.broken:
            self.conn = self._connect()

    def rows(self, sql: str, params: Any = None) -> list[dict]:
        with self.lock:
            self._ensure()
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() if cur.description else []

    def run_tx(self, statements: list[tuple[str, Any]], required: tuple[int, ...] = ()) -> list[int]:
        """Chạy nhiều lệnh trong MỘT transaction; trả về rowcount từng lệnh.

        required: chỉ số các lệnh phải đổi ≥ 1 dòng, nếu không thì rollback cả transaction.
        """
        with self.lock:
            self._ensure()
            counts: list[int] = []
            with self.conn.transaction(), self.conn.cursor() as cur:
                for index, (sql, params) in enumerate(statements):
                    cur.execute(sql, params)
                    counts.append(cur.rowcount)
                    if index in required and cur.rowcount < 1:
                        raise StaleAlert
            return counts


@st.cache_resource
def get_db() -> Db:
    url = os.getenv("DATABASE_URL")
    if not url:
        st.error("Chưa đặt biến môi trường DATABASE_URL")
        st.stop()
    return Db(url)


def rows(sql: str, params: Any = None) -> list[dict]:
    return get_db().rows(sql, params)


def one(sql: str, params: Any = None) -> dict | None:
    result = rows(sql, params)
    return result[0] if result else None


def frame(sql: str, params: Any = None) -> pd.DataFrame:
    return pd.DataFrame(rows(sql, params))


# ---------------------------------------------------------------------------
# Đọc
# ---------------------------------------------------------------------------
def params_by_station() -> dict[str, dict]:
    sql = """SELECT p.*, s.station_name, s.neighbor_station_id
               FROM param_threshold p JOIN dim_substation s USING (station_id)"""
    return {r["station_id"]: r for r in rows(sql)}


def station_latest() -> dict[str, dict]:
    sql = "SELECT * FROM v_station_latest WHERE run_id = %s ORDER BY station_id"
    return {r["station_id"]: r for r in rows(sql, (RUN_ID,))}


def open_queue() -> list[dict]:
    sql = """SELECT q.*, EXTRACT(EPOCH FROM now() - q.opened_at) AS wait_sec
               FROM v_alert_queue q
              WHERE q.run_id = %s AND q.status <> 'RESOLVED'"""
    return rows(sql, (RUN_ID,))


def open_alert(station: str, alert_type: str) -> dict | None:
    """Cảnh báo đang mở của trạm, kèm đồng hồ dữ liệu (data_wait_min, deadline_min, is_overdue)."""
    sql = """SELECT * FROM v_alert_queue
              WHERE run_id = %s AND station_id = %s AND alert_type = %s
                AND status IN ('OPEN', 'ACK', 'ACTION_TAKEN')"""
    return one(sql, (RUN_ID, station, alert_type))


def load_series(station: str, start: Any, end: Any) -> pd.DataFrame:
    """Số đo và tải sau xử lý (mô phỏng) của một trạm."""
    sql = """SELECT ts, load_pct, load_pct_sim, s_h, s_h_sim, ot FROM v_load_sim
              WHERE run_id = %s AND station_id = %s AND ts >= %s AND ts <= %s ORDER BY ts"""
    return frame(sql, (RUN_ID, station, start, end))


def loads_at(station: str, ts: Any) -> dict | None:
    """Tải đo (load_pct) và tải sau chuyển tải (load_pct_sim) tại một mốc."""
    return one("SELECT load_pct, load_pct_sim FROM v_load_sim WHERE run_id = %s AND station_id = %s AND ts = %s",
               (RUN_ID, station, ts))


def alert_history(station: str, since: Any) -> list[dict]:
    sql = """SELECT alert_id, alert_type, severity, status, opened_ts, resolved_ts, peak_load_pct,
                    verdict, root_cause, received_load, handled_ts, clock_start_ts, action_ts,
                    data_wait_min, is_overdue, is_missed, is_late
               FROM v_alert_queue WHERE run_id = %s AND station_id = %s AND opened_ts >= %s
              ORDER BY opened_ts DESC"""
    return rows(sql, (RUN_ID, station, since))


def transfers(station: str | None = None, alert_id: int | None = None) -> list[dict]:
    """Các lần chuyển tải (đi hoặc đến trạm), mới nhất trước, kèm trạng thái quá hạn."""
    sql = """SELECT * FROM v_transfer_status
              WHERE run_id = %s AND action_type = 'TRANSFER'
                AND (%s::text IS NULL OR source_station = %s OR target_station = %s)
                AND (%s::bigint IS NULL OR alert_id = %s)
              ORDER BY effective_from_ts DESC, action_id DESC"""
    return rows(sql, (RUN_ID, station, station, station, alert_id, alert_id))


def last_red_result(station: str) -> dict | None:
    """Cảnh báo Đỏ đã đóng gần nhất của trạm (thẻ "Kết quả xử lý"), kể cả bỏ lỡ / báo giả."""
    sql = """SELECT * FROM v_alert_queue
              WHERE run_id = %s AND station_id = %s AND alert_type = 'OVERLOAD' AND severity = 'RED'
                AND status = 'RESOLVED'
              ORDER BY opened_ts DESC LIMIT 1"""
    return one(sql, (RUN_ID, station))


# ---------------------------------------------------------------------------
# Ghi (giờ thật luôn là now() của DB; mốc dữ liệu = mốc mới nhất của trạm)
# ---------------------------------------------------------------------------
NOW_TS = "(SELECT max(c.ts) FROM cleaned c WHERE c.station_id = fact_alert.station_id)"


def follow(alert_id: int, user: str) -> bool:
    """Vàng – "Theo dõi": ghi ai bấm, giờ thật và mốc dữ liệu."""
    sql = f"""UPDATE fact_alert SET status = 'ACK', acked_by = %s, acked_at = now(), acked_ts = {NOW_TS},
                     updated_at = now()
               WHERE alert_id = %s AND status = 'OPEN' AND severity = 'YELLOW'"""
    return get_db().run_tx([(sql, (user, alert_id))])[0] == 1


def mark_false_alarm(alert_id: int, user: str) -> bool:
    """Đỏ – "Báo giả": đóng ngay (resolved_ts = last_ts), detector theo dõi ngầm tới khi tự đóng."""
    sql = f"""UPDATE fact_alert SET verdict = 'FALSE_ALARM', verified_at = now(), status = 'RESOLVED',
                     acked_by = coalesce(acked_by, %s), acked_at = coalesce(acked_at, now()),
                     acked_ts = coalesce(acked_ts, {NOW_TS}),
                     resolved_ts = last_ts, resolved_at = now(), updated_at = now()
               WHERE alert_id = %s AND status IN ('OPEN', 'ACK')"""
    try:
        get_db().run_tx([(sql, (user, alert_id))], required=(0,))
    except StaleAlert:
        return False
    return True


def transfer_load(alert_id: int, amount: float, target: str, user: str) -> bool:
    """Đỏ – "Xác nhận chuyển tải": tự ghi verdict = REAL; hiệu lực từ mốc dữ liệu mới nhất của trạm
    (mô phỏng từ mốc kế tiếp). Lần đầu chuyển cảnh báo sang ACTION_TAKEN; có thể chuyển thêm.
    Trạm nhận bị giới hạn ngay lúc chuyển: lượng ≤ yellow_pct × định mức − S_H (sau chuyển) hiện tại.
    """
    update = f"""UPDATE fact_alert
                    SET status = 'ACTION_TAKEN', verdict = 'REAL', verified_at = coalesce(verified_at, now()),
                        acked_by = coalesce(acked_by, %s), acked_at = coalesce(acked_at, now()),
                        acked_ts = coalesce(acked_ts, {NOW_TS}),
                        action_done_at = coalesce(action_done_at, now()), action_ts = coalesce(action_ts, {NOW_TS}),
                        updated_at = now()
                  WHERE alert_id = %s AND severity = 'RED' AND status IN ('OPEN', 'ACK', 'ACTION_TAKEN')"""
    insert = """INSERT INTO fact_alert_action (alert_id, action_type, amount, target_station,
                                               effective_from_ts, done_by)
                SELECT a.alert_id, 'TRANSFER', %(amount)s, %(target)s,
                       (SELECT max(ts) FROM cleaned c WHERE c.station_id = a.station_id), %(user)s
                  FROM fact_alert a
                  JOIN v_station_latest n ON n.run_id = a.run_id AND n.station_id = %(target)s
                  JOIN param_threshold  p ON p.station_id = %(target)s
                 WHERE a.alert_id = %(alert_id)s
                   AND %(amount)s <= p.yellow_pct / 100.0 * n.rated_load - n.s_h_sim + 1e-9"""
    try:
        get_db().run_tx([(update, (user, alert_id)),
                         (insert, {"amount": amount, "target": target, "user": user, "alert_id": alert_id})],
                        required=(0, 1))
    except (StaleAlert, psycopg.errors.CheckViolation):
        return False
    return True


def send_to_maintenance(alert_id: int, user: str) -> bool:
    sql = f"""UPDATE fact_alert
                 SET status = CASE WHEN status = 'OPEN' THEN 'ACK' ELSE status END,
                     acked_by = coalesce(acked_by, %s), acked_at = coalesce(acked_at, now()),
                     acked_ts = coalesce(acked_ts, {NOW_TS}),
                     root_cause = 'SENSOR_MAINTENANCE', updated_at = now()
               WHERE alert_id = %s AND status IN ('OPEN', 'ACK')"""
    return get_db().run_tx([(sql, (user, alert_id))])[0] == 1


def save_root_cause(alert_id: int, text: str) -> bool:
    sql = "UPDATE fact_alert SET root_cause = %s, updated_at = now() WHERE alert_id = %s"
    return get_db().run_tx([(sql, (text, alert_id))])[0] == 1
