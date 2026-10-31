"""Replay or continuously detect transformer overload and stuck sensors."""

import argparse
import csv
import json
import math
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTERVAL = timedelta(minutes=15)
FIELDS = ["station_id", "alert_type", "severity", "trigger_rule", "status",
          "opened_ts", "escalated_ts", "last_ts", "resolved_ts", "load_pct",
          "peak_load_pct", "ttb_min", "speed_pct_h", "below_close_count"]


class Detector:
    def __init__(self, params, method, state=None):
        self.params = params
        self.method = method
        self.state = state or {}

    def process(self, station, ts, sh, load):
        p = self.params[station]
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
        speed = load - history[-4][2] if len(history) >= 4 else None
        ttb = (100 - load) / speed * 60 if speed is not None and speed > 0 else None
        history.append([ts.isoformat(sep=" "), sh, load])
        del history[:-4]
        stamp = ts.isoformat(sep=" ")
        actions = []

        def update(kind, severity=None, rule=None, close=False):
            active = s["active"]
            alert = active.get(kind)
            if alert is None:
                if severity is None:
                    return
                alert = {"station_id": station, "alert_type": kind, "severity": severity,
                         "trigger_rule": rule, "status": "OPEN", "opened_ts": stamp,
                         "escalated_ts": stamp if severity == "RED" else None,
                         "peak_load_pct": load, "below_close_count": 0}
                active[kind] = alert
            alert.update(last_ts=stamp, load_pct=load, ttb_min=ttb,
                         speed_pct_h=speed, peak_load_pct=max(alert["peak_load_pct"], load))
            if severity == "RED" and alert["severity"] != "RED":
                alert.update(severity="RED", escalated_ts=stamp, trigger_rule=rule)
            if kind == "OVERLOAD":
                alert["below_close_count"] = alert["below_close_count"] + 1 if load < p["close_pct"] else 0
                close = alert["below_close_count"] >= p["close_records"]
            alert["resolved_ts"] = stamp if close else None
            if close:
                alert["status"] = "RESOLVED"
            actions.append(alert.copy())
            if close:
                del active[kind]

        if self.method == "B":
            if s["stuck"] >= p["stuck_records"]:
                if "OVERLOAD" in s["active"]:
                    s["active"]["OVERLOAD"]["below_close_count"] = 0
                update("SENSOR", "SENSOR", "STUCK")
                return actions
            if "SENSOR" in s["active"] and previous_sh is not None and sh != previous_sh:
                update("SENSOR", close=True)

        severity = rule = None
        if load >= p["red_pct"]:
            severity, rule = "RED", "THRESHOLD"
        elif self.method == "B" and ttb is not None and ttb <= p["red_ttb_min"]:
            severity, rule = "RED", "TTB"
        elif load >= p["yellow_pct"]:
            severity, rule = "YELLOW", "THRESHOLD"
        elif self.method == "B" and ttb is not None and ttb <= p["yellow_ttb_min"]:
            severity, rule = "YELLOW", "TTB"
        if severity or "OVERLOAD" in s["active"]:
            update("OVERLOAD", severity, rule)
        return actions


def read_params(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    params = {}
    for row in rows:
        station = row["station_id"]
        if station in params:
            raise ValueError(f"Trạm trùng trong tham số: {station}")
        params[station] = {key: float(row[key]) for key in (
            "rated_load", "yellow_pct", "red_pct", "yellow_ttb_min", "red_ttb_min",
            "close_pct", "close_records", "stuck_records")}
        if params[station]["rated_load"] <= 0:
            raise ValueError(f"Định mức không hợp lệ: {station}")
    return params


def run_csv(args):
    params = read_params(args.params)
    detector = Detector(params, args.method)
    with open(args.input, encoding="utf-8-sig", newline="") as f:
        rows = sorted(csv.DictReader(f), key=lambda row: (row["ts"], row["station_id"]))
    alerts = {}
    for row in rows:
        station = row["station_id"]
        sh = math.hypot(float(row["HUFL"]), float(row["HULL"]))
        load = 100 * sh / params[station]["rated_load"]
        if not math.isfinite(load):
            raise ValueError(f"Giá trị tải không hợp lệ: {station} {row['ts']}")
        for alert in detector.process(station, datetime.fromisoformat(row["ts"]), sh, load):
            alerts[(station, alert["alert_type"], alert["opened_ts"])] = alert
    output = args.output or f"alerts_{args.method}.csv"
    with open(output, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(alerts.values())
    print(f"Processed {len(rows)} readings; {len(alerts)} alerts -> {output}")


def run_db(args):
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise SystemExit("Cần cài psycopg: pip install -r detector/requirements.txt") from exc
    if not args.database_url:
        raise SystemExit("Cần --database-url hoặc biến môi trường DATABASE_URL")
    with psycopg.connect(args.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS locked", ("detector:" + args.run_id,))
            if not cur.fetchone()["locked"]:
                raise SystemExit("Đã có detector chạy với run_id này")
            cur.execute("""CREATE TABLE IF NOT EXISTS detector_checkpoint (
                run_id TEXT PRIMARY KEY, method TEXT NOT NULL, state JSONB NOT NULL)""")
            cur.execute("SELECT * FROM param_threshold")
            params = {row["station_id"]: row for row in cur.fetchall()}
            cur.execute("SELECT method, state FROM detector_checkpoint WHERE run_id = %s", (args.run_id,))
            checkpoint = cur.fetchone()
            if checkpoint and checkpoint["method"] != args.method:
                raise SystemExit("run_id đã dùng cho method khác")
            if not checkpoint:
                cur.execute("SELECT 1 FROM fact_alert WHERE run_id = %s LIMIT 1", (args.run_id,))
                if cur.fetchone():
                    raise SystemExit("run_id đã có cảnh báo nhưng thiếu checkpoint; hãy dùng run_id khác")
            detector = Detector(params, args.method, checkpoint["state"] if checkpoint else None)
        conn.commit()
        while True:
            count = 0
            with conn.cursor() as cur:
                for station in params:
                    history = detector.state.get(station, {}).get("history", [])
                    last_ts = history[-1][0] if history else "1900-01-01"
                    cur.execute("""SELECT ts, s_h, load_pct FROM cleaned
                                   WHERE station_id = %s AND ts > %s ORDER BY ts LIMIT %s""",
                                (station, last_ts, args.batch_size))
                    for row in cur.fetchall():
                        count += 1
                        for alert in detector.process(station, row["ts"], row["s_h"], row["load_pct"]):
                            if alert["status"] == "RESOLVED":
                                cur.execute("""UPDATE fact_alert SET status='RESOLVED',
                                    last_ts=%s, resolved_ts=%s, resolved_at=now(), load_pct=%s,
                                    peak_load_pct=%s, ttb_min=%s, speed_pct_h=%s,
                                    below_close_count=%s, updated_at=now()
                                    WHERE run_id=%s AND station_id=%s AND alert_type=%s
                                      AND status IN ('OPEN', 'ACK')""",
                                    (alert["last_ts"], alert["resolved_ts"], alert["load_pct"],
                                     alert["peak_load_pct"], alert["ttb_min"], alert["speed_pct_h"],
                                     alert["below_close_count"], args.run_id, station, alert["alert_type"]))
                                if cur.rowcount != 1:
                                    raise RuntimeError("Không tìm thấy cảnh báo đang mở trong fact_alert")
                                continue
                            values = [alert.get(field) for field in FIELDS]
                            cur.execute("""INSERT INTO fact_alert
                                (run_id, station_id, alert_type, severity, trigger_rule, status,
                                 opened_ts, escalated_ts, last_ts, resolved_ts, load_pct,
                                 peak_load_pct, ttb_min, speed_pct_h, below_close_count, resolved_at)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                        CASE WHEN %s = 'RESOLVED' THEN now() END)
                                ON CONFLICT (run_id, station_id, alert_type)
                                    WHERE status IN ('OPEN', 'ACK') DO UPDATE SET
                                severity=EXCLUDED.severity, trigger_rule=EXCLUDED.trigger_rule,
                                status=CASE WHEN EXCLUDED.status='RESOLVED' THEN 'RESOLVED' ELSE fact_alert.status END,
                                escalated_ts=EXCLUDED.escalated_ts, last_ts=EXCLUDED.last_ts,
                                resolved_ts=EXCLUDED.resolved_ts, resolved_at=EXCLUDED.resolved_at,
                                load_pct=EXCLUDED.load_pct, peak_load_pct=EXCLUDED.peak_load_pct,
                                ttb_min=EXCLUDED.ttb_min, speed_pct_h=EXCLUDED.speed_pct_h,
                                below_close_count=EXCLUDED.below_close_count, updated_at=now()""",
                                [args.run_id, *values, alert["status"]])
                cur.execute("""INSERT INTO detector_checkpoint (run_id, method, state)
                               VALUES (%s, %s, %s::jsonb) ON CONFLICT (run_id) DO UPDATE
                               SET state = EXCLUDED.state""",
                            (args.run_id, args.method, json.dumps(detector.state)))
            conn.commit()
            if args.once:
                if count == 0:
                    break
            elif count == 0:
                time.sleep(args.poll_seconds)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("csv", "db"), required=True)
    parser.add_argument("--method", choices=("A", "B"), required=True)
    parser.add_argument("--input", default=ROOT / "data/scenario_data.csv")
    parser.add_argument("--params", default=ROOT / "data/param_threshold.csv")
    parser.add_argument("--output")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--run-id", default="LIVE")
    parser.add_argument("--poll-seconds", type=float, default=10)
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
