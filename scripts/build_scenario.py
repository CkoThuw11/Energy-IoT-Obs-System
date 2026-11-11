"""Dựng lại data/scenario_data.csv và data/event_label.csv (sped.md §1) – KHÔNG bơm lỗi tự tạo.

- Kỳ kiểm thử: 8 tháng cuối, 2017-11-01 00:00 → 2018-06-26 19:45 (15 phút / bản ghi).
- TRAM_Y = ETTm1, TRAM_2 = ETTm2, TRAM_3 = ETTm1 dời 7 ngày, TRAM_4 = ETTm2 dời 14 ngày
  (giá trị tại ts lấy từ nguồn tại ts − dời). Dòng trùng giá trị (≤ 1e-9) với file cũ giữ nguyên chuỗi cũ; dòng khác lấy chuỗi gốc ETT.
- event_label.csv: bỏ các sự cố bơm (source_type = SIMULATED), giữ mã EV gốc → 9 quá tải + 20 cảm biến.

Chạy: python scripts/build_scenario.py   (ghi đè 2 file trong data/)
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
START, END = datetime(2017, 11, 1), datetime(2018, 6, 26, 19, 45)
STATIONS = {"TRAM_2": ("ETTm2", 0), "TRAM_3": ("ETTm1", 7), "TRAM_4": ("ETTm2", 14), "TRAM_Y": ("ETTm1", 0)}
COLUMNS = ["HUFL", "HULL", "MUFL", "MULL", "LUFL", "LULL", "OT"]


def previous() -> dict[tuple[str, str], list[str]]:
    """Chuỗi số của file kịch bản hiện có (nếu có) để giữ nguyên dòng không đổi → diff gọn."""
    path = DATA / "scenario_data.csv"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8", newline="") as f:
        return {(r["ts"], r["station_id"]): [r[c] for c in COLUMNS] for r in csv.DictReader(f)}


def same(old: list[str], src: list[str]) -> bool:
    return all(abs(float(a) - float(b)) <= 1e-9 for a, b in zip(old, src))


def load(name: str) -> dict[datetime, dict]:
    with open(DATA / f"{name}.csv", encoding="utf-8", newline="") as f:
        return {datetime.fromisoformat(r["date"]): r for r in csv.DictReader(f)}


def build_scenario() -> int:
    sources = {name: load(name) for name in {s for s, _ in STATIONS.values()}}
    old = previous()
    rows = []
    ts = START
    while ts <= END:
        for station in sorted(STATIONS):
            src, shift = STATIONS[station]
            row = sources[src].get(ts - timedelta(days=shift))
            if row is None:
                raise SystemExit(f"Thiếu dữ liệu nguồn {src} cho {station} tại {ts}")
            stamp = ts.strftime("%Y-%m-%d %H:%M:%S")
            values = [row[c] for c in COLUMNS]
            kept = old.get((stamp, station))
            rows.append([stamp, station, *(kept if kept and same(kept, values) else values)])
        ts += timedelta(minutes=15)
    with open(DATA / "scenario_data.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["ts", "station_id", *COLUMNS])
        writer.writerows(rows)
    return len(rows)


def build_labels() -> tuple[int, int]:
    path = DATA / "event_label.csv"
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        kept = [r for r in reader if r["source_type"] != "SIMULATED"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(kept)
    overload = sum(r["type"] == "OVERLOAD" for r in kept)
    return overload, len(kept) - overload


if __name__ == "__main__":
    n = build_scenario()
    ov, se = build_labels()
    print(f"scenario_data.csv: {n} dòng · event_label.csv: {ov} quá tải + {se} cảm biến = {ov + se}")
