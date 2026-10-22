#!/usr/bin/env python3
"""
load_dimensions.py
==================
Nạp 3 file CSV dimension vào TimescaleDB (schema mart).

Dữ liệu được đánh dấu SIMULATED — do nhóm tự tạo, không lấy từ nguồn đo lường thực tế.
Xem chi tiết thiết kế trong: db/init/008_load_dimensions.sql

Cách chạy
---------
  # từ thư mục gốc dự án
  python data/load_dimensions.py

  # ghi đè chuỗi kết nối
  DATABASE_URL=postgresql://user:pass@host:5432/db python data/load_dimensions.py

Yêu cầu
--------
  pip install psycopg2-binary pandas python-dotenv
"""

import os
import sys
import csv
import pathlib
import logging

# Ưu tiên python-dotenv nếu có
try:
    from dotenv import load_dotenv
    load_dotenv(pathlib.Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

try:
    import psycopg2
    from psycopg2.extras import execute_values
except ImportError:
    print("ERROR: psycopg2 chưa được cài. Chạy: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Cấu hình logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Kết nối DB
# ---------------------------------------------------------------------------
DATA_DIR = pathlib.Path(__file__).resolve().parent  # thư mục chứa script này

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://{user}:{password}@{host}:{port}/{dbname}".format(
        user=os.environ.get("POSTGRES_USER", "iot"),
        password=os.environ.get("POSTGRES_PASSWORD", "iotpass"),
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ.get("POSTGRES_DB", "energyiot"),
    ),
)


def connect() -> psycopg2.extensions.connection:
    log.info("Kết nối tới: %s", DATABASE_URL.split("@")[-1])  # ẩn password
    return psycopg2.connect(DATABASE_URL)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_csv(filename: str) -> list[dict]:
    path = DATA_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy file: {path}")
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def coerce(value: str, typ):
    """Ép kiểu chuỗi CSV sang kiểu Python; chuỗi rỗng → None."""
    if value == "" or value is None:
        return None
    if typ is bool:
        return value.strip().lower() in ("true", "1", "yes")
    return typ(value)


# ---------------------------------------------------------------------------
# Nạp từng bảng
# ---------------------------------------------------------------------------

def load_dim_substation(cur) -> int:
    """
    dim_substation – SIMULATED
    Hệ số định mức (rating_factor):
      S1/Trạm Y : 1.10  (trạm sự cố Red Alert, hệ số thấp)
      S2/Trạm 2 : 1.10
      S3/Trạm 3 : 1.35  (sức chứa lớn, nhận chuyển tải từ Trạm Y)
      S4/Trạm 4 : 1.15
    rated_load để NULL – Thiện điền sau khi hiệu chuẩn P99 (EDA notebook).
    neighbor_substation_id: S1↔S3, S2↔S4.
    """
    rows = read_csv("dim_substation.csv")

    # TRUNCATE + reload để idempotent (CASCADE xóa FK ở các bảng phụ thuộc)
    cur.execute("TRUNCATE mart.dim_substation RESTART IDENTITY CASCADE;")

    records = [
        (
            coerce(r["substation_id"], int),
            r["substation_code"].strip(),
            r["display_name"].strip(),
            r["source_series"].strip(),
            r["reading_source_type"].strip(),
            coerce(r["rating_factor"], float),
            coerce(r["rated_load"], float),           # NULL nếu rỗng
            coerce(r["criticality"], int),
            coerce(r["neighbor_substation_id"], int), # NULL nếu rỗng
            r["attr_source"].strip(),
        )
        for r in rows
    ]

    execute_values(
        cur,
        """
        INSERT INTO mart.dim_substation
            (substation_id, substation_code, display_name,
             source_series, reading_source_type,
             rating_factor, rated_load,
             criticality, neighbor_substation_id, attr_source)
        VALUES %s
        """,
        records,
    )
    log.info("dim_substation: nạp %d dòng (SIMULATED)", len(records))
    return len(records)


def load_dim_customer_group(cur) -> int:
    """
    dim_customer_group – SIMULATED
    3 nhóm / trạm: công nghiệp (M), thương mại (L), dân dụng (L).
    load_share tổng = 1.000 cho mỗi trạm.
    shed_priority: 1 = sa thải trước (dân dụng); 3 = bảo vệ (công nghiệp).
    is_critical = TRUE → không được sa thải (nhóm công nghiệp nặng).
    """
    rows = read_csv("dim_customer_group.csv")
    cur.execute("TRUNCATE mart.dim_customer_group RESTART IDENTITY;")

    records = [
        (
            coerce(r["customer_group_id"], int),
            coerce(r["substation_id"], int),
            r["voltage_side"].strip(),
            r["group_name"].strip(),
            coerce(r["load_share"], float),
            coerce(r["shed_priority"], int),
            coerce(r["is_critical"], bool),
            r["attr_source"].strip(),
        )
        for r in rows
    ]

    execute_values(
        cur,
        """
        INSERT INTO mart.dim_customer_group
            (customer_group_id, substation_id, voltage_side, group_name,
             load_share, shed_priority, is_critical, attr_source)
        VALUES %s
        """,
        records,
    )
    log.info("dim_customer_group: nạp %d dòng (SIMULATED)", len(records))
    return len(records)


def load_scenario_mapping(cur) -> int:
    """
    scenario_mapping – SIMULATED
    S1, S2: dữ liệu ETT gốc (shift_days=0, noise_pct=0.00, random_seed=NULL).
    S3    : ETTm1 dịch 7 ngày + nhiễu 2.5%, seed=42.
    S4    : ETTm2 dịch 14 ngày + nhiễu 3.0%, seed=99.
    """
    rows = read_csv("scenario_mapping.csv")
    cur.execute("TRUNCATE mart.scenario_mapping;")

    records = [
        (
            coerce(r["substation_id"], int),
            r["mapping_version"].strip(),
            r["source_series"].strip(),
            coerce(r["shift_days"], int),
            coerce(r["noise_pct"], float),
            coerce(r["random_seed"], int),  # NULL cho trạm REAL
        )
        for r in rows
    ]

    execute_values(
        cur,
        """
        INSERT INTO mart.scenario_mapping
            (substation_id, mapping_version, source_series,
             shift_days, noise_pct, random_seed)
        VALUES %s
        """,
        records,
    )
    log.info("scenario_mapping: nạp %d dòng (SIMULATED)", len(records))
    return len(records)


# ---------------------------------------------------------------------------
# Kiểm tra sau khi nạp
# ---------------------------------------------------------------------------

def verify(cur) -> None:
    cur.execute(
        """
        SELECT
            s.substation_code,
            s.display_name,
            s.reading_source_type,
            s.rating_factor,
            s.criticality,
            ns.substation_code      AS neighbor_code,
            sm.shift_days,
            sm.noise_pct,
            COUNT(cg.customer_group_id) AS n_customer_groups
        FROM mart.dim_substation s
        LEFT JOIN mart.dim_substation ns
               ON ns.substation_id = s.neighbor_substation_id
        LEFT JOIN mart.scenario_mapping sm
               ON sm.substation_id = s.substation_id
              AND sm.mapping_version = 'v1'
        LEFT JOIN mart.dim_customer_group cg
               ON cg.substation_id = s.substation_id
        GROUP BY s.substation_id, s.substation_code, s.display_name,
                 s.reading_source_type, s.rating_factor, s.criticality,
                 ns.substation_code, sm.shift_days, sm.noise_pct
        ORDER BY s.substation_id;
        """
    )
    rows = cur.fetchall()
    header = ["code", "name", "type", "factor", "crit", "neighbor", "shift_d", "noise%", "groups"]
    sep = "-" * 90
    log.info("=== Kết quả kiểm tra ===")
    log.info(sep)
    log.info("  %-4s  %-10s  %-8s  %-7s  %-5s  %-8s  %-8s  %-7s  %-6s", *header)
    log.info(sep)
    for r in rows:
        log.info("  %-4s  %-10s  %-8s  %-7s  %-5s  %-8s  %-8s  %-7s  %-6s", *r)
    log.info(sep)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    conn = connect()
    try:
        with conn:          # auto-commit/rollback theo block
            with conn.cursor() as cur:
                load_dim_substation(cur)
                load_dim_customer_group(cur)
                load_scenario_mapping(cur)
                verify(cur)
        log.info("✓ Nạp dimension hoàn tất.")
    except Exception as exc:
        log.error("Lỗi khi nạp dimension: %s", exc)
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
