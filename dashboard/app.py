"""Dashboard Streamlit: 3 trang Điều hành · Chi tiết trạm · Báo cáo quản trị.

Chạy: streamlit run dashboard/app.py   (cần DATABASE_URL)
"""

import streamlit as st

import common
import db
import page_ops
import page_report
import page_station

st.set_page_config(page_title="Giám sát quá tải trạm biến áp", page_icon="⚡", layout="wide")

pages = {
    "ops": st.Page(page_ops.render, title="Điều hành", icon="🚦", url_path="dieu-hanh", default=True),
    "station": st.Page(page_station.render, title="Chi tiết trạm", icon="🔎", url_path="chi-tiet-tram"),
    "report": st.Page(page_report.render, title="Báo cáo quản trị", icon="📊", url_path="bao-cao"),
}
st.session_state["pages"] = pages

common.setup_page()
navigation = st.navigation(list(pages.values()))
common.sidebar(next(iter(db.params_by_station().values()), None))   # ngưỡng giống nhau cho mọi trạm
navigation.run()
