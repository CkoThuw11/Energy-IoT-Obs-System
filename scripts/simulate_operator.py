"""Giả lập người trực trong lúc phát lại (v2, sped.md §4) – gọi ĐÚNG các hàm ghi của dashboard/db.py.

Chạy BÊN TRONG container dashboard (có streamlit, psycopg, DATABASE_URL):
    docker cp scripts/simulate_operator.py energy-dashboard:/app/
    docker exec energy-dashboard python /app/simulate_operator.py --red-plan ontime,ignore,late

Kế hoạch cho từng cảnh báo Đỏ MỚI (theo thứ tự lên Đỏ, lặp lại nếu hết danh sách):
    ontime : chuyển tải lượng gợi ý sau --ontime-min phút DỮ LIỆU kể từ lúc lên Đỏ (mặc định 15 ≤ 30)
    late   : chuyển tải sau --late-min phút dữ liệu (mặc định 45 > 30 → trễ)
    ignore : không làm gì → cảnh báo tự đóng khi tải về bình thường → "bị bỏ lỡ"
    false  : bấm "Báo giả" sau --ontime-min phút dữ liệu
Vàng: --yellow follow (mặc định) bấm "Theo dõi" sau --yellow-min phút dữ liệu (mặc định 15); ignore = bỏ qua.
Dừng sau --minutes phút giờ thật (mặc định 60) hoặc Ctrl+C.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from datetime import datetime

sys.path.insert(0, "/app")
logging.getLogger("streamlit").setLevel(logging.ERROR)

import db  # noqa: E402  (dashboard/db.py)

OPERATOR = "Người trực A (giả lập)"


def say(text: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {text}", flush=True)


def suggestion(station: str) -> tuple[float, str | None]:
    """Lượng gợi ý (như page_station.suggested_amount) giới hạn bởi sức nhận của trạm dự phòng."""
    params = db.params_by_station()
    latest = db.station_latest()
    p, row = params[station], latest[station]
    target = p["close_pct"] / 100 * row["rated_load"]
    need = max(row["s_h_sim"] - target, row["s_h_sim"] + max(row["speed_pct_h"] or 0, 0) / 100 * row["rated_load"] - target, 0)
    amount = math.ceil(need * 10) / 10
    neighbor = p["neighbor_station_id"]
    if not neighbor:
        return amount, None
    n_p, n_row = params[neighbor], latest[neighbor]
    capacity = n_p["yellow_pct"] / 100 * n_row["rated_load"] - n_row["s_h_sim"]
    return max(min(amount, math.floor(capacity * 100) / 100), 0.1), neighbor


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--red-plan", default="ontime")
    ap.add_argument("--ontime-min", type=float, default=15)
    ap.add_argument("--late-min", type=float, default=45)
    ap.add_argument("--yellow", choices=("follow", "ignore"), default="follow")
    ap.add_argument("--yellow-min", type=float, default=15)
    ap.add_argument("--minutes", type=float, default=60)
    args = ap.parse_args()
    plan = [x.strip() for x in args.red_plan.split(",") if x.strip()]
    assigned: dict[int, str] = {}
    done: set[int] = set()
    stop_at = time.monotonic() + args.minutes * 60
    say(f"Bắt đầu: kế hoạch Đỏ = {plan}, Vàng = {args.yellow}")
    while time.monotonic() < stop_at:
        for a in db.open_queue():
            aid, wait = a["alert_id"], a["data_wait_min"] or 0
            if aid in done:
                continue
            if a["severity"] == "YELLOW" and a["status"] == "OPEN" and args.yellow == "follow" and wait >= args.yellow_min:
                if db.follow(aid, OPERATOR):
                    say(f"Theo dõi Vàng #{aid} ({a['station_id']}) sau {wait:.0f} phút dữ liệu")
                done.add(aid)
            elif a["severity"] == "RED" and a["action_ts"] is None:
                policy = assigned.setdefault(aid, plan[len(assigned) % len(plan)])
                if policy == "ignore":
                    if aid not in done:
                        say(f"Đỏ #{aid} ({a['station_id']}): kế hoạch IGNORE – không xử lý")
                        done.add(aid)
                    continue
                due = args.late_min if policy == "late" else args.ontime_min
                if wait < due:
                    continue
                if policy == "false":
                    ok = db.mark_false_alarm(aid, OPERATOR)
                    say(f"Báo giả Đỏ #{aid} sau {wait:.0f} phút dữ liệu: {'OK' if ok else 'không ghi được'}")
                else:
                    amount, target = suggestion(a["station_id"])
                    ok = target is not None and db.transfer_load(aid, amount, target, OPERATOR)
                    say(f"Chuyển tải Đỏ #{aid} ({policy}) {amount:.2f} → {target} sau {wait:.0f} phút dữ liệu: "
                        f"{'OK' if ok else 'không ghi được'}")
                done.add(aid)
        time.sleep(2)
    say("Hết thời gian giả lập.")


if __name__ == "__main__":
    main()
