"""Phát hiện quá tải và cảm biến kẹt (v2, sped.md §2, §4).

Một phương pháp duy nhất: cảm biến đứng yên ≥ stuck_records → SENSOR; ngưỡng % tải và TTB
(thời gian đến khi chạm 100%) → Vàng / Đỏ. Dữ liệu KHÔNG dừng khi lên Đỏ.
Trả tải (chỉ chế độ DB), theo TẢI ĐO của trạm nguồn:
  SAFE    : < close_pct hai mốc liên tiếp sau khi chuyển
  TIMEOUT : đã chuyển ≥ 2 giờ dữ liệu và < yellow_pct
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INTERVAL = timedelta(minutes=15)
METHOD = "B"                            # tên phương pháp lưu trong detector_checkpoint (giữ tương thích v1)
RUN_IDS = ("LIVE", "HISTORY")
FIELDS = ["station_id", "alert_type", "severity", "trigger_rule", "status",
          "opened_ts", "escalated_ts", "last_ts", "resolved_ts", "load_pct",
          "peak_load_pct", "ttb_min", "speed_pct_h", "below_close_count", "received_load"]
PARAM_KEYS = ("rated_load", "yellow_pct", "red_pct", "yellow_ttb_min", "red_ttb_min",
              "close_pct", "close_records", "stuck_records")
TRANSFER_TIMEOUT = timedelta(hours=2)

Alert = dict[str, Any]
Params = dict[str, dict[str, float]]


def time_to_breach(load: float, speed: float | None, limit: float) -> float | None:
    """Phút đến khi chạm `limit` (%); None khi tải không tăng hoặc đã ≥ limit."""
    if speed is None or speed <= 0 or load >= limit:
        return None
    return (limit - load) / speed * 60


def classify(load: float, ttb: float | None, p: dict[str, float]) -> tuple[str | None, str | None]:
    """Mức và luật kích hoạt của một bản ghi (chưa xét vòng đời)."""
    if load >= p["red_pct"]:
        return "RED", "THRESHOLD"
    if ttb is not None and ttb <= p["red_ttb_min"]:
        return "RED", "TTB"
    if load >= p["yellow_pct"]:
        return "YELLOW", "THRESHOLD"
    if ttb is not None and ttb <= p["yellow_ttb_min"]:
        return "YELLOW", "TTB"
    return None, None


class Detector:
    """Một hàm phát hiện dùng chung cho --source csv và --source db.

    `process` trả về ảnh chụp các cảnh báo vừa thay đổi. Cờ `became_red` = True khi
    cảnh báo mở mới ở mức Đỏ hoặc nâng từ Vàng lên Đỏ. Cảnh báo bị đóng từ bên ngoài
    (báo giả) được đánh dấu `muted`: vẫn theo dõi ngầm đến khi tự đóng theo dữ liệu
    nhưng không trả về nữa, nên đợt quá tải đó không mở thêm cảnh báo.
    """

    def __init__(self, params: Params, state: dict | None = None) -> None:
        self.params = params
        self.state = state or {}

    def mute(self, station: str, kind: str) -> None:
        alert = self.state.get(station, {}).get("active", {}).get(kind)
        if alert is not None:
            alert["muted"] = True

    def process(self, station: str, ts: datetime, sh: float, load: float,
                trend_load: float | None = None, received: float | None = None) -> list[Alert]:
        """sh = S_H đo (xét kẹt); load = % tải dùng để xét ngưỡng (có thể là tải sau chuyển tải);
        trend_load = % tải ĐO dùng để tính tốc độ (mặc định = load). Chuyển tải là một khoảng bù
        cố định, không phải phụ tải tăng/giảm, nên tốc độ luôn lấy từ tải đo.
        received = lượng tải trạm đang nhận về ở mốc này (ghi vào cảnh báo khi mở)."""
        p = self.params[station]
        trend_load = load if trend_load is None else trend_load
        s = self.state.setdefault(station, {"history": [], "stuck": 0, "active": {}})
        history = s["history"]
        if history and ts <= datetime.fromisoformat(history[-1][0]):
            raise ValueError(f"Bản ghi trùng hoặc sai thứ tự: {station} {ts}")
        contiguous = bool(history) and ts - datetime.fromisoformat(history[-1][0]) == INTERVAL
        previous_sh = history[-1][1] if history else None
        if not contiguous:
            history.clear()
            s["stuck"] = 0
            if "OVERLOAD" in s["active"]:
                s["active"]["OVERLOAD"]["below_close_count"] = 0
        s["stuck"] = s["stuck"] + 1 if contiguous and sh == previous_sh else 1
        # history: [ts, s_h, load, trend_load]
        speed = trend_load - history[-4][-1] if len(history) >= 4 else None
        ttb = time_to_breach(load, speed, p["red_pct"])
        history.append([ts.isoformat(sep=" "), sh, load, trend_load])
        del history[:-4]
        reading = {"stamp": ts.isoformat(sep=" "), "load": load, "speed": speed, "ttb": ttb,
                   "received": received or None}
        actions: list[Alert] = []

        if s["stuck"] >= p["stuck_records"]:
            if "OVERLOAD" in s["active"]:
                s["active"]["OVERLOAD"]["below_close_count"] = 0
            self._update(s, station, "SENSOR", reading, actions, "SENSOR", "STUCK")
            return actions
        if "SENSOR" in s["active"] and previous_sh is not None and sh != previous_sh:
            self._update(s, station, "SENSOR", reading, actions, close=True)

        severity, rule = classify(load, ttb, p)
        if severity or "OVERLOAD" in s["active"]:
            self._update(s, station, "OVERLOAD", reading, actions, severity, rule)
        return actions

    def _update(self, s: dict, station: str, kind: str, reading: dict, actions: list[Alert],
                severity: str | None = None, rule: str | None = None, close: bool = False) -> None:
        p = self.params[station]
        active = s["active"]
        stamp, load = reading["stamp"], reading["load"]
        alert = active.get(kind)
        was_red = alert is not None and alert["severity"] == "RED"
        if alert is None:
            if severity is None:
                return
            alert = {"station_id": station, "alert_type": kind, "severity": severity,
                     "trigger_rule": rule, "status": "OPEN", "opened_ts": stamp,
                     "escalated_ts": stamp if severity == "RED" else None,
                     "peak_load_pct": load, "below_close_count": 0,
                     "received_load": reading["received"]}
            active[kind] = alert
        alert.update(last_ts=stamp, load_pct=load, ttb_min=reading["ttb"],
                     speed_pct_h=reading["speed"], peak_load_pct=max(alert["peak_load_pct"], load))
        if severity == "RED" and alert["severity"] != "RED":
            alert.update(severity="RED", escalated_ts=stamp, trigger_rule=rule)
        if kind == "OVERLOAD":
            alert["below_close_count"] = alert["below_close_count"] + 1 if load < p["close_pct"] else 0
            close = alert["below_close_count"] >= p["close_records"]
        alert["resolved_ts"] = stamp if close else None
        if close:
            alert["status"] = "RESOLVED"
        if not alert.get("muted"):
            snapshot = alert.copy()
            snapshot["became_red"] = alert["severity"] == "RED" and not was_red
            actions.append(snapshot)
        if close:
            del active[kind]


def read_params(path: str | Path) -> Params:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    params: Params = {}
    for row in rows:
        station = row["station_id"]
        if station in params:
            raise ValueError(f"Trạm trùng trong tham số: {station}")
        params[station] = {key: float(row[key]) for key in PARAM_KEYS}
        if params[station]["rated_load"] <= 0:
            raise ValueError(f"Định mức không hợp lệ: {station}")
    return params


def run_csv(args: argparse.Namespace) -> None:
    params = read_params(args.params)
    detector = Detector(params)
    with open(args.input, encoding="utf-8-sig", newline="") as f:
        rows = sorted(csv.DictReader(f), key=lambda row: (row["ts"], row["station_id"]))
    alerts: dict[tuple, Alert] = {}
    for row in rows:
        station = row["station_id"]
        sh = math.hypot(float(row["HUFL"]), float(row["HULL"]))
        load = 100 * sh / params[station]["rated_load"]
        if not math.isfinite(load):
            raise ValueError(f"Giá trị tải không hợp lệ: {station} {row['ts']}")
        for alert in detector.process(station, datetime.fromisoformat(row["ts"]), sh, load):
            alerts[(station, alert["alert_type"], alert["opened_ts"])] = alert
    output = args.output or "alerts.csv"
    with open(output, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(alerts.values())
    print(f"Processed {len(rows)} readings; {len(alerts)} alerts -> {output}")


# ---------------------------------------------------------------------------
# Chế độ DB
# ---------------------------------------------------------------------------
UPDATE_ALERT_SQL = """
UPDATE fact_alert SET severity=%(severity)s, trigger_rule=%(trigger_rule)s,
       status=CASE WHEN %(status)s = 'RESOLVED' THEN 'RESOLVED' ELSE status END,
       escalated_ts=%(escalated_ts)s, last_ts=%(last_ts)s, resolved_ts=%(resolved_ts)s,
       resolved_at=CASE WHEN %(status)s = 'RESOLVED' THEN now() END,
       load_pct=%(load_pct)s, peak_load_pct=%(peak_load_pct)s, ttb_min=%(ttb_min)s,
       speed_pct_h=%(speed_pct_h)s, below_close_count=%(below_close_count)s, updated_at=now()
 WHERE run_id=%(run_id)s AND station_id=%(station_id)s AND alert_type=%(alert_type)s
   AND opened_ts=%(opened_ts)s AND status IN ('OPEN', 'ACK', 'ACTION_TAKEN')
RETURNING alert_id"""

INSERT_ALERT_SQL = """
INSERT INTO fact_alert (run_id, station_id, alert_type, severity, trigger_rule, status,
       opened_ts, escalated_ts, last_ts, resolved_ts, resolved_at, load_pct, peak_load_pct,
       ttb_min, speed_pct_h, below_close_count, received_load)
VALUES (%(run_id)s, %(station_id)s, %(alert_type)s, %(severity)s, %(trigger_rule)s, %(status)s,
        %(opened_ts)s, %(escalated_ts)s, %(last_ts)s, %(resolved_ts)s,
        CASE WHEN %(status)s = 'RESOLVED' THEN now() END, %(load_pct)s, %(peak_load_pct)s,
        %(ttb_min)s, %(speed_pct_h)s, %(below_close_count)s, %(received_load)s)
RETURNING alert_id"""

# s_h = số ĐO (xét cảm biến kẹt); load_pct = tải đo (tính tốc độ);
# load_pct_sim = tải sau chuyển tải mô phỏng (xét ngưỡng % và TTB); transfer_in = tải đang nhận về
NEW_ROWS_SQL = """
SELECT c.ts, c.station_id, c.s_h, c.load_pct, c.load_pct_sim, c.transfer_in
  FROM v_load_sim c
  JOIN unnest(%s::text[], %s::timestamp[]) AS w (station_id, last_ts) ON w.station_id = c.station_id
 WHERE c.run_id = %s AND c.ts > w.last_ts
 ORDER BY c.ts, c.station_id
 LIMIT %s"""

ACTIVE_TRANSFERS_SQL = """
SELECT count(*) AS n FROM fact_alert_action x JOIN fact_alert a USING (alert_id)
 WHERE a.run_id = %s AND x.action_type = 'TRANSFER' AND x.effective_to_ts IS NULL"""

# Trả tải SAFE: TẢI ĐO của trạm nguồn < close_pct ở mốc này và mốc liền trước (cả hai sau lúc chuyển)
SAFE_RETURN_SQL = """
UPDATE fact_alert_action x SET effective_to_ts = %(ts)s, return_reason = 'SAFE'
  FROM fact_alert a, param_threshold p
 WHERE x.alert_id = a.alert_id AND p.station_id = a.station_id
   AND a.run_id = %(run_id)s AND a.station_id = %(station_id)s
   AND x.action_type = 'TRANSFER' AND x.effective_to_ts IS NULL
   AND %(ts)s - INTERVAL '15 minutes' > x.effective_from_ts
   AND (SELECT count(*) FROM cleaned c
         WHERE c.station_id = a.station_id AND c.load_pct < p.close_pct
           AND c.ts IN (%(ts)s, %(ts)s - INTERVAL '15 minutes')) = 2"""

# Trả tải TIMEOUT: đã chuyển ≥ 2 giờ dữ liệu và TẢI ĐO của trạm nguồn ở mốc này < yellow_pct
TIMEOUT_RETURN_SQL = """
UPDATE fact_alert_action x SET effective_to_ts = %(ts)s, return_reason = 'TIMEOUT'
  FROM fact_alert a, param_threshold p
 WHERE x.alert_id = a.alert_id AND p.station_id = a.station_id
   AND a.run_id = %(run_id)s AND a.station_id = %(station_id)s
   AND x.action_type = 'TRANSFER' AND x.effective_to_ts IS NULL
   AND %(ts)s - x.effective_from_ts >= %(timeout)s
   AND (SELECT c.load_pct FROM cleaned c WHERE c.station_id = a.station_id AND c.ts = %(ts)s) < p.yellow_pct"""


def write_alert(cur: Any, run_id: str, alert: Alert) -> int | None:
    """Ghi cột của detector, không đụng cột người trực nhập.

    Trả về alert_id; None nếu cảnh báo đã bị đóng từ bên ngoài (báo giả).
    """
    values = {field: alert.get(field) for field in FIELDS} | {"run_id": run_id}
    cur.execute(UPDATE_ALERT_SQL, values)
    row = cur.fetchone()
    if row:
        return row["alert_id"]
    cur.execute("""SELECT 1 FROM fact_alert WHERE run_id=%(run_id)s AND station_id=%(station_id)s
                     AND alert_type=%(alert_type)s AND opened_ts=%(opened_ts)s""", values)
    if cur.fetchone():
        return None
    cur.execute(INSERT_ALERT_SQL, values)
    return cur.fetchone()["alert_id"]


def fetch_new_rows(cur: Any, detector: Detector, run_id: str, limit: int) -> list[dict]:
    """Bản ghi mới của mọi trạm, xếp theo ts (rồi station_id) để xử lý tuần tự."""
    names, last = [], []
    for station in detector.params:
        history = detector.state.get(station, {}).get("history", [])
        names.append(station)
        last.append(history[-1][0] if history else "1900-01-01")
    cur.execute(NEW_ROWS_SQL, (names, last, run_id, limit))
    return cur.fetchall()


def has_active_transfer(cur: Any, run_id: str) -> bool:
    cur.execute(ACTIVE_TRANSFERS_SQL, (run_id,))
    return cur.fetchone()["n"] > 0


def handle_row(cur: Any, detector: Detector, run_id: str, row: dict, transfers: bool) -> None:
    if transfers:
        values = {"ts": row["ts"], "run_id": run_id, "station_id": row["station_id"], "timeout": TRANSFER_TIMEOUT}
        cur.execute(SAFE_RETURN_SQL, values)
        cur.execute(TIMEOUT_RETURN_SQL, values)
    for alert in detector.process(row["station_id"], row["ts"], row["s_h"], row["load_pct_sim"],
                                  row["load_pct"], row["transfer_in"]):
        if write_alert(cur, run_id, alert) is None:
            detector.mute(row["station_id"], alert["alert_type"])


def open_detector(cur: Any, args: argparse.Namespace) -> Detector:
    cur.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS locked", ("detector:" + args.run_id,))
    if not cur.fetchone()["locked"]:
        raise SystemExit("Đã có detector chạy với run_id này")
    cur.execute(f"SELECT station_id, {', '.join(PARAM_KEYS)} FROM param_threshold")
    params = {row.pop("station_id"): {k: float(v) for k, v in row.items()} for row in cur.fetchall()}
    cur.execute("SELECT method, state FROM detector_checkpoint WHERE run_id = %s", (args.run_id,))
    checkpoint = cur.fetchone()
    if checkpoint and checkpoint["method"] != METHOD:
        raise SystemExit("Checkpoint của run_id này thuộc phương pháp cũ; hãy chạy sql/reset_replay.sql")
    if not checkpoint:
        cur.execute("SELECT 1 FROM fact_alert WHERE run_id = %s LIMIT 1", (args.run_id,))
        if cur.fetchone():
            raise SystemExit("run_id đã có cảnh báo nhưng thiếu checkpoint; hãy chạy sql/reset_replay.sql")
    return Detector(params, checkpoint["state"] if checkpoint else None)


def run_db(args: argparse.Namespace) -> None:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise SystemExit("Cần cài psycopg: pip install -r detector/requirements.txt") from exc
    if not args.database_url:
        raise SystemExit("Cần --database-url hoặc biến môi trường DATABASE_URL")
    with psycopg.connect(args.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            detector = open_detector(cur, args)
        conn.commit()
        while True:
            with conn.cursor() as cur:
                rows = fetch_new_rows(cur, detector, args.run_id, args.batch_size)
                transfers = bool(rows) and has_active_transfer(cur, args.run_id)
                for row in rows:
                    handle_row(cur, detector, args.run_id, row, transfers)
                cur.execute("""INSERT INTO detector_checkpoint (run_id, method, state)
                               VALUES (%s, %s, %s::jsonb) ON CONFLICT (run_id) DO UPDATE
                               SET state = EXCLUDED.state""",
                            (args.run_id, METHOD, json.dumps(detector.state)))
            conn.commit()
            if not rows:
                if args.once:
                    break
                time.sleep(args.poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=("csv", "db"), required=True)
    parser.add_argument("--input", default=ROOT / "data/scenario_data.csv")
    parser.add_argument("--params", default=ROOT / "data/param_threshold.csv")
    parser.add_argument("--output")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--run-id", choices=RUN_IDS, default="LIVE",
                        help="LIVE = vận hành / phát lại; HISTORY = chạy một lượt toàn bộ 8 tháng cho Power BI")
    parser.add_argument("--poll-seconds", type=float, default=2)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--once", action="store_true", help="DB: xử lý hết dữ liệu hiện có rồi thoát")
    args = parser.parse_args()
    if args.batch_size < 1 or args.poll_seconds <= 0:
        parser.error("batch-size và poll-seconds phải dương")
    if args.source == "csv":
        run_csv(args)
    else:
        run_db(args)


if __name__ == "__main__":
    main()
