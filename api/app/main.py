import os

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse

app = FastAPI(title="Energy Alert ACK API")
DATABASE_URL = os.environ["DATABASE_URL"]
# Địa chỉ Grafana mà TRÌNH DUYỆT truy cập được (không phải tên service trong docker)
GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://localhost:3000").rstrip("/")
DASHBOARD_PATH = os.environ.get("GRAFANA_DASHBOARD_PATH", "/d/energy-alert/energy-alert")


@app.get("/health")
def health():
    with psycopg.connect(DATABASE_URL, connect_timeout=3) as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/ack")
def ack(
    alert_id: int = Query(..., ge=1),
    user: str = Query(..., min_length=1, max_length=100),
):
    """Người trực bấm Tiếp nhận: OPEN → ACK, ghi người nhận + giờ thật, rồi quay lại Grafana.

    Chỉ cảnh báo đang OPEN mới được ACK; bấm lại lần 2 (hoặc cảnh báo đã RESOLVED)
    không ghi đè người/giờ tiếp nhận ban đầu.
    """
    user = user.strip()
    if not user:
        raise HTTPException(status_code=422, detail="user không được rỗng")

    with psycopg.connect(DATABASE_URL, connect_timeout=3) as conn:
        row = conn.execute(
            """UPDATE fact_alert
                  SET status = 'ACK', acked_by = %s, acked_at = now(), updated_at = now()
                WHERE alert_id = %s AND status = 'OPEN'
            RETURNING alert_id""",
            (user, alert_id),
        ).fetchone()
        if row is None:
            exists = conn.execute(
                "SELECT 1 FROM fact_alert WHERE alert_id = %s", (alert_id,)
            ).fetchone()
            if exists is None:
                raise HTTPException(status_code=404, detail=f"Không có cảnh báo {alert_id}")

    return RedirectResponse(f"{GRAFANA_URL}{DASHBOARD_PATH}", status_code=303)
