"""Màu, định dạng, khung trang và sidebar dùng chung."""

from __future__ import annotations

import html
import os
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import streamlit as st

# Màu trạng thái (nền ô / chip / sọc) – thống nhất với cảnh báo đang mở
COLORS = {"RED": "#E02F44", "YELLOW": "#FADE2A", "SENSOR": "#8E8E8E", "NORMAL": "#56A64B"}
TEXT_ON = {"RED": "#FFFFFF", "YELLOW": "#1F1F1F", "SENSOR": "#FFFFFF", "NORMAL": "#FFFFFF"}
LEVEL_LABEL = {"RED": "Đỏ", "YELLOW": "Vàng", "SENSOR": "Cảm biến", "NORMAL": "Bình thường"}
# Màu chữ cho SỐ LIỆU / XU HƯỚNG: tông đậm, tương phản ≥ 4.5:1 trên nền trắng
INK = {"RED": "#C8102E", "AMBER": "#9A6700", "ORANGE": "#C2410C", "GREEN": "#15803D",
       "GRAY": "#111827", "BLUE": "#1D4ED8"}   # GRAY: chữ trung tính = đen
NAVY = "#14213D"
OPERATOR = "Người trực A"                       # một người trực duy nhất
SIM_TAG = "mô phỏng"                            # nhãn cho số liệu sau chuyển tải
DISPLAY_TZ = ZoneInfo(os.getenv("DISPLAY_TZ", "Asia/Ho_Chi_Minh"))
RED_DEADLINE_MIN = 30     # Đỏ: chuyển tải trong 30 phút DỮ LIỆU kể từ lúc lên Đỏ (sped.md §4)
YELLOW_DEADLINE_MIN = 60  # Vàng: bấm "Theo dõi" trong 60 phút dữ liệu kể từ lúc mở
DEMO_SPEED = 20           # tốc độ phát lại demo: 15 phút dữ liệu = 45 giây thật

CSS = f"""
<style>
.block-container {{padding-top: 2.6rem; max-width: 1400px;}}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] * {{color: #111827 !important;}}
[data-testid="stToolbar"] {{display: none;}}
h1, h2, h3 {{color: {NAVY};}}
.topbar {{background: {NAVY}; color: #fff; border-radius: 10px; padding: 14px 20px; margin-bottom: 14px;
          display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap;}}
.topbar .title {{font-size: 1.35rem; font-weight: 700; letter-spacing: .2px;}}
.topbar .meta {{font-size: .85rem;}}
.topbar .pill {{display: inline-block; border-radius: 999px; padding: 3px 12px; margin-left: 6px;
                font-weight: 700; font-size: .85rem;}}
.section {{display: flex; align-items: center; gap: 8px; margin: 18px 0 8px; font-size: .8rem; font-weight: 700;
           letter-spacing: .08em; text-transform: uppercase; color: {NAVY};}}
.section::before {{content: ""; width: 4px; height: 16px; border-radius: 2px; background: #1D4ED8;}}
.section::after {{content: ""; flex: 1; height: 1px; background: #E3E8EF;}}
.chip {{border-radius: 4px; padding: 2px 8px; font-weight: 700; font-size: .8rem; white-space: nowrap;}}
@keyframes blink {{50% {{opacity: 0.25;}}}}
.blink {{animation: blink 1s step-start infinite;}}
.card {{border-radius: 10px; padding: 12px 14px; margin-bottom: 6px; border: 1px solid #E3E8EF;
        border-left-width: 6px; background: #fff; box-shadow: 0 1px 2px rgba(16,24,40,.06);}}
.card .name {{font-size: .95rem; font-weight: 700; color: {NAVY}; display: flex; justify-content: space-between;
              gap: 6px;}}
.card .big {{font-size: 2.1rem; font-weight: 800; line-height: 1.15; font-variant-numeric: tabular-nums;}}
.card .trend {{font-size: .95rem; font-weight: 700; margin-left: 8px;}}
.card .sub {{font-size: .88rem; font-weight: 700;}}
.mcard {{border-radius: 10px; padding: 10px 14px; border: 1px solid #E3E8EF; background: #fff; height: 100%;
         box-shadow: 0 1px 2px rgba(16,24,40,.06);}}
.mcard .label {{font-size: .78rem; font-weight: 700; color: #111827; text-transform: uppercase; letter-spacing: .04em;}}
.mcard .value {{font-size: 1.6rem; font-weight: 800; line-height: 1.25; font-variant-numeric: tabular-nums;}}
.mcard .note {{font-size: .8rem; color: #111827;}}
.bar {{position: relative; height: 8px; border-radius: 4px; background: #E5E7EB; margin: 8px 0 6px;}}
.bar .fill {{height: 100%; border-radius: 4px;}}
.bar .tick {{position: absolute; top: -3px; width: 2px; height: 14px;}}
.step {{border-radius: 8px; padding: 8px; text-align: center; font-size: .9rem; border: 1px solid #D1D5DB;
        color: #111827; background: #F9FAFB;}}
.step.done {{font-weight: 700; background: #DCFCE7; border-color: #16A34A; color: #15803D;}}
.step .when {{font-size: .78rem; font-weight: 600; margin-top: 2px;}}
.step.now {{border: 2px solid #E02F44; font-weight: 800; background: #FDECEE; color: #C8102E;}}
.tbl {{width: 100%; border-collapse: separate; border-spacing: 0; font-size: .88rem; border: 1px solid #E3E8EF;
        border-radius: 10px; overflow: hidden;}}
.tbl th {{background: {NAVY}; color: #fff; text-align: left; padding: 8px 10px; font-weight: 700;
          font-size: .78rem; text-transform: uppercase; letter-spacing: .04em;}}
.tbl td {{padding: 7px 10px; border-top: 1px solid #EEF1F5; color: #1F2937; font-variant-numeric: tabular-nums;}}
.tbl tr:nth-child(even) td {{background: #F7F9FC;}}
.tbl td.num {{text-align: right;}}
.qhead {{background: {NAVY}; color: #fff; border-radius: 8px; padding: 8px 4px; font-size: .78rem; font-weight: 700;
         text-transform: uppercase; letter-spacing: .04em;}}
[data-testid="stSidebarNavLink"] {{padding: 10px 12px; margin-bottom: 4px; border-radius: 8px;}}
[data-testid="stSidebarNavLink"] span {{font-size: 1.15rem !important; font-weight: 700; color: {NAVY};}}
[data-testid="stSidebarNavLink"][aria-current="page"] {{background: #DCE4F2;}}
.legend ul {{list-style: none; padding-left: 0; margin: 4px 0 12px;}}
.legend li {{margin: 6px 0; font-size: .85rem; color: #111827; line-height: 1.45; display: flex;
             gap: 6px; align-items: baseline;}}
.legend li .chip {{flex: none;}}
.legend .lg-title {{font-size: .75rem; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: #111827;}}
.legend .dot {{display: inline-block; flex: none; width: 10px; height: 10px; border-radius: 50%;}}
[class*="st-key-red_"] button {{background: #E02F44; border-color: #E02F44; color: #fff; font-weight: 700;}}
[class*="st-key-red_"] button:hover {{background: #C8102E; border-color: #C8102E; color: #fff;}}
</style>
"""


def setup_page() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def operator() -> str:
    return OPERATOR


# ---------------------------------------------------------------------------
# Khung trang
# ---------------------------------------------------------------------------
def topbar(title: str, data_ts: Any = None, counts: dict[str, int] | None = None) -> None:
    """Thanh tiêu đề tối: tên trang; tùy chọn giờ dữ liệu và số cảnh báo đang mở theo mức."""
    pills = ""
    for level, n in (counts or {}).items():
        bg, fg = (COLORS[level], TEXT_ON[level]) if n else ("#2B3A5C", "#C7D2E5")
        pills += f'<span class="pill" style="background:{bg};color:{fg}">{LEVEL_LABEL[level]} {n}</span>'
    meta = f'<div class="meta">Giờ dữ liệu: <b>{data_ts}</b></div>' if data_ts else ""
    st.markdown(f'<div class="topbar"><div><div class="title">⚡ {title}</div>{meta}</div>'
                f'<div>{pills}</div></div>', unsafe_allow_html=True)


def section(title: str) -> None:
    st.markdown(f'<div class="section">{title}</div>', unsafe_allow_html=True)


def html_table(rows: list[dict], columns: list[tuple[str, str, Callable[[Any, dict], str] | None]],
               stripe: Callable[[dict], str] | None = None) -> str:
    """Bảng HTML: tiêu đề nền tối, dòng xen màu; cột = (khóa, tiêu đề, hàm định dạng → html)."""
    head = "".join(f"<th>{html.escape(title)}</th>" for _, title, _ in columns)
    body = []
    for row in rows:
        cells = []
        for index, (key, _, fmt) in enumerate(columns):
            value = row.get(key)
            text = fmt(value, row) if fmt else ("—" if value is None else html.escape(str(value)))
            style = f' style="border-left:5px solid {stripe(row)}"' if stripe and index == 0 else ""
            cls = ' class="num"' if isinstance(value, (int, float)) and not isinstance(value, bool) else ""
            cells.append(f"<td{cls}{style}>{text}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f'<table class="tbl"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def legend_html(p: dict) -> str:
    """Chú thích: mỗi màu đi kèm ý nghĩa của nó, dạng liệt kê."""
    status = [
        ("RED", f"tải ≥ {p['red_pct']}% hoặc còn ≤ {p['red_ttb_min']} phút đến {p['red_pct']}%"),
        ("YELLOW", f"tải ≥ {p['yellow_pct']}% hoặc còn ≤ {p['yellow_ttb_min']} phút"),
        ("SENSOR", f"số liệu đứng yên ≥ {p['stuck_records']} bản ghi"),
        ("NORMAL", "không có cảnh báo đang mở"),
    ]
    numbers = [
        (INK["GREEN"], f"&lt; {p['close_pct']}%"),
        (INK["ORANGE"], f"{p['close_pct']}–{p['yellow_pct']}%"),
        (INK["AMBER"], f"{p['yellow_pct']}–{p['red_pct']}%"),
        (INK["RED"], f"≥ {p['red_pct']}%"),
    ]
    rows = "".join(f'<li>{chip(level)}<span>{text}</span></li>' for level, text in status)
    nums = "".join(f'<li><span class="dot" style="background:{c}"></span>{span(t, c)}</li>' for c, t in numbers)
    return (f'<div class="legend"><div class="lg-title">Mức cảnh báo (màu ô / nhãn)</div><ul>{rows}</ul>'
            f'<div class="lg-title">Con số % tải</div><ul>{nums}</ul>'
            f'<div class="lg-title">Mô phỏng</div><ul><li><span class="dot" style="background:{INK["BLUE"]}"></span>'
            f'<span>{span("Xanh dương", INK["BLUE"])} = tải sau chuyển tải (mô phỏng); số đo gốc giữ nguyên</span></li>'
            f'</ul></div>')


def sidebar(p: dict | None = None) -> None:
    """Sidebar: chỉ có điều hướng (Streamlit) và mục Chú thích."""
    if p is None:
        return
    with st.sidebar:
        st.markdown("#### Chú thích")
        st.markdown(legend_html(p), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Định dạng
# ---------------------------------------------------------------------------
def chip(level: str, text: str | None = None, blink: bool = False) -> str:
    cls = "chip blink" if blink else "chip"
    return (f'<span class="{cls}" style="background:{COLORS[level]};color:{TEXT_ON[level]}">'
            f'{text or LEVEL_LABEL[level]}</span>')


def fmt_minutes(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = max(int(seconds), 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


def fmt_clock(value: datetime | None) -> str:
    return value.astimezone(DISPLAY_TZ).strftime("%H:%M:%S") if value else "—"


def fmt_ts(value: Any) -> str:
    return "—" if value is None else str(value)[:16]


def trend_arrow(speed: float | None) -> str:
    if speed is None:
        return "→"
    return "↑" if speed > 0 else "↓" if speed < 0 else "→"


# ---------------------------------------------------------------------------
# Màu theo số liệu (ngưỡng lấy từ param_threshold)
# ---------------------------------------------------------------------------
def load_ink(load: float | None, p: dict) -> str:
    """% tải: ≥ Đỏ → đỏ, ≥ Vàng → vàng đậm, ≥ ngưỡng đóng → cam (cảnh giác), còn lại xanh."""
    if load is None:
        return INK["GRAY"]
    if load >= p["red_pct"]:
        return INK["RED"]
    if load >= p["yellow_pct"]:
        return INK["AMBER"]
    if load >= p["close_pct"]:
        return INK["ORANGE"]
    return INK["GREEN"]


def load_fill(load: float | None, p: dict) -> str:
    """Màu thanh tải (nền đặc, dùng màu trạng thái sáng hơn)."""
    if load is None:
        return "#9CA3AF"
    if load >= p["red_pct"]:
        return COLORS["RED"]
    if load >= p["yellow_pct"]:
        return "#F2C200"
    return "#F59E0B" if load >= p["close_pct"] else COLORS["NORMAL"]


def trend_ink(speed: float | None) -> str:
    if speed is None or speed == 0:
        return INK["GRAY"]
    return INK["RED"] if speed > 0 else INK["GREEN"]


def ttb_ink(ttb: float | None, p: dict) -> str:
    if ttb is None:
        return INK["GRAY"]
    if ttb <= p["red_ttb_min"]:
        return INK["RED"]
    return INK["AMBER"] if ttb <= p["yellow_ttb_min"] else INK["GRAY"]


def span(text: str, color: str, bold: bool = True) -> str:
    weight = "font-weight:700;" if bold else ""
    return f'<span style="color:{color};{weight}">{text}</span>'


def trend_html(speed: float | None) -> str:
    text = f"{trend_arrow(speed)} {speed:+.1f}%/h" if speed is not None else "→ —"
    return span(text, trend_ink(speed))


def load_bar(load: float | None, p: dict, scale_max: float = 120.0) -> str:
    """Thanh % tải, có vạch ngưỡng Vàng và Đỏ."""
    width = 0 if load is None else max(0.0, min(load, scale_max)) / scale_max * 100
    ticks = "".join(
        f'<div class="tick" style="left:{p[k] / scale_max * 100:.1f}%;background:{c}"></div>'
        for k, c in (("yellow_pct", "#B58900"), ("red_pct", COLORS["RED"])))
    return (f'<div class="bar"><div class="fill" style="width:{width:.1f}%;background:{load_fill(load, p)}"></div>'
            f'{ticks}</div>')


def metric_card(label: str, value: str, color: str, note: str = "") -> str:
    note_html = f'<div class="note">{note}</div>' if note else ""
    return (f'<div class="mcard" style="border-top:4px solid {color}"><div class="label">{label}</div>'
            f'<div class="value" style="color:{color}">{value}</div>{note_html}</div>')


def elapsed_ink(minutes: float | None, deadline: float) -> str:
    """Đồng hồ xử lý theo giờ dữ liệu: < 2/3 thời hạn xanh, đến thời hạn vàng, quá hạn đỏ."""
    if minutes is None:
        return INK["GRAY"]
    if minutes > deadline:
        return INK["RED"]
    return INK["AMBER"] if minutes > deadline * 2 / 3 else INK["GREEN"]


def fmt_data_min(minutes: float | None) -> str:
    """Số phút DỮ LIỆU (bội số 15) kèm quy đổi giờ thật ở tốc độ demo."""
    if minutes is None:
        return "—"
    real = minutes * 60 / DEMO_SPEED
    return f"{minutes:.0f} phút (≈ {fmt_minutes(real)} thật ở ×{DEMO_SPEED})"


def open_station(station: str) -> None:
    """Chuyển sang trang Chi tiết trạm."""
    st.session_state["station"] = station
    st.switch_page(st.session_state["pages"]["station"])


def shown_load(row: dict) -> tuple[float, bool]:
    """% tải dùng để vận hành: tải sau chuyển tải (mô phỏng) nếu có, ngược lại số đo."""
    simulated = bool(row["transfer_out"] or row["transfer_in"])
    return (row["load_pct_sim"] if simulated else row["load_pct"]), simulated
