"""Trang 2 – Chi tiết trạm.

Một layout duy nhất. Khi trạm có cảnh báo Đỏ đang mở, khung "Xử lý sự cố" (3 bước) được chèn lên
trên cùng: ① Xác nhận → ② Chuyển tải sang trạm lân cận → ③ Theo dõi. Dưới mỗi bước đã xong ghi giờ thật.
Sau khi cảnh báo tự đóng, thẻ "Kết quả xử lý" của cảnh báo Đỏ gần nhất vẫn được giữ.
Tải sau chuyển tải là MÔ PHỎNG (v_load_sim): trạm nguồn − X, trạm nhận + X; số đo gốc không đổi.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import altair as alt
import pandas as pd
import streamlit as st

import common
import db

STEPS = ["① Xác nhận", "② Chuyển tải", "③ Theo dõi"]
MEASURED, SIMULATED = "Tải đo", "Tải sau chuyển tải (mô phỏng)"
LINE_COLORS = {MEASURED: "#111827", SIMULATED: "#1D4ED8"}
CHART_HEIGHT = 460
INK_BLACK = "#111827"
CHART_HOURS = 24


def render() -> None:
    params = db.params_by_station()
    names = {sid: p["station_name"] for sid, p in sorted(params.items())}
    wanted = st.query_params.get("tram")            # liên kết trực tiếp: ?tram=TRAM_Y
    if wanted in names and "station_from_url" not in st.session_state:
        st.session_state["station"] = wanted
        st.session_state["station_from_url"] = True
    if st.session_state.get("station") not in names:
        st.session_state["station"] = next(iter(names))
    # key "station" cũng được Trang 1 đặt trước khi chuyển trang (bấm ô trạm / hàng đợi)
    col, _ = st.columns([1, 3])
    station = col.selectbox("Trạm", list(names), format_func=names.get, key="station")
    station_block(station)


@st.fragment(run_every=5)
def station_block(station: str) -> None:
    params = db.params_by_station()
    latest_all = db.station_latest()
    latest = latest_all.get(station)
    if latest is None:
        st.info("Chưa có dữ liệu của trạm này.")
        return
    p = params[station]
    neighbor = latest_all.get(p["neighbor_station_id"]) if p["neighbor_station_id"] else None
    n_params = params.get(p["neighbor_station_id"]) if neighbor else None
    common.topbar(f"Chi tiết trạm · {latest['station_name']}")
    red = db.open_alert(station, "OVERLOAD")
    if red and red["severity"] == "RED":
        common.section("Xử lý sự cố Đỏ")
        incident_panel(red, latest, p, neighbor, n_params)
    else:
        last = db.last_red_result(station)
        if last:
            common.section("Kết quả xử lý sự cố Đỏ gần nhất")
            result_panel(last)
    common.section("Số liệu hiện tại")
    station_metrics(latest, p, station)
    common.section(f"Diễn biến tải {CHART_HOURS} giờ gần nhất")
    load_chart_block(station, latest["ts"], p)
    common.section("Trạm dự phòng")
    left, _ = st.columns([1, 1])
    with left:
        neighbor_panel(neighbor, n_params)
    common.section("Chuyển tải · lịch sử cảnh báo 7 ngày dữ liệu")
    transfer_table(station)
    history_table(station, latest["ts"] - timedelta(days=7))


# ---------------------------------------------------------------------------
# Khối chung
# ---------------------------------------------------------------------------
def ttb_at(station: str, ts: datetime, red_pct: float) -> float | None:
    """TTB tại một mốc, cùng công thức với detector: (Đỏ − tải sau chuyển tải) / tốc độ của TẢI ĐO."""
    now, before = db.loads_at(station, ts), db.loads_at(station, ts - timedelta(hours=1))
    if now is None or before is None:
        return None
    speed = now["load_pct"] - before["load_pct"]
    if speed <= 0 or now["load_pct_sim"] >= red_pct:
        return None
    return (red_pct - now["load_pct_sim"]) / speed * 60


def headroom_ink(headroom: float, rated: float) -> str:
    if headroom < 0:
        return common.INK["RED"]
    return common.INK["AMBER"] if headroom < 0.05 * rated else common.INK["GREEN"]


def station_metrics(latest: dict, p: dict, station: str) -> None:
    rated = latest["rated_load"]
    load, simulated = common.shown_load(latest)
    s_h = latest["s_h_sim"] if simulated else latest["s_h"]
    stuck = db.open_alert(station, "SENSOR") is not None
    speed = latest["speed_pct_h"]
    ttb = ttb_at(station, latest["ts"], p["red_pct"])
    headroom = p["yellow_pct"] / 100 * rated - s_h
    measured = common.span(f"{latest['load_pct']:.1f}%", common.load_ink(latest["load_pct"], p))
    load_note = (f"{common.SIM_TAG} · tải đo {measured}" if simulated else f"S_H {s_h:.2f} / định mức {rated:.2f}")
    trend_value = f"{common.trend_arrow(speed)} {speed:+.1f} %/giờ" if speed is not None else "—"
    ttb_note = common.span(f"Còn {ttb:.0f} phút đến {p['red_pct']}%", common.ttb_ink(ttb, p)) if ttb is not None else ""
    cards = [
        common.metric_card("Tải", f"{load:.1f}%", common.load_ink(load, p), load_note + common.load_bar(load, p)),
        common.metric_card("Xu hướng 1 giờ", trend_value, common.trend_ink(speed), ttb_note),
        common.metric_card(f"Còn dư đến {p['yellow_pct']}%", f"{headroom:.2f}", headroom_ink(headroom, rated),
                           "Đã vượt ngưỡng Vàng" if headroom < 0 else "Đơn vị S_H"),
        common.metric_card("Cảm biến", "Kẹt" if stuck else "OK",
                           common.INK["GRAY"] if stuck else common.INK["GREEN"],
                           "Không dùng số liệu để kết luận quá tải" if stuck else "Số liệu đang thay đổi bình thường"),
    ]
    for col, card in zip(st.columns(4), cards):
        col.markdown(card, unsafe_allow_html=True)


def threshold_bands(p: dict, low: float, top: float, scale: alt.Scale) -> alt.Chart:
    """Dải nền: dưới ngưỡng Vàng xanh nhạt, Vàng–Đỏ vàng nhạt, trên Đỏ đỏ nhạt."""
    bands = pd.DataFrame([
        {"y0": low, "y1": p["yellow_pct"], "c": common.COLORS["NORMAL"]},
        {"y0": p["yellow_pct"], "y1": p["red_pct"], "c": common.COLORS["YELLOW"]},
        {"y0": p["red_pct"], "y1": top, "c": common.COLORS["RED"]},
    ])
    return alt.Chart(bands).mark_rect(opacity=0.06, clip=True).encode(
        y=alt.Y("y0:Q", scale=scale), y2="y1:Q", color=alt.Color("c:N", scale=None, legend=None))


def level_name(load: float, p: dict) -> str:
    if load >= p["red_pct"]:
        return "≥ Đỏ"
    if load >= p["yellow_pct"]:
        return "≥ Vàng"
    return "≥ ngưỡng đóng" if load >= p["close_pct"] else "Bình thường"


def transfer_rules(station: str, start: datetime) -> list[alt.Chart]:
    """Vạch dọc lúc chuyển tải / trả tải."""
    marks = []
    for x in db.transfers(station):
        sign = "−" if x["source_station"] == station else "+"
        marks.append({"ts": x["effective_from_ts"], "label": f"Chuyển {sign}{x['amount']:.2f}"})
        if x["effective_to_ts"]:
            marks.append({"ts": x["effective_to_ts"], "label": "Trả tải"})
    marks = [m for m in marks if m["ts"] >= start]
    if not marks:
        return []
    df = pd.DataFrame(marks)
    rule = alt.Chart(df).mark_rule(color="#1D4ED8", strokeWidth=2).encode(x="ts:T")
    text = alt.Chart(df).mark_text(align="left", dx=5, dy=-4, color="#1D4ED8", fontWeight="bold", fontSize=13).encode(
        x="ts:T", y=alt.value(12), text="label:N")
    return [rule, text]


def load_chart(df: pd.DataFrame, p: dict, station: str, lines: dict[str, tuple[float, str]]) -> alt.Chart:
    simulated = bool((df["load_pct_sim"] - df["load_pct"]).abs().max() > 1e-9)
    series = [MEASURED, SIMULATED] if simulated else [MEASURED]
    long = df.melt(id_vars=["ts"], value_vars=["load_pct", "load_pct_sim"] if simulated else ["load_pct"],
                   var_name="kind", value_name="value")
    long["Đường"] = long["kind"].map({"load_pct": MEASURED, "load_pct_sim": SIMULATED})
    values = long["value"]
    low = max(0.0, min(float(values.min()), p["close_pct"]) - 5)
    top = max(float(values.max()) + 5, p["red_pct"] + 5)
    scale = alt.Scale(domain=[low, top], nice=False)
    y = alt.Y("value:Q", title="% tải", scale=scale)
    x = alt.X("ts:T", title=None, axis=alt.Axis(format="%d/%m %H:%M", labelAngle=0, tickCount=8))
    # Đường giá trị nổi bật: dày, màu đậm. Khi có chuyển tải, tải đo = nét đứt đen mảnh hơn,
    # tải sau chuyển tải = nét liền xanh dương dày; khi không chuyển, tải đo là nét liền đen dày.
    dash = [[6, 4], [1, 0]] if simulated else [[1, 0]]
    width = [2.2, 4] if simulated else [3.5]
    line = alt.Chart(long).mark_line(clip=True, interpolate="monotone").encode(
        x=x, y=y,
        color=alt.Color("Đường:N", scale=alt.Scale(domain=series, range=[LINE_COLORS[s] for s in series]),
                        legend=alt.Legend(orient="top", title=None, labelLimit=400, symbolStrokeWidth=4)),
        strokeDash=alt.StrokeDash("Đường:N", scale=alt.Scale(domain=series, range=dash), legend=None),
        strokeWidth=alt.StrokeWidth("Đường:N", scale=alt.Scale(domain=series, range=width), legend=None))
    operational = df.assign(value=df["load_pct_sim"], Mức=[level_name(v, p) for v in df["load_pct_sim"]])
    point_scale = alt.Scale(domain=["Bình thường", "≥ ngưỡng đóng", "≥ Vàng", "≥ Đỏ"],
                            range=[common.INK["GREEN"], common.INK["ORANGE"], common.INK["AMBER"], common.INK["RED"]])
    points = alt.Chart(operational).mark_point(filled=True, size=70, opacity=1, stroke="white", strokeWidth=1.5,
                                               clip=True).encode(
        x=x, y=alt.Y("value:Q", scale=scale), color=alt.Color("Mức:N", scale=point_scale, legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("ts:T", title="Mốc"), alt.Tooltip("load_pct:Q", title="Tải đo %", format=".1f"),
                 alt.Tooltip("load_pct_sim:Q", title="Sau chuyển tải %", format=".1f")])
    # Vạch ngưỡng mảnh, mờ, có nhãn ở mép phải → không lẫn với đường giá trị
    rule_df = pd.DataFrame([{"y": value, "label": f"{value:g}%", "c": color, "ts": df["ts"].max()}
                            for value, color in lines.values()])
    rules = alt.Chart(rule_df).mark_rule(strokeDash=[3, 4], strokeWidth=1, opacity=0.7, clip=True).encode(
        y=alt.Y("y:Q", scale=scale), color=alt.Color("c:N", scale=None, legend=None))
    rule_labels = alt.Chart(rule_df).mark_text(align="right", dx=-4, dy=-7, fontSize=12, fontWeight="bold").encode(
        x="ts:T", y=alt.Y("y:Q", scale=scale), text="label:N", color=alt.Color("c:N", scale=None, legend=None))
    layers = [threshold_bands(p, low, top, scale), rules, line, points, *transfer_rules(station, df["ts"].min())]
    return (alt.layer(*layers, rule_labels).resolve_scale(color="independent").properties(height=CHART_HEIGHT)
            .configure_axis(labelFontSize=13, titleFontSize=13, labelColor=INK_BLACK, titleColor=INK_BLACK,
                            gridColor="#E5E7EB", domainColor="#9CA3AF", tickColor="#9CA3AF")
            .configure_legend(labelFontSize=13, labelColor=INK_BLACK, symbolSize=160)
            .configure_view(strokeWidth=0))


def load_chart_block(station: str, newest: datetime, p: dict) -> None:
    df = db.load_series(station, newest - timedelta(hours=CHART_HOURS), newest)
    if df.empty:
        return
    lines = {"close": (p["close_pct"], common.INK["GREEN"]),
             "yellow": (p["yellow_pct"], common.INK["AMBER"]),
             "red": (p["red_pct"], common.INK["RED"])}
    st.altair_chart(load_chart(df, p, station, lines), width="stretch", theme=None)
    st.markdown(
        f"- **Nét liền đen:** tải đo (dữ liệu thật); khi đang chuyển tải → **nét đứt đen**\n"
        f"- **Nét liền xanh dương (dày):** tải sau chuyển tải (mô phỏng)\n"
        f"- **Điểm màu:** mức tải dùng để vận hành\n"
        f"- **Vạch ngang:** {p['close_pct']}% / {p['yellow_pct']}% / {p['red_pct']}%\n"
        f"- **Vạch dọc:** lúc chuyển / trả tải\n"
        f"- **Trục ngang:** giờ dữ liệu")


def neighbor_panel(neighbor: dict | None, n_params: dict | None) -> None:
    if neighbor is None:
        st.caption("Không có trạm lân cận.")
        return
    load, simulated = common.shown_load(neighbor)
    s_h = neighbor["s_h_sim"] if simulated else neighbor["s_h"]
    capacity = n_params["yellow_pct"] / 100 * neighbor["rated_load"] - s_h
    note = (f"{common.SIM_TAG} · đo {neighbor['load_pct']:.1f}% · " if simulated else "") + common.trend_html(
        neighbor["speed_pct_h"])
    a, b = st.columns(2)
    a.markdown(common.metric_card(neighbor["station_name"], f"{load:.1f}%", common.load_ink(load, n_params),
                                  note + common.load_bar(load, n_params)), unsafe_allow_html=True)
    b.markdown(common.metric_card("Nhận thêm tối đa", f"{capacity:.2f}",
                                  common.INK["GREEN"] if capacity > 0 else common.INK["RED"],
                                  f"= {n_params['yellow_pct']}% × định mức − S_H hiện tại"), unsafe_allow_html=True)


TRANSFER_COLUMNS = [
    ("alert_id", "Cảnh báo", lambda v, r: f"<b>#{v}</b>"),
    ("source_station", "Hướng", lambda v, r: f"{v} → {r['target_station']}"),
    ("amount", "Lượng", lambda v, r: f"{v:.2f}"),
    ("effective_from_ts", "Từ mốc", lambda v, r: common.fmt_ts(v)),
    ("effective_to_ts", "Trả tải", lambda v, r: common.fmt_ts(v) if v else common.span("đang hiệu lực", common.INK["BLUE"])),
    ("done_by", "Người thực hiện", None),
]
HISTORY_COLUMNS = [
    ("alert_id", "#", lambda v, r: f"<b>{v}</b>"),
    ("alert_type", "Loại", lambda v, r: "Quá tải" if v == "OVERLOAD" else "Cảm biến"),
    ("severity", "Mức", lambda v, r: common.chip(v)),
    ("status", "Trạng thái", lambda v, r: {"OPEN": "Chưa nhận", "ACK": "Đã nhận", "ACTION_TAKEN": "Đã chuyển tải",
                                            "RESOLVED": "Đã đóng"}.get(v, v)),
    ("opened_ts", "Mở", lambda v, r: common.fmt_ts(v)),
    ("resolved_ts", "Đóng", lambda v, r: common.fmt_ts(v)),
    ("peak_load_pct", "Đỉnh tải", lambda v, r: f"{v:.1f}%" if v is not None else "—"),
    ("verdict", "Xác minh", lambda v, r: {"REAL": "Quá tải thật", "FALSE_ALARM": "Báo giả"}.get(v, "—")),
    ("root_cause", "Nguyên nhân", lambda v, r: v or "—"),
]


def transfer_table(station: str) -> None:
    items = db.transfers(station)
    if not items:
        st.caption("Chưa có lần chuyển tải nào.")
        return
    st.markdown(f"**Chuyển tải ({common.SIM_TAG})**")
    st.markdown(common.html_table(items, TRANSFER_COLUMNS,
                                  stripe=lambda r: "#9CA3AF" if r["effective_to_ts"] else common.INK["BLUE"]),
                unsafe_allow_html=True)


def history_table(station: str, since: datetime) -> None:
    history = db.alert_history(station, since)
    st.markdown("**Lịch sử cảnh báo**")
    if not history:
        st.caption("Không có cảnh báo trong 7 ngày dữ liệu.")
        return
    st.markdown(common.html_table(history, HISTORY_COLUMNS, stripe=lambda r: common.COLORS[r["severity"]]),
                unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Khung xử lý sự cố Đỏ (3 bước)
# ---------------------------------------------------------------------------
def current_step(alert: dict) -> int:
    if alert["verdict"] is None:
        return 0
    return 1 if alert["action_done_at"] is None else 2


def incident_panel(alert: dict, latest: dict, p: dict, neighbor: dict | None, n_params: dict | None) -> None:
    with st.container(border=True):
        incident_header(alert)
        step = current_step(alert)
        if step == 0:
            accept_step(alert)
        elif step == 1:
            transfer_step(alert, latest, p, neighbor, n_params)
        else:
            follow_step(alert, latest, p, neighbor, n_params)


def incident_header(alert: dict) -> None:
    elapsed = alert["elapsed_sec"] or 0
    badge = common.chip("RED", f"Cảnh báo Đỏ #{alert['alert_id']}")
    st.markdown(f"{badge} &nbsp; "
                f"<b>Quy trình 3 bước · mục tiêu chuyển tải trong {common.TARGET_MIN} phút</b>",
                unsafe_allow_html=True)
    cards = [
        common.metric_card("Mở lúc (giờ dữ liệu)", str(alert["opened_ts"])[11:16], common.INK["RED"],
                           str(alert["opened_ts"])[:10]),
        common.metric_card("Người nhận", alert["acked_by"] or "Chưa nhận",
                           common.INK["GREEN"] if alert["acked_by"] else common.INK["RED"]),
        common.metric_card("Đồng hồ (giờ thật)", f"{common.fmt_minutes(elapsed)} / {common.TARGET_MIN}:00",
                           common.elapsed_ink(elapsed),
                           "Dừng khi xác nhận chuyển tải" if alert["action_done_at"] is None else "Đã chuyển tải"),
    ]
    for col, card in zip(st.columns(3), cards):
        col.markdown(card, unsafe_allow_html=True)
    step_bar(alert, current_step(alert))


def step_times(alert: dict) -> list[datetime | None]:
    """Giờ thật hoàn thành từng bước: ① xác nhận, ② chuyển tải, ③ cảnh báo tự đóng."""
    return [alert["verified_at"], alert["action_done_at"], alert["resolved_at"]]


def step_bar(alert: dict, step: int) -> None:
    """Thanh 3 bước; dưới mỗi bước đã xong ghi giờ thật và thời gian kể từ lúc mở."""
    cells = []
    for index, (label, done_at) in enumerate(zip(STEPS, step_times(alert))):
        cls = "step done" if index < step else "step now" if index == step else "step"
        mark = "✓ " if index < step else ""
        when = ""
        if index < step and done_at:
            after = common.fmt_minutes((done_at - alert["opened_at"]).total_seconds())
            when = f'<div class="when">{common.fmt_clock(done_at)} · sau {after}</div>'
        cells.append(f'<div class="{cls}">{mark}{label}{when}</div>')
    st.markdown('<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin:8px 0">'
                + "".join(cells) + "</div>", unsafe_allow_html=True)


def result_panel(alert: dict) -> None:
    """Thẻ kết quả của cảnh báo Đỏ thật đã tự đóng: thời gian, lượng chuyển, trạng thái chuyển tải."""
    items = db.transfers(alert_id=alert["alert_id"])
    moved = sum(x["amount"] for x in items)
    returned = [x["effective_to_ts"] for x in items]
    active = any(t is None for t in returned)
    target_name = db.params_by_station()[items[0]["target_station"]]["station_name"] if items else ""
    act_sec = (alert["action_done_at"] - alert["opened_at"]).total_seconds() if alert["action_done_at"] else None
    with st.container(border=True):
        badge = common.chip("NORMAL", f"Cảnh báo Đỏ #{alert['alert_id']} · đã đóng")
        st.markdown(f"{badge} &nbsp; <b>Mở {common.fmt_ts(alert['opened_ts'])} → đóng "
                    f"{common.fmt_ts(alert['resolved_ts'])} (giờ dữ liệu) · đỉnh {alert['peak_load_pct']:.1f}%</b>",
                    unsafe_allow_html=True)
        step_bar(alert, len(STEPS))
        target = common.TARGET_MIN * 60
        cards = [
            common.metric_card("Chuyển tải sau (giờ thật)", common.fmt_minutes(act_sec),
                               common.INK["GREEN"] if act_sec is not None and act_sec <= target else common.INK["RED"],
                               f"Mục tiêu ≤ {common.TARGET_MIN}:00"),
            common.metric_card("Lượng đã chuyển", f"{moved:.2f}" if items else "—", INK_BLACK,
                               f"{len(items)} lần · sang {target_name}" if items else "Không chuyển tải"),
            common.metric_card("Chuyển tải", "Đang hiệu lực" if active else "Đã trả tải" if items else "—",
                               common.INK["BLUE"] if active else INK_BLACK,
                               "Trả khi tải đo < ngưỡng đóng 2 lần liên tiếp" if active
                               else f"Trả lúc {common.fmt_ts(max(returned))}" if items else ""),
        ]
        for col, card in zip(st.columns(3), cards):
            col.markdown(card, unsafe_allow_html=True)
        root_cause_input(alert)


def accept_step(alert: dict) -> None:
    st.markdown("Xem **Số liệu hiện tại** và **biểu đồ** bên dưới, rồi xác nhận quá tải thật hay báo giả.")
    a, b = st.columns(2)
    if a.button("Xác nhận – quá tải thật", type="primary", width="stretch"):
        db.accept_real(alert["alert_id"], common.operator())
        st.rerun()
    if b.button("Báo giả (đóng cảnh báo)", width="stretch"):
        db.mark_false_alarm(alert["alert_id"], common.operator())
        st.toast("Đã đóng cảnh báo (báo giả).")
        st.rerun()


def suggested_amount(latest: dict, p: dict) -> tuple[float, float, float]:
    """(cần giảm ngay, cần giảm theo dự báo 1 giờ, lượng gợi ý) – đơn vị S_H.

    Dự báo 1 giờ: tải tiếp tục tăng với tốc độ hiện tại (chỉ khi đang tăng).
    """
    rated = latest["rated_load"]
    s_h = latest["s_h_sim"]
    target = p["close_pct"] / 100 * rated
    need_now = s_h - target
    speed = latest["speed_pct_h"] or 0
    need_1h = s_h + max(speed, 0) / 100 * rated - target
    return need_now, need_1h, math.ceil(max(need_now, need_1h, 0) * 10) / 10


def transfer_preview(latest: dict, neighbor: dict, amount: float, p: dict, n_params: dict) -> str:
    y_after = (latest["s_h_sim"] - amount) / latest["rated_load"] * 100
    n_after = (neighbor["s_h_sim"] + amount) / neighbor["rated_load"] * 100
    return (f"Dự kiến ngay sau chuyển: {latest['station_name']} "
            f"{common.span(f'{y_after:.1f}%', common.load_ink(y_after, p))} · {neighbor['station_name']} "
            f"{common.span(f'{n_after:.1f}%', common.load_ink(n_after, n_params))}")


def transfer_step(alert: dict, latest: dict, p: dict, neighbor: dict | None, n_params: dict | None,
                  more: bool = False) -> None:
    if neighbor is None:
        st.warning("Trạm không có trạm lân cận để chuyển tải.")
        return
    need_now, need_1h, suggestion = suggested_amount(latest, p)
    capacity = n_params["yellow_pct"] / 100 * neighbor["rated_load"] - neighbor["s_h_sim"]
    if not more:
        cards = [
            common.metric_card(f"Cần giảm ngay (về < {p['close_pct']}%)", f"{max(need_now, 0):.2f}",
                               common.INK["RED"] if need_now > 0 else common.INK["GREEN"],
                               f"S_H {latest['s_h_sim']:.2f} − {p['close_pct'] / 100:.2f} × {latest['rated_load']:.2f}"),
            common.metric_card("Cần giảm nếu tải tiếp tục tăng 1 giờ", f"{max(need_1h, 0):.2f}",
                               common.INK["RED"] if need_1h > 0 else common.INK["GREEN"],
                               "Theo tốc độ tăng hiện tại"),
            common.metric_card(f"{neighbor['station_name']} nhận thêm tối đa", f"{capacity:.2f}",
                               common.INK["GREEN"] if capacity >= suggestion else common.INK["RED"],
                               f"Giữ {neighbor['station_name']} dưới {n_params['yellow_pct']}%"),
        ]
        for col, card in zip(st.columns(3), cards):
            col.markdown(card, unsafe_allow_html=True)
    key = f"amount_{alert['alert_id']}_{'more' if more else 'first'}"
    st.session_state.setdefault(key, float(min(suggestion, max(capacity, 0))) or 0.1)
    amount = st.number_input(f"Lượng chuyển sang {neighbor['station_name']} (đơn vị S_H, gợi ý {suggestion:.1f})",
                             min_value=0.1, step=0.1, format="%.2f", key=key)
    st.markdown(transfer_preview(latest, neighbor, amount, p, n_params), unsafe_allow_html=True)
    too_much = amount > capacity
    if too_much:
        st.error(f"{neighbor['station_name']} chỉ nhận thêm tối đa {capacity:.2f} để giữ dưới {n_params['yellow_pct']}%.")
    label = "Chuyển thêm" if more else "Xác nhận chuyển tải"
    if st.button(label, type="primary", disabled=too_much, key=f"btn_{key}"):
        if db.transfer_load(alert["alert_id"], float(amount), neighbor["station_id"], common.operator()):
            st.toast(f"Đã chuyển {amount:.2f} sang {neighbor['station_name']} (mô phỏng từ mốc dữ liệu kế tiếp).")
        else:
            st.toast("Cảnh báo đã thay đổi trạng thái; không ghi chuyển tải.")
        st.rerun()


def follow_step(alert: dict, latest: dict, p: dict, neighbor: dict | None, n_params: dict | None) -> None:
    items = db.transfers(alert_id=alert["alert_id"])
    moved = sum(x["amount"] for x in items)
    load = latest["load_pct_sim"]
    closing = load < p["close_pct"]
    if items and latest["ts"] <= max(x["effective_from_ts"] for x in items):
        # Chuyển tải có tác dụng từ mốc dữ liệu KẾ TIẾP: chưa có số liệu sau chuyển để đánh giá
        pending = sum(x["amount"] for x in items if x["effective_from_ts"] >= latest["ts"])
        expected = (latest["s_h_sim"] - pending) / latest["rated_load"] * 100
        st.info(f"⏳ Đã ghi chuyển {pending:.2f} sang {neighbor['station_name'] if neighbor else 'trạm lân cận'}. "
                f"Tác dụng bắt đầu từ mốc dữ liệu kế tiếp: dự kiến {latest['station_name']} ≈ {expected:.1f}%.")
        root_cause_input(alert)
        return
    st.markdown(
        f"Đã chuyển **{moved:.2f}** sang {neighbor['station_name'] if neighbor else 'trạm lân cận'}. "
        f"Tải {latest['station_name']} sau chuyển: {common.span(f'{load:.1f}%', common.load_ink(load, p))} "
        f"(đo {latest['load_pct']:.1f}%). "
        + ("✅ Dưới ngưỡng đóng, cảnh báo sẽ đóng sau 2 mốc liên tiếp." if closing
           else f"Chưa về dưới {p['close_pct']}%."), unsafe_allow_html=True)
    if load >= p["yellow_pct"] and neighbor is not None:
        st.warning("Tải sau chuyển vẫn cao (tải đo tiếp tục tăng). Có thể chuyển thêm.")
        transfer_step(alert, latest, p, neighbor, n_params, more=True)
    root_cause_input(alert)
    st.caption(f"Tải sau chuyển tải là mô phỏng (tải đo − lượng chuyển). Cảnh báo đóng khi tải này < {p['close_pct']}% "
               f"hai lần liên tiếp; chuyển tải tự trả khi TẢI ĐO < {p['close_pct']}% hai lần liên tiếp.")


def root_cause_input(alert: dict) -> None:
    key = f"cause_{alert['alert_id']}"
    st.session_state.setdefault(key, alert.get("root_cause") or "")
    cause = st.text_input("Nguyên nhân", key=key)
    if st.button("Lưu nguyên nhân", key=f"save_{key}", disabled=not cause.strip()):
        db.save_root_cause(alert["alert_id"], cause.strip())
        st.toast("Đã lưu nguyên nhân.")
