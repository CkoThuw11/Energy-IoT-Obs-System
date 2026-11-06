#!/usr/bin/env python3
"""
producer.py - Phát lại scenario_data.csv vào bảng raw như cảm biến gửi về.

- Đọc CSV, phát theo thứ tự ts. Mỗi mốc ts ghi đủ các trạm trong MỘT transaction,
  nên thủ tục ELT không bao giờ thấy một mốc ghi dở.
- Chạy lại không trùng: INSERT ... ON CONFLICT (station_id, ts) DO NOTHING.
- --speed: hệ số tăng tốc so với thời gian của dữ liệu (15 phút dữ liệu = 900 giây thật / speed).
    x1 = đúng thời gian thật, x10 = 90 giây / mốc, x60 = 15 giây / mốc, 0 = chạy hết tốc độ.
- --start: bắt đầu phát từ ngày/giờ này (mặc định: dòng đầu tiên của CSV).
- --days / --end: dừng sau N ngày / trước mốc --end (để phát đúng 1 ngày khi kiểm tra).
- --event EVxxx: phát lại đúng một sự cố trong data/event_label.csv, từ t_start − 2 giờ đến
  t_end + 2 giờ (chỉ dùng t_start / t_end để chọn khoảng; detector không đọc đáp án).
- Trước MỖI mốc ts, đọc replay_control.paused của run LIVE; đang dừng (detector bật khi có
  cảnh báo Đỏ) thì chờ, kiểm tra lại mỗi 1 giây. Không bao giờ dừng giữa một mốc ts.
  Thời gian dừng được cộng vào lịch phát.

Ví dụ:
    python producer/producer.py --speed 60 --start 2017-11-01
    python producer/producer.py --speed 0 --start 2017-11-01 --days 1
    python producer/producer.py --event EV029 --speed 60
"""
import argparse
import csv
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

INTERVAL_SEC = 15 * 60  # chu kỳ lấy mẫu của dữ liệu: 15 phút

PAUSE_POLL_SEC = 1.0
MAX_SAFE_SPEED = 60      # nhanh hơn (15 giây / mốc) thì detector có thể dừng trễ hơn 1 bản ghi
RUN_IDS = ("LIVE",)
EVENT_MARGIN = timedelta(hours=2)

PAUSED_SQL = "SELECT paused, paused_at_ts, reason FROM replay_control WHERE run_id = %s"

COLUMNS = ["HUFL", "HULL", "MUFL", "MULL", "LUFL", "LULL", "OT"]

INSERT_SQL = (
    "INSERT INTO raw (ts, station_id, hufl, hull, mufl, mull, lufl, lull, ot) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (station_id, ts) DO NOTHING"
)


def parse_speed(text: str) -> float:
    """Chấp nhận '60', 'x60', '×60', '0'."""
    t = text.strip().lower().lstrip("x×")
    try:
        value = float(t)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--speed không hợp lệ: {text!r} (ví dụ: 1, 10, x60, 0)")
    if value < 0:
        raise argparse.ArgumentTypeError("--speed phải >= 0 (0 = chạy hết tốc độ)")
    return value


def parse_dt(text: str) -> datetime:
    """Chấp nhận 'YYYY-MM-DD' hoặc 'YYYY-MM-DD HH:MM[:SS]'."""
    try:
        return datetime.fromisoformat(text.strip())
    except ValueError:
        raise argparse.ArgumentTypeError(f"Ngày không hợp lệ: {text!r} (dùng YYYY-MM-DD hoặc 'YYYY-MM-DD HH:MM')")


def default_dsn() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    user = os.environ.get("POSTGRES_USER", "energy")
    pwd = os.environ.get("POSTGRES_PASSWORD", "energy")
    db = os.environ.get("POSTGRES_DB", "energy")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5433")  # cổng host trong docker-compose.yml
    return f"postgresql://{user}:{pwd}@{host}:{port}/{db}"


def build_parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description="Phát lại scenario_data.csv vào bảng raw.")
    p.add_argument("--csv", type=Path, default=repo_root / "data" / "scenario_data.csv",
                   help="file kịch bản (mặc định: data/scenario_data.csv)")
    p.add_argument("--speed", type=parse_speed, default=1.0,
                   help="hệ số tăng tốc: 1, 10, 60 ... ; 0 = hết tốc độ (mặc định 1)")
    p.add_argument("--start", type=parse_dt, default=None,
                   help="bắt đầu phát từ ngày này (mặc định: đầu file)")
    p.add_argument("--event", default=None,
                   help="phát lại một sự cố của event_label.csv (vd EV029): t_start − 2h → t_end + 2h")
    p.add_argument("--labels", type=Path, default=repo_root / "data" / "event_label.csv",
                   help="file đáp án dùng cho --event (mặc định: data/event_label.csv)")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--days", type=float, default=None, help="chỉ phát N ngày kể từ --start")
    group.add_argument("--end", type=parse_dt, default=None, help="dừng trước mốc này (không gồm mốc này)")
    p.add_argument("--run-id", choices=RUN_IDS, default="LIVE",
                   help="run đọc replay_control để tự dừng khi có Đỏ (mặc định LIVE)")
    p.add_argument("--dsn", default=None, help="chuỗi kết nối DB (mặc định: biến môi trường / localhost:5433)")
    p.add_argument("--log-every", type=int, default=None,
                   help="in log sau mỗi N mốc ts (mặc định: tự chọn theo tốc độ)")
    p.add_argument("--dry-run", action="store_true", help="chỉ đọc CSV và in kế hoạch phát, không ghi DB")
    return p


def load_batches(path: Path, start, end):
    """Trả về list [(ts, [dòng của các trạm])] đã sắp theo ts, lọc theo [start, end)."""
    if not path.exists():
        sys.exit(f"Không thấy file {path}")
    by_ts = {}
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = {"ts", "station_id", *COLUMNS} - set(reader.fieldnames or [])
        if missing:
            sys.exit(f"CSV thiếu cột: {sorted(missing)}")
        for row in reader:
            ts = datetime.fromisoformat(row["ts"])
            if start and ts < start:
                continue
            if end and ts >= end:
                continue
            values = [float(row[c]) if row[c] != "" else None for c in COLUMNS]
            by_ts.setdefault(ts, []).append((row["station_id"], values))
    return [(ts, sorted(by_ts[ts], key=lambda x: x[0])) for ts in sorted(by_ts)]


def event_window(path: Path, event_id: str) -> tuple[datetime, datetime]:
    """[t_start − 2h, t_end + 2h] của một sự cố (mốc --end không gồm, nên cộng thêm 15 phút)."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["event_id"] == event_id:
                start = datetime.fromisoformat(row["t_start"]) - EVENT_MARGIN
                end = datetime.fromisoformat(row["t_end"]) + EVENT_MARGIN + timedelta(seconds=INTERVAL_SEC)
                print(f"Sự cố {event_id}: {row['station_id']} {row['type']} {row['detail']} "
                      f"{row['t_start']} → {row['t_end']}", flush=True)
                return start, end
    sys.exit(f"Không thấy sự cố {event_id} trong {path}")


def read_pause(cur: Any, run_id: str) -> tuple | None:
    """(paused_at_ts, reason) nếu run đang dừng, ngược lại None."""
    cur.execute(PAUSED_SQL, (run_id,))
    row = cur.fetchone()
    return (row[1], row[2]) if row and row[0] else None


def wait_while_paused(conn: Any, cur: Any, run_id: str, next_ts: datetime) -> float:
    """Chờ đến khi replay_control.paused = false. Trả về số giây đã chờ."""
    pause = read_pause(cur, run_id)  # READ COMMITTED: luôn thấy giá trị mới nhất, không cần commit
    if pause is None:
        return 0.0
    conn.commit()  # đẩy các mốc đã ghi trước khi chờ
    started = time.monotonic()
    print(f"[{next_ts}] TẠM DỪNG ({run_id}): cảnh báo Đỏ alert_id={pause[1]} lúc dữ liệu {pause[0]}. "
          f"Chờ người trực xử lý trên dashboard...", flush=True)
    while pause is not None:
        time.sleep(PAUSE_POLL_SEC)
        pause = read_pause(cur, run_id)
        conn.commit()
    waited = time.monotonic() - started
    print(f"[{next_ts}] CHẠY TIẾP sau {waited:.0f} giây dừng.", flush=True)
    return waited


def main() -> int:
    args = build_parser().parse_args()

    start = args.start
    end = args.end
    if args.event:
        if start or end or args.days is not None:
            sys.exit("--event không dùng chung với --start / --end / --days")
        start, end = event_window(args.labels, args.event)
    if args.days is not None:
        if start is None:
            # --days tính từ dòng đầu của file; xác định sau khi đọc
            pass
        else:
            end = start + timedelta(days=args.days)

    batches = load_batches(args.csv, start, end)
    if args.days is not None and start is None and batches:
        end = batches[0][0] + timedelta(days=args.days)
        batches = [b for b in batches if b[0] < end]
    if not batches:
        sys.exit("Không có dòng nào trong khoảng thời gian đã chọn (kiểm tra --start / --days / --end).")

    total_rows = sum(len(rows) for _, rows in batches)
    first_ts, last_ts = batches[0][0], batches[-1][0]
    wait_per_ts = INTERVAL_SEC / args.speed if args.speed > 0 else 0.0
    print(f"Phát {len(batches)} mốc ts / {total_rows} dòng, từ {first_ts} đến {last_ts}; "
          f"speed={'max' if args.speed == 0 else 'x' + format(args.speed, 'g')} "
          f"({wait_per_ts:.1f} giây / mốc 15 phút)", flush=True)
    if args.speed == 0 or args.speed > MAX_SAFE_SPEED:
        print(f"CẢNH BÁO: nên chạy với --speed ≤ {MAX_SAFE_SPEED}; nhanh hơn thì producer có thể "
              f"dừng trễ hơn 1 bản ghi so với lúc lên Đỏ. Vẫn tiếp tục.", flush=True)
    if args.dry_run:
        print("--dry-run: không ghi DB.")
        return 0

    try:
        import psycopg  # import muộn để --dry-run chạy được khi chưa cài driver
    except ImportError:
        sys.exit("Thiếu thư viện: pip install -r producer/requirements.txt")

    try:
        conn = psycopg.connect(args.dsn or default_dsn())
    except Exception as exc:  # noqa: BLE001 - báo lỗi kết nối gọn cho người dùng
        sys.exit(f"Không kết nối được DB: {exc}\nKiểm tra 'docker compose ps' và cổng 5433.")

    # speed = 0: gom nhiều mốc vào một lần commit cho nhanh; vẫn cắt đúng ranh giới mốc ts.
    commit_every = 1 if args.speed > 0 else 96
    if args.log_every:
        log_every = args.log_every
    else:
        log_every = 1 if (args.speed > 0 and wait_per_ts >= 1.0) else 96

    sent = inserted = 0
    t0 = started = time.monotonic()
    done_ts = 0
    pending = 0
    cur = conn.cursor()
    try:
        for i, (ts, rows) in enumerate(batches):
            if args.speed > 0:
                # hẹn giờ theo mốc tuyệt đối để không bị trôi dần
                target = t0 + (ts - first_ts).total_seconds() / args.speed
                delay = target - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
            # kiểm tra dừng trước MỖI mốc ts; thời gian dừng đẩy lùi cả lịch phát
            waited = wait_while_paused(conn, cur, args.run_id, ts)
            if waited:
                t0 += waited
                pending = 0
            for station_id, values in rows:
                cur.execute(INSERT_SQL, (ts, station_id, *values))
                sent += 1
                if cur.rowcount and cur.rowcount > 0:
                    inserted += cur.rowcount
            pending += 1
            done_ts += 1
            if pending >= commit_every:
                conn.commit()
                pending = 0
            if done_ts % log_every == 0 or i == len(batches) - 1:
                conn.commit()
                pending = 0
                print(f"[{ts}] đã phát {done_ts}/{len(batches)} mốc | gửi {sent} dòng, "
                      f"ghi mới {inserted}, bỏ qua trùng {sent - inserted}", flush=True)
        conn.commit()
    except KeyboardInterrupt:
        conn.rollback()  # mốc đang ghi dở bị hủy; các mốc đã commit vẫn còn
        print(f"\nĐã dừng. Phát tới ts={batches[max(done_ts - 1, 0)][0]} "
              f"({sent} dòng gửi, {inserted} dòng mới). Chạy lại với --start để phát tiếp.")
        return 130
    finally:
        conn.close()

    print(f"Xong: {done_ts} mốc ts, gửi {sent} dòng, ghi mới {inserted}, bỏ qua trùng {sent - inserted}, "
          f"mất {time.monotonic() - started:.1f} giây.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
