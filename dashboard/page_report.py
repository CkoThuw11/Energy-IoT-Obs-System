"""Trang 3 – Báo cáo quản trị (run LIVE): vận hành đang làm tốt đến đâu.

Gồm 4 ô KPI kèm mục tiêu, biểu đồ cảnh báo theo trạm × mức, và bảng "Kết quả xử lý sự cố Đỏ"
(bảng này cũng nằm trong trang Điều hành). Thời gian tính theo giờ thật (*_at).
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

import common
import db

RED_SQL = """
SELECT a.alert_id, s.station_name, a.status, a.opened_ts, a.resolved_ts, a.peak_load_pct, a.verdict,
       EXTRACT(EPOCH FROM a.acked_at - a.opened_at) AS ack_sec,
       EXTRACT(EPOCH FROM a.action_done_at - a.opened_at) AS act_sec,
       (SELECT sum(x.amount) FROM fact_alert_action x WHERE x.alert_id = a.alert_id) AS moved,
       a.root_cause
  FROM fact_alert a JOIN dim_substation s USING (station_id)
 WHERE a.run_id = %s AND a.severity = 'RED' ORDER BY a.opened_ts DESC"""
KPI_SQL = """
SELECT avg(EXTRACT(EPOCH FROM acked_at - opened_at))       FILTER (WHERE acked_at IS NOT NULL)       AS ack_avg,
       avg(EXTRACT(EPOCH FROM action_done_at - opened_at)) FILTER (WHERE action_done_at IS NOT NULL) AS act_avg,
       count(*) FILTER (WHERE status = 'RESOLVED' AND acked_at IS NULL)                              AS missed,
       count(*) FILTER (WHERE verdict = 'FALSE_ALARM')                                               AS false_alarm,
       count(*)                                                                                      AS total
  FROM fact_alert WHERE run_id = %s AND severity = 'RED'"""
BY_STATION_SQL = """
SELECT s.station_name, a.severity, count(*) AS n
  FROM fact_alert a JOIN dim_substation s USING (station_id)
 WHERE a.run_id = %s GROUP BY s.station_name, a.severity"""
ACK_TARGET_SEC = 3 * 60          # Đỏ phải được nhận trong 3 phút (cùng mốc leo thang của v_alert_queue)
SEVERITY_ORDER = ["RED", "YELLOW", "SENSOR"]
STATUS_LABEL = {"OPEN": "Chưa nhận", "ACK": "Đã nhận", "ACTION_TAKEN": "Đã chuyển tải", "RESOLVED": "Đã đóng"}


def timing(seconds: float | None, limit: float) -> str:
    if seconds is None:
        return "—"
    return common.span(common.fmt_minutes(seconds), common.INK["GREEN"] if seconds <= limit else common.INK["RED"])


RED_COLUMNS = [
    ("alert_id", "#", lambda v, r: f"<b>{v}</b>"),
    ("station_name", "Trạm", None),
    ("status", "Trạng thái", lambda v, r: STATUS_LABEL.get(v, v)),
    ("opened_ts", "Mở (giờ dữ liệu)", lambda v, r: common.fmt_ts(v)),
    ("resolved_ts", "Đóng", lambda v, r: common.fmt_ts(v)),
    ("peak_load_pct", "Đỉnh tải", lambda v, r: common.span(f"{v:.1f}%", common.INK["RED"]) if v else "—"),
    ("verdict", "Xác minh", lambda v, r: {"REAL": "Quá tải thật", "FALSE_ALARM": "Báo giả"}.get(v, "—")),
    ("ack_sec", "Nhận sau (giờ thật)", lambda v, r: timing(v, ACK_TARGET_SEC)),
    ("act_sec", "Chuyển tải sau (giờ thật)", lambda v, r: timing(v, common.TARGET_MIN * 60)),
    ("moved", "Đã chuyển", lambda v, r: f"{v:.2f}" if v else "—"),
    ("root_cause", "Nguyên nhân", lambda v, r: v or "—"),
]


def red_stripe(row: dict) -> str:
    if row["status"] == "RESOLVED":
        return common.COLORS["SENSOR"] if row["verdict"] == "FALSE_ALARM" else common.COLORS["NORMAL"]
    return common.COLORS["RED"]


def operations_block() -> None:
    run = db.RUN_ID
    reds = db.rows(RED_SQL, (run,))
    if not reds:
        st.caption("Chưa có cảnh báo Đỏ.")
    else:
        st.markdown(common.html_table(reds, RED_COLUMNS, stripe=red_stripe), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Trang Báo cáo quản trị
# ---------------------------------------------------------------------------
def render() -> None:
    report_block()


@st.fragment(run_every=5)
def report_block() -> None:
    common.topbar("Báo cáo quản trị · Hiệu quả xử lý cảnh báo")
    common.section("Chỉ số vận hành (cảnh báo Đỏ)")
    kpi_cards(db.one(KPI_SQL, (db.RUN_ID,)))
    common.section("Số cảnh báo theo trạm và mức")
    station_chart(db.frame(BY_STATION_SQL, (db.RUN_ID,)))
    common.section("Kết quả xử lý sự cố Đỏ")
    operations_block()


def pass_ink(ok: bool | None) -> str:
    if ok is None:
        return common.INK["GRAY"]
    return common.INK["GREEN"] if ok else common.INK["RED"]


def verdict_note(target: str, ok: bool | None) -> str:
    if ok is None:
        return f"Mục tiêu {target} · chưa có dữ liệu"
    return f"Mục tiêu {target} · " + common.span("Đạt" if ok else "Chưa đạt", pass_ink(ok))


def avg_card(label: str, seconds: float | None, limit: float, target: str) -> str:
    ok = None if seconds is None else seconds <= limit
    return common.metric_card(label, common.fmt_minutes(seconds), pass_ink(ok), verdict_note(target, ok))


def kpi_cards(k: dict) -> None:
    total = k["total"]
    missed_ok = None if total == 0 else k["missed"] == 0
    false_share = f"{k['false_alarm']} / {total}" + (f" ({k['false_alarm'] / total:.0%})" if total else "")
    cards = [
        avg_card("Nhận TB (giờ thật)", k["ack_avg"], ACK_TARGET_SEC, "≤ 3:00"),
        avg_card("Chuyển tải TB (giờ thật)", k["act_avg"], common.TARGET_MIN * 60, f"≤ {common.TARGET_MIN}:00"),
        common.metric_card("Đỏ bị bỏ lỡ", str(k["missed"]), pass_ink(missed_ok), verdict_note("0", missed_ok)
                           + "<br>Đã đóng mà chưa ai nhận"),
        common.metric_card("Báo giả / tổng Đỏ", false_share, common.INK["GRAY"],
                           "Theo dõi, không đặt mục tiêu<br>Recall / Precision chấm bằng event_label"),
    ]
    for col, card in zip(st.columns(4), cards):
        col.markdown(card, unsafe_allow_html=True)


def station_chart(df: pd.DataFrame) -> None:
    if df.empty:
        st.caption("Chưa có cảnh báo.")
        return
    df["Mức"] = df["severity"].map(common.LEVEL_LABEL)
    order = [common.LEVEL_LABEL[s] for s in SEVERITY_ORDER]
    color = alt.Color("Mức:N", scale=alt.Scale(domain=order, range=[common.COLORS[s] for s in SEVERITY_ORDER]),
                      legend=alt.Legend(orient="top", title=None))
    chart = alt.Chart(df).mark_bar().encode(
        x=alt.X("station_name:N", title=None, axis=alt.Axis(labelAngle=0)),
        xOffset=alt.XOffset("Mức:N", sort=order),
        y=alt.Y("n:Q", title="Số cảnh báo", axis=alt.Axis(tickMinStep=1)),
        color=color,
        tooltip=[alt.Tooltip("station_name:N", title="Trạm"), alt.Tooltip("Mức:N"),
                 alt.Tooltip("n:Q", title="Số cảnh báo")])
    labels = chart.mark_text(dy=-8, fontWeight="bold").encode(text="n:Q", color=alt.value("#111827"))
    st.altair_chart((chart + labels).properties(height=320)
                    .configure_axis(labelFontSize=13, titleFontSize=13, labelColor="#111827", titleColor="#111827")
                    .configure_legend(labelFontSize=13, labelColor="#111827")
                    .configure_view(strokeWidth=0), width="stretch", theme=None)
    st.caption("Mức của cảnh báo là mức cao nhất mà cảnh báo đạt tới (Vàng nâng lên Đỏ được tính là Đỏ).")
