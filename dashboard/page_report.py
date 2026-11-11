"""Trang 3 – Báo cáo quản trị (run LIVE): vận hành đang làm tốt đến đâu (v2, sped.md §4–5).

KPI theo GIỜ DỮ LIỆU: Đỏ chuyển tải ≤ 30 phút kể từ lúc lên Đỏ; Vàng theo dõi ≤ 60 phút kể từ lúc mở.
Đỏ bị bỏ lỡ = đã đóng, không chuyển tải, không báo giả. Chuyển tải: an toàn (SAFE) / hết giờ (TIMEOUT) / quá hạn.
Bảng "Kết quả xử lý sự cố Đỏ" cũng nằm trong trang Điều hành.
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

import common
import db

RED_SQL = """
SELECT q.alert_id, q.station_name, q.status, q.escalated_ts, q.resolved_ts, q.peak_load_pct, q.verdict,
       q.action_ts, q.data_wait_min, q.is_missed, q.is_late, q.is_overdue, q.acked_by,
       (SELECT sum(x.amount) FROM fact_alert_action x WHERE x.alert_id = q.alert_id) AS moved,
       (SELECT string_agg(DISTINCT x.return_reason, ', ') FROM fact_alert_action x WHERE x.alert_id = q.alert_id) AS returned,
       q.root_cause
  FROM v_alert_queue q
 WHERE q.run_id = %s AND q.severity = 'RED' ORDER BY q.escalated_ts DESC"""
KPI_SQL = """
SELECT count(*) FILTER (WHERE severity = 'RED')                                                   AS red_total,
       count(*) FILTER (WHERE severity = 'RED' AND action_ts IS NOT NULL AND NOT is_late)         AS red_on_time,
       count(*) FILTER (WHERE severity = 'RED' AND is_late)                                       AS red_late,
       count(*) FILTER (WHERE is_missed)                                                          AS red_missed,
       count(*) FILTER (WHERE severity = 'RED' AND verdict = 'FALSE_ALARM')                       AS red_false,
       count(*) FILTER (WHERE severity = 'RED' AND is_overdue)                                    AS red_overdue_open,
       avg(data_wait_min) FILTER (WHERE severity = 'RED' AND action_ts IS NOT NULL)               AS red_avg_min,
       count(*) FILTER (WHERE severity = 'YELLOW')                                                AS yellow_total,
       count(*) FILTER (WHERE severity = 'YELLOW' AND acked_ts IS NOT NULL AND data_wait_min <= deadline_min)
                                                                                                  AS yellow_on_time
  FROM v_alert_queue WHERE run_id = %s"""
TRANSFER_SQL = """
SELECT count(*) AS n, coalesce(sum(amount), 0) AS amount,
       count(*) FILTER (WHERE return_reason = 'SAFE')    AS safe,
       count(*) FILTER (WHERE return_reason = 'TIMEOUT') AS timeout,
       count(*) FILTER (WHERE is_overdue)                AS overdue
  FROM v_transfer_status WHERE run_id = %s AND action_type = 'TRANSFER'"""
BY_STATION_SQL = """
SELECT s.station_name, a.severity, count(*) AS n
  FROM fact_alert a JOIN dim_substation s USING (station_id)
 WHERE a.run_id = %s GROUP BY s.station_name, a.severity"""
SEVERITY_ORDER = ["RED", "YELLOW", "SENSOR"]
STATUS_LABEL = {"OPEN": "Chưa xử lý", "ACK": "Đang theo dõi", "ACTION_TAKEN": "Đã chuyển tải", "RESOLVED": "Đã đóng"}
RETURN_LABEL = {"SAFE": "An toàn", "TIMEOUT": "Hết 2 giờ", "SAFE, TIMEOUT": "An toàn, hết 2 giờ"}


def red_outcome(row: dict) -> str:
    if row["verdict"] == "FALSE_ALARM":
        return common.span("Báo giả", common.INK["GRAY"])
    if row["is_missed"]:
        return common.span("Bị bỏ lỡ", common.INK["RED"])
    if row["action_ts"] is None:
        return common.span("Quá hạn" if row["is_overdue"] else "Chưa xử lý", common.INK["RED"])
    return common.span("Trễ", common.INK["RED"]) if row["is_late"] else common.span("Đúng hạn", common.INK["GREEN"])


def handle_text(row: dict) -> str:
    if row["action_ts"] is None:
        return "—"
    ok = not row["is_late"]
    return common.span(f"{row['data_wait_min']:.0f} phút", common.INK["GREEN"] if ok else common.INK["RED"])


RED_COLUMNS = [
    ("alert_id", "#", lambda v, r: f"<b>{v}</b>"),
    ("station_name", "Trạm", None),
    ("status", "Trạng thái", lambda v, r: STATUS_LABEL.get(v, v)),
    ("escalated_ts", "Lên Đỏ (giờ dữ liệu)", lambda v, r: common.fmt_ts(v)),
    ("resolved_ts", "Đóng", lambda v, r: common.fmt_ts(v)),
    ("peak_load_pct", "Đỉnh tải", lambda v, r: common.span(f"{v:.1f}%", common.INK["RED"]) if v else "—"),
    ("data_wait_min", f"Chuyển tải sau (≤ {common.RED_DEADLINE_MIN}′ dữ liệu)", lambda v, r: handle_text(r)),
    ("is_missed", "Kết quả", lambda v, r: red_outcome(r)),
    ("moved", "Đã chuyển", lambda v, r: f"{v:.2f}" if v else "—"),
    ("returned", "Trả tải", lambda v, r: RETURN_LABEL.get(v, v) if v else "—"),
    ("root_cause", "Nguyên nhân", lambda v, r: v or "—"),
]


def red_stripe(row: dict) -> str:
    if row["status"] == "RESOLVED":
        if row["verdict"] == "FALSE_ALARM":
            return common.COLORS["SENSOR"]
        return common.COLORS["RED"] if row["is_missed"] or row["is_late"] else common.COLORS["NORMAL"]
    return common.COLORS["RED"]


def operations_block() -> None:
    reds = db.rows(RED_SQL, (db.RUN_ID,))
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
    common.section("Cảnh báo Đỏ · Vàng (thời hạn theo giờ dữ liệu)")
    kpi_cards(db.one(KPI_SQL, (db.RUN_ID,)))
    common.section("Chuyển tải")
    transfer_cards(db.one(TRANSFER_SQL, (db.RUN_ID,)))
    common.section("Số cảnh báo theo trạm và mức")
    station_chart(db.frame(BY_STATION_SQL, (db.RUN_ID,)))
    common.section("Kết quả xử lý sự cố Đỏ")
    operations_block()


def pass_ink(ok: bool | None) -> str:
    if ok is None:
        return common.INK["GRAY"]
    return common.INK["GREEN"] if ok else common.INK["RED"]


def share(part: int, total: int) -> str:
    return f"{part} / {total}" + (f" ({part / total:.0%})" if total else "")


def kpi_cards(k: dict) -> None:
    red, handled = k["red_total"], k["red_on_time"] + k["red_late"]
    missed_ok = None if red == 0 else k["red_missed"] == 0
    avg = k["red_avg_min"]
    cards = [
        common.metric_card(f"Đỏ chuyển tải đúng hạn (≤ {common.RED_DEADLINE_MIN}′)", share(k["red_on_time"], red),
                           pass_ink(None if red == 0 else k["red_on_time"] == handled and handled > 0),
                           f"Trễ {k['red_late']} · đang quá hạn {k['red_overdue_open']}"
                           + (f" · TB {avg:.0f} phút dữ liệu" if avg is not None else "")),
        common.metric_card("Đỏ bị bỏ lỡ", str(k["red_missed"]), pass_ink(missed_ok),
                           "Mục tiêu 0 · đã đóng mà không chuyển tải, không báo giả"),
        common.metric_card("Báo giả / tổng Đỏ", share(k["red_false"], red), common.INK["GRAY"],
                           "Phản ánh độ tin cậy của cảnh báo"),
        common.metric_card(f"Vàng theo dõi đúng hạn (≤ {common.YELLOW_DEADLINE_MIN}′)",
                           share(k["yellow_on_time"], k["yellow_total"]),
                           pass_ink(None if k["yellow_total"] == 0 else k["yellow_on_time"] == k["yellow_total"]),
                           "Bấm “Theo dõi” trong 60 phút dữ liệu"),
    ]
    for col, card in zip(st.columns(4), cards):
        col.markdown(card, unsafe_allow_html=True)


def transfer_cards(t: dict) -> None:
    n = t["n"]
    cards = [
        common.metric_card("Số lần chuyển tải", str(n), "#111827", f"Tổng lượng {t['amount']:.2f} (S_H)"),
        common.metric_card("Trả tải an toàn", share(t["safe"], n), pass_ink(None if n == 0 else t["safe"] > 0),
                           "Tải đo < 85% hai lần liên tiếp"),
        common.metric_card("Trả do hết 2 giờ", share(t["timeout"], n), common.INK["AMBER"] if t["timeout"] else "#111827",
                           "Đã 2 giờ, tải đo < 90%"),
        common.metric_card("Chuyển tải quá hạn", str(t["overdue"]), pass_ink(None if n == 0 else t["overdue"] == 0),
                           "Còn hiệu lực ≥ 2 giờ, tải đo vẫn ≥ 90%"),
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
