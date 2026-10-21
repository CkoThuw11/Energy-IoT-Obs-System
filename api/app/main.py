import os
import psycopg
from fastapi import FastAPI

app = FastAPI(title="Energy Alert ACK API")
DATABASE_URL = os.environ["DATABASE_URL"]


@app.get("/health")
def health():
    with psycopg.connect(DATABASE_URL, connect_timeout=3) as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}