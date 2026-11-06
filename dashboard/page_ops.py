"""Trang Điều hành: trạm nào nguy, làm gì trước, và vận hành đang làm tốt đến đâu."""

from __future__ import annotations

import streamlit as st

import common
import db
import page_report

QUEUE_WIDTHS = [0.4, 1.1, 1.0, 0.9, 0.9, 2.0, 1.4]
QUEUE_HEADERS = ["#", "Trạm", "Mức", "Tải", "Còn", "Người nhận / thời gian chờ", ""]


def render() -> None:
    live_block()


@st.fragment(run_every=5)
def live_block() -> None:
    params = db.params_by_station()
    stations = db.station_latest()
    queue = db.open_queue()
    common.topbar("Điều hành · Giám sát quá tải trạm biến áp")
    if not stations:
        st.info("Chưa có dữ liệu. Hãy chạy producer (xem README: scripts/replay_event.sh).")
        return
    common.section("Tình trạng trạm")
    station_tiles(list(stations.values()), params)
    common.section(f"Hàng đợi cảnh báo đang mở ({len(queue)})")
    alert_queue(queue, params)
    common.section("Kết quả xử lý sự cố Đỏ")
    page_report.operations_block()


def tile_subline(row: dict, load: float, red_pct: float) -> str:
    level = row["load_level"]
    if level == "SENSOR":
        return "Cảm biến kẹt"
    if level == "NORMAL":
        return "Bình thường"
    if row["ttb_min"] is not None:
        return f"Còn {row['ttb_min']:.0f} phút"
    return "Đang quá tải" if load >= red_pct else "Tải không tăng"


def subline_ink(row: dict, load: float, p: dict) -> str:
    level = row["load_level"]
    if level == "SENSOR":
        return common.INK["GRAY"]
    if level == "NORMAL":
        return common.INK["GREEN"]
    if row["ttb_min"] is not None:
        return common.ttb_ink(row["ttb_min"], p)
    return common.load_ink(load, p)


def transfer_note(row: dict) -> str:
    """Dòng ghi chú khi trạm đang chuyển / nhận tải; nhãn "mô phỏng" đã nằm cạnh con số % nên không lặp lại."""
    if row["transfer_out"]:
        text = f"⇢ đang chuyển −{row['transfer_out']:.2f} · tải đo {row['load_pct']:.1f}%"
    elif row["transfer_in"]:
        text = f"⇠ đang nhận +{row['transfer_in']:.2f} · tải đo {row['load_pct']:.1f}%"
    else:
        return ""
    return f'<div class="sub" style="color:#4C78A8">{text}</div>'


def station_card(row: dict, p: dict) -> str:
    """Viền trái + chip = mức cảnh báo đang mở; số % / mũi tên / thanh = màu theo số liệu."""
    level = row["load_level"]
    load, simulated = common.shown_load(row)
    tag = f' <span style="font-size:0.8rem;color:#4C78A8">{common.SIM_TAG}</span>' if simulated else ""
    return (
        f'<div class="card" style="border-left-color:{common.COLORS[level]};background:{common.COLORS[level]}14">'
        f'<div class="name"><span>{row["station_name"]}</span>{common.chip(level)}</div>'
        f'<div><span class="big" style="color:{common.load_ink(load, p)}">{load:.1f}%</span>{tag}'
        f'<span class="trend">{common.trend_html(row["speed_pct_h"])}</span></div>'
        f'{common.load_bar(load, p)}'
        f'{subline_html(row, load, p)}{transfer_note(row)}</div>')


def subline_html(row: dict, load: float, p: dict) -> str:
    """Dòng phụ; bỏ "Bình thường" khi đang có dòng chuyển tải (chip mức đã ghi Bình thường)."""
    if row["load_level"] == "NORMAL" and common.shown_load(row)[1]:
        return ""
    return f'<div class="sub" style="color:{subline_ink(row, load, p)}">{tile_subline(row, load, p["red_pct"])}</div>'


def station_tiles(stations: list[dict], params: dict[str, dict]) -> None:
    for col, row in zip(st.columns(len(stations)), stations):
        with col:
            st.markdown(station_card(row, params[row["station_id"]]), unsafe_allow_html=True)
            if st.button("Xem trạm", key=f"tile_{row['station_id']}", width="stretch"):
                common.open_station(row["station_id"])


def queue_header() -> None:
    cells = "".join(f'<div style="flex:{w}">{t}</div>' for w, t in zip(QUEUE_WIDTHS, QUEUE_HEADERS))
    st.markdown(f'<div class="qhead" style="display:flex;gap:16px;padding-left:12px">{cells}</div>',
                unsafe_allow_html=True)


def owner_text(alert: dict) -> str:
    if alert["acked_by"]:
        return (f"{common.span('✓', common.INK['GREEN'])} {alert['acked_by']} · nhận sau "
                f"{common.fmt_minutes(alert['response_sec'])}")
    ink = common.INK["RED"] if alert["is_escalated"] else common.INK["AMBER"]
    return common.span(f"Chưa nhận · chờ {common.fmt_minutes(alert['wait_sec'])}", ink)


def alert_queue(alerts: list[dict], params: dict[str, dict]) -> None:
    if not alerts:
        st.success("Không có cảnh báo đang mở.")
        return
    queue_header()
    for index, alert in enumerate(alerts, start=1):
        with st.container(border=alert["is_escalated"]):
            queue_row(index, alert, params[alert["station_id"]])


def queue_row(index: int, alert: dict, p: dict) -> None:
    cols = st.columns(QUEUE_WIDTHS, vertical_alignment="center")
    escalated = alert["is_escalated"]
    stripe = common.COLORS[alert["severity"]]
    cols[0].markdown(f'<span style="border-left:5px solid {stripe};padding-left:8px;font-weight:800">{index}</span>',
                     unsafe_allow_html=True)
    cols[1].markdown(f"**{alert['station_name']}**")
    label = common.LEVEL_LABEL[alert["severity"]] + (" · quá hạn" if escalated else "")
    cols[2].markdown(common.chip(alert["severity"], label, blink=escalated), unsafe_allow_html=True)
    load, ttb = alert["load_pct"], alert["ttb_min"]
    cols[3].markdown(common.span(f"{load:.1f}%", common.load_ink(load, p)) if load is not None else "—",
                     unsafe_allow_html=True)
    cols[4].markdown(common.span(f"{ttb:.0f} phút", common.ttb_ink(ttb, p)) if ttb is not None else "—",
                     unsafe_allow_html=True)
    cols[5].markdown(owner_text(alert), unsafe_allow_html=True)
    with cols[6]:
        action_button(alert)


def action_button(alert: dict) -> None:
    key = f"q_{alert['alert_id']}"
    severity, status = alert["severity"], alert["status"]
    if severity == "RED":
        label = "Nhận & xử lý" if status == "OPEN" else "Xử lý"
        if st.button(label, key=f"red_{key}", width="stretch"):
            if status == "OPEN":
                db.ack(alert["alert_id"], common.operator())
            common.open_station(alert["station_id"])
    elif severity == "YELLOW":
        if status == "OPEN":
            if st.button("Nhận", key=key, width="stretch"):
                db.ack(alert["alert_id"], common.operator())
                st.rerun()
        elif st.button("Xem", key=key, width="stretch"):
            common.open_station(alert["station_id"])
    else:
        done = alert["root_cause"] == "SENSOR_MAINTENANCE"
        if st.button("Đã chuyển bảo trì" if done else "Chuyển bảo trì", key=key,
                     disabled=done, width="stretch"):
            db.send_to_maintenance(alert["alert_id"], common.operator())
            st.rerun()
