"""Sinh project Power BI v2 (PBIP) – sped.md §5.2, KHÔNG còn A/B testing.

    python powerbi/build_pbip_v2.py          (cần container energy-timescaledb đang chạy)

Ghi đè thư mục powerbi/CanhBaoQuaTai_v2/ (giữ nguyên bản cũ powerbi/CanhBaoQuaTai/).
Nguồn: view schema gold (powerbi/gold_views.sql) trên 2 DB:
  DbHist = energy_history (8 tháng, run HISTORY) · DbLive = energy (phát lại có người trực, run LIVE)
Chế độ Import, làm mới bằng Refresh tay.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLD = HERE / "CanhBaoQuaTai"
OUT = HERE / "CanhBaoQuaTai_v2"
NAME = "CanhBaoQuaTai_v2"
SM = OUT / f"{NAME}.SemanticModel"
RP = OUT / f"{NAME}.Report"
VISUAL_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.14.0/schema.json"
PAGE_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/1.0.0/schema.json"

# bảng model: (DB, view gold, lọc run_id hoặc None, nhóm)
TABLES = {
    "dim_station":           ("DbHist", "dim_station", None),
    "dim_customer_group":    ("DbHist", "dim_customer_group", None),
    "dim_date":              ("DbHist", "dim_date", None),
    "fact_load_hourly":      ("DbHist", "fact_load_hourly", None),
    "fact_overload_episode": ("DbHist", "fact_overload_episode", None),
    "fact_sensor_fault":     ("DbHist", "fact_sensor_fault", None),
    "hist_alert":            ("DbHist", "fact_alert", "HISTORY"),
    "live_alert":            ("DbLive", "fact_alert", "LIVE"),
    "live_transfer":         ("DbLive", "fact_transfer", "LIVE"),
}
SORT_BY = {("dim_date", "weekday_name"): "weekday_no"}

# (bảng nguồn, cột) → dim_station / dim_date ; (active?)
RELATIONSHIPS = [
    ("dim_customer_group", "station_id", "dim_station", "station_id", True),
    ("fact_load_hourly", "station_id", "dim_station", "station_id", True),
    ("fact_overload_episode", "station_id", "dim_station", "station_id", True),
    ("fact_sensor_fault", "station_id", "dim_station", "station_id", True),
    ("hist_alert", "station_id", "dim_station", "station_id", True),
    ("live_alert", "station_id", "dim_station", "station_id", True),
    ("live_transfer", "source_station", "dim_station", "station_id", True),
    ("live_transfer", "target_station", "dim_station", "station_id", False),
    ("fact_load_hourly", "date", "dim_date", "date", True),
    ("fact_overload_episode", "date", "dim_date", "date", True),
    ("fact_sensor_fault", "date", "dim_date", "date", True),
    ("hist_alert", "date", "dim_date", "date", True),
    ("live_alert", "date", "dim_date", "date", True),
    ("live_transfer", "date", "dim_date", "date", True),
]

RED_H, YEL_H, SEN_H = ('hist_alert[severity] = "RED"', 'hist_alert[severity] = "YELLOW"', 'hist_alert[severity] = "SENSOR"')
RED_L, YEL_L = 'live_alert[severity] = "RED"', 'live_alert[severity] = "YELLOW"'
# (thư mục, tên, DAX, định dạng)
MEASURES = [
    ("1 Tải lưới", "Tải TB (%)", "AVERAGE ( fact_load_hourly[avg_load_pct] )", "0.0"),
    ("1 Tải lưới", "Tải đỉnh (%)", "MAX ( fact_load_hourly[max_load_pct] )", "0.0"),
    ("1 Tải lưới", "Giờ ≥ 90%", "SUM ( fact_load_hourly[is_ge90_hour] ) + 0", "0"),
    ("1 Tải lưới", "Giờ quá tải ≥ 100%", "SUM ( fact_load_hourly[is_overload_hour] ) + 0", "0"),
    ("1 Tải lưới", "% giờ ≥ 90%", "DIVIDE ( [Giờ ≥ 90%], COUNTROWS ( fact_load_hourly ) )", "0.0%"),
    ("2 Sự cố", "Cảnh báo Đỏ", f"CALCULATE ( COUNTROWS ( hist_alert ), {RED_H} ) + 0", "0"),
    ("2 Sự cố", "Cảnh báo Vàng", f"CALCULATE ( COUNTROWS ( hist_alert ), {YEL_H} ) + 0", "0"),
    ("2 Sự cố", "Cảnh báo cảm biến", f"CALCULATE ( COUNTROWS ( hist_alert ), {SEN_H} ) + 0", "0"),
    ("2 Sự cố", "Đợt quá tải thật", "COUNTROWS ( fact_overload_episode ) + 0", "0"),
    ("2 Sự cố", "Thời lượng đợt quá tải TB (phút)", "AVERAGE ( fact_overload_episode[duration_min] )", "0"),
    ("2 Sự cố", "Giờ ≥ 100% trong các đợt", "SUM ( fact_overload_episode[minutes_ge100] ) / 60", "0.0"),
    ("2 Sự cố", "Đỉnh đợt quá tải (%)", "MAX ( fact_overload_episode[peak_load_pct] )", "0.0"),
    ("2 Sự cố", "Cảnh báo không khớp sự cố (%)",
     "DIVIDE ( SUM ( hist_alert[is_unmatched] ), COUNTROWS ( hist_alert ) )", "0%"),
    ("3 Vận hành", "Đỏ (LIVE)", f"CALCULATE ( COUNTROWS ( live_alert ), {RED_L} ) + 0", "0"),
    ("3 Vận hành", "Đỏ đúng hạn", f"CALCULATE ( SUM ( live_alert[is_on_time] ), {RED_L} ) + 0", "0"),
    ("3 Vận hành", "Đỏ xử lý trễ", "SUM ( live_alert[is_late] ) + 0", "0"),
    ("3 Vận hành", "Đỏ bị bỏ lỡ", "SUM ( live_alert[is_missed] ) + 0", "0"),
    ("3 Vận hành", "Đỏ báo giả", "SUM ( live_alert[is_false_alarm_verdict] ) + 0", "0"),
    ("3 Vận hành", "% Đỏ đúng hạn", "DIVIDE ( [Đỏ đúng hạn], [Đỏ (LIVE)] - [Đỏ báo giả] )", "0%"),
    ("3 Vận hành", "Tỷ lệ báo giả", "DIVIDE ( [Đỏ báo giả], [Đỏ (LIVE)] )", "0%"),
    ("3 Vận hành", "Thời gian chuyển tải TB (phút dữ liệu)",
     f"CALCULATE ( AVERAGE ( live_alert[handle_min] ), {RED_L}, live_alert[is_handled] = 1 )", "0"),
    ("3 Vận hành", "% Vàng theo dõi đúng hạn",
     f"DIVIDE ( CALCULATE ( SUM ( live_alert[is_on_time] ), {YEL_L} ), CALCULATE ( COUNTROWS ( live_alert ), {YEL_L} ) )", "0%"),
    ("4 Chuyển tải", "Số lần chuyển tải", "COUNTROWS ( live_transfer ) + 0", "0"),
    ("4 Chuyển tải", "Tổng lượng chuyển", "SUM ( live_transfer[amount] ) + 0", "0.00"),
    ("4 Chuyển tải", "% trả tải an toàn", "DIVIDE ( SUM ( live_transfer[is_safe_return] ), COUNTROWS ( live_transfer ) )", "0%"),
    ("4 Chuyển tải", "Trả do hết 2 giờ", "SUM ( live_transfer[is_timeout_return] ) + 0", "0"),
    ("4 Chuyển tải", "Chuyển tải quá hạn", "SUM ( live_transfer[is_overdue] ) + 0", "0"),
    ("4 Chuyển tải", "Trạm nhận bị đẩy lên Vàng/Đỏ", "SUM ( live_transfer[receiver_pushed] ) + 0", "0"),
    ("5 Cảm biến", "Lỗi cảm biến", "COUNTROWS ( fact_sensor_fault ) + 0", "0"),
    ("5 Cảm biến", "Giờ mất dữ liệu tin cậy", "SUM ( fact_sensor_fault[duration_hours] ) + 0", "0"),
    ("5 Cảm biến", "% lỗi cảm biến được phát hiện",
     "DIVIDE ( SUM ( fact_sensor_fault[is_detected] ), COUNTROWS ( fact_sensor_fault ) )", "0%"),
    ("5 Cảm biến", "Trễ phát hiện TB (phút)", "AVERAGE ( fact_sensor_fault[detect_delay_min] )", "0"),
]


def guid() -> str:
    return str(uuid.uuid4())


def hexid() -> str:
    return uuid.uuid4().hex[:20]


def columns() -> dict[str, list[tuple[str, str]]]:
    sql = ("SELECT table_name || '|' || column_name || '|' || data_type FROM information_schema.columns "
           "WHERE table_schema = 'gold' ORDER BY table_name, ordinal_position")
    out = subprocess.run(["docker", "exec", "energy-timescaledb", "psql", "-U", "energy", "-d", "energy_history",
                          "-tAc", sql], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    cols: dict[str, list[tuple[str, str]]] = {}
    for line in out.splitlines():
        if line.count("|") == 2:
            t, c, d = line.split("|")
            cols.setdefault(t, []).append((c, d))
    return cols


def types(pg: str) -> tuple[str, str, str | None, str | None]:
    if pg in ("bigint", "integer", "smallint"):
        return "Int64.Type", "int64", "0", None
    if pg in ("numeric", "double precision", "real"):
        return "type number", "double", "#,0.##", None
    if pg.startswith("timestamp"):
        return "type datetime", "dateTime", "dd/mm/yyyy hh:nn", "DateTime"
    if pg == "date":
        return "type date", "dateTime", "dd/mm/yyyy", "Date"
    if pg == "boolean":
        return "type logical", "boolean", None, None
    return "type text", "string", None, None


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, obj) -> None:
    write(path, json.dumps(obj, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- semantic model
def build_model(cols: dict) -> None:
    write_json(SM / ".platform", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "SemanticModel", "displayName": NAME}, "config": {"version": "2.0", "logicalId": guid()}})
    write_json(SM / "definition.pbism", {"version": "4.2", "settings": {}})
    write(SM / "definition/database.tmdl", "database\n\tcompatibilityLevel: 1606\n\n")
    shutil.copy(OLD / "CanhBaoQuaTai.SemanticModel/definition/cultures/en-US.tmdl", _mk(SM / "definition/cultures/en-US.tmdl"))
    expr = ""
    for name, val in (("PgServer", "localhost:5433"), ("DbHist", "energy_history"), ("DbLive", "energy")):
        expr += (f'expression {name} = "{val}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n'
                 f"\tlineageTag: {guid()}\n\n\tannotation PBI_ResultType = Text\n\n")
    write(SM / "definition/expressions.tmdl", expr)

    for table, (db, view, run) in TABLES.items():
        out = [f"table {table}", f"\tlineageTag: {guid()}", ""]
        for col, pg in cols[view]:
            _, dt, fmt, under = types(pg)
            out.append(f"\tcolumn {col}")
            out.append(f"\t\tdataType: {dt}")
            if fmt:
                out.append(f"\t\tformatString: {fmt}")
            out += [f"\t\tlineageTag: {guid()}", "\t\tsummarizeBy: none", f"\t\tsourceColumn: {col}"]
            if (table, col) in SORT_BY:
                out.append(f"\t\tsortByColumn: {SORT_BY[(table, col)]}")
            out.append("")
            if under:
                out += [f"\t\tannotation UnderlyingDateTimeDataType = {under}", ""]
        typed = ", ".join(f'{{"{c}", {types(p)[0]}}}' for c, p in cols[view])
        steps = [f"Db = PostgreSQL.Database ( PgServer, {db} ),",
                 f'Source = Db{{[Schema = "gold", Item = "{view}"]}}[Data],']
        last = "Source"
        if run:
            steps.append(f'Run = Table.SelectRows ( Source, each [run_id] = "{run}" ),')
            last = "Run"
        steps.append(f'Typed = Table.TransformColumnTypes ( {last}, {{{typed}}}, "en-US" )')
        out += [f"\tpartition {table} = m", "\t\tmode: import", "\t\tsource =", "\t\t\t\tlet"]
        out += [f"\t\t\t\t    {x}" for x in steps]
        out += ["\t\t\t\tin", "\t\t\t\t    Typed", ""]
        write(SM / f"definition/tables/{table}.tmdl", "\n".join(out))

    m = ["table _Measures", f"\tlineageTag: {guid()}", ""]
    for folder, name, dax, fmt in MEASURES:
        m += [f"\tmeasure '{name}' = {dax}", f"\t\tformatString: {fmt}", f"\t\tdisplayFolder: {folder}",
              f"\t\tlineageTag: {guid()}", ""]
    m += ["\tcolumn Value", "\t\tdataType: string", "\t\tisHidden", f"\t\tlineageTag: {guid()}",
          "\t\tsummarizeBy: none", "\t\tsourceColumn: Value", ""]
    m += ["\tpartition _Measures = m", "\t\tmode: import", "\t\tsource =", "\t\t\t\tlet",
          '\t\t\t\t    Source = #table ( type table [Value = text], {} )', "\t\t\t\tin", "\t\t\t\t    Source", ""]
    write(SM / "definition/tables/_Measures.tmdl", "\n".join(m))

    rel = []
    for frm, fc, to, tc, active in RELATIONSHIPS:
        rel += [f"relationship {guid()}"] + ([] if active else ["\tisActive: false"]) + [
            f"\tfromColumn: {frm}.{fc}", f"\ttoColumn: {to}.{tc}", ""]
    write(SM / "definition/relationships.tmdl", "\n".join(rel))

    order = list(TABLES) + ["_Measures", "PgServer", "DbHist", "DbLive"]
    model = ["model Model", "\tculture: en-US", "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
             "\tsourceQueryCulture: en-US", "\tdataAccessOptions", "\t\tlegacyRedirects", "\t\treturnErrorValuesAsNull", "",
             "annotation __PBI_TimeIntelligenceEnabled = 0", "",
             f"annotation PBI_QueryOrder = {json.dumps(order, ensure_ascii=False, separators=(',', ':'))}", "",
             'annotation PBI_ProTooling = ["DevMode"]', ""]
    model += [f"ref table {t}" for t in list(TABLES) + ["_Measures"]] + ["", "ref cultureInfo en-US", ""]
    write(SM / "definition/model.tmdl", "\n".join(model))


def _mk(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------- report
def lit(v: str) -> dict:
    return {"expr": {"Literal": {"Value": v}}}


def title(text: str) -> dict:
    return {"title": [{"properties": {"show": lit("true"), "text": lit("'" + text.replace("'", "''") + "'"),
                                      "fontSize": lit("11D")}}]}


def measure(name: str) -> dict:
    return {"field": {"Measure": {"Expression": {"SourceRef": {"Entity": "_Measures"}}, "Property": name}},
            "queryRef": f"_Measures.{name}", "nativeQueryRef": name}


def column(entity: str, col: str) -> dict:
    return {"field": {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": col}},
            "queryRef": f"{entity}.{col}", "nativeQueryRef": col}


class Page:
    def __init__(self, display: str) -> None:
        self.id = hexid()
        self.dir = RP / "definition/pages" / self.id
        self.z = 0
        write_json(self.dir / "page.json", {"$schema": PAGE_SCHEMA, "name": self.id, "displayName": display,
                                            "displayOption": "FitToPage", "height": 720, "width": 1280})

    def add(self, x: int, y: int, w: int, h: int, visual: dict) -> None:
        self.z += 1000
        vid = hexid()
        write_json(self.dir / "visuals" / vid / "visual.json", {
            "$schema": VISUAL_SCHEMA, "name": vid,
            "position": {"x": x, "y": y, "z": self.z, "height": h, "width": w, "tabOrder": self.z},
            "visual": visual})

    def card(self, x, y, w, h, name, text, color="'#1F2A44'"):
        self.add(x, y, w, h, {
            "visualType": "card", "query": {"queryState": {"Values": {"projections": [measure(name)]}}},
            "objects": {"labels": [{"properties": {"fontSize": lit("22D"), "color": {"solid": {"color": lit(color)}}}}],
                        "categoryLabels": [{"properties": {"show": lit("false")}}]},
            "visualContainerObjects": title(text), "drillFilterOtherVisuals": True})

    def chart(self, kind, x, y, w, h, category, measures, text, series=None):
        state = {"Category": {"projections": [dict(column(*category), active=True)]},
                 "Y": {"projections": [measure(m) for m in measures]}}
        if series:
            state["Series"] = {"projections": [column(*series)]}
        self.add(x, y, w, h, {
            "visualType": kind, "query": {"queryState": state},
            "objects": {"labels": [{"properties": {"show": lit("true")}}]} if kind != "lineChart" else {},
            "visualContainerObjects": title(text), "drillFilterOtherVisuals": True})

    def table(self, x, y, w, h, entity, cols, text):
        self.add(x, y, w, h, {
            "visualType": "tableEx",
            "query": {"queryState": {"Values": {"projections": [column(entity, c) for c in cols]}}},
            "visualContainerObjects": title(text), "drillFilterOtherVisuals": True})

    def slicer(self, x, y, w, h, entity, col, text):
        self.add(x, y, w, h, {
            "visualType": "slicer",
            "query": {"queryState": {"Values": {"projections": [dict(column(entity, col), active=True)]}}},
            "objects": {"data": [{"properties": {"mode": lit("'Dropdown'")}}]},
            "visualContainerObjects": title(text), "drillFilterOtherVisuals": True})


def cards(page: Page, items: list[tuple[str, str]], y: int = 8, color="'#1F2A44'") -> None:
    n = len(items)
    w = (1280 - 16 * 2 - 8 * (n - 1)) // n
    for i, (m, t) in enumerate(items):
        page.card(16 + i * (w + 8), y, w, 96, m, t, color)


def build_report() -> list[str]:
    old = OLD / "CanhBaoQuaTai.Report"
    write_json(RP / ".platform", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "Report", "displayName": NAME}, "config": {"version": "2.0", "logicalId": guid()}})
    write_json(RP / "definition.pbir", {"version": "4.0",
                                        "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}})
    shutil.copy(old / "definition/version.json", _mk(RP / "definition/version.json"))
    shutil.copy(old / "definition/report.json", _mk(RP / "definition/report.json"))
    shutil.copytree(old / "StaticResources", RP / "StaticResources", dirs_exist_ok=True)

    pages: list[Page] = []

    p = Page("1 · Lưới điện chịu tải thế nào?"); pages.append(p)
    cards(p, [("Tải TB (%)", "Tải trung bình (%)"), ("Tải đỉnh (%)", "Tải đỉnh (%)"),
              ("Giờ ≥ 90%", "Số giờ ≥ 90% định mức"), ("Giờ quá tải ≥ 100%", "Số giờ ≥ 100% định mức")])
    p.chart("clusteredColumnChart", 16, 112, 400, 296, ("dim_station", "station_name"), ["Giờ ≥ 90%", "Giờ quá tải ≥ 100%"],
            "Trạm nào thường xuyên tải cao? (giờ ≥ 90% / ≥ 100%)")
    p.chart("columnChart", 424, 112, 420, 296, ("fact_load_hourly", "hour_of_day"), ["Tải TB (%)"],
            "Cao điểm theo giờ trong ngày (tải TB %)")
    p.chart("columnChart", 852, 112, 412, 296, ("dim_date", "weekday_name"), ["Tải TB (%)"],
            "Theo ngày trong tuần (tải TB %)")
    p.chart("lineChart", 16, 416, 1248, 296, ("dim_date", "year_month"), ["Tải TB (%)"],
            "Tải theo tháng – đang tăng hay giảm? (theo trạm)", series=("dim_station", "station_name"))

    p = Page("2 · Có bao nhiêu sự cố, xảy ra ở đâu?"); pages.append(p)
    cards(p, [("Cảnh báo Đỏ", "Cảnh báo Đỏ"), ("Cảnh báo Vàng", "Cảnh báo Vàng"),
              ("Cảnh báo cảm biến", "Cảnh báo cảm biến"), ("Đợt quá tải thật", "Đợt quá tải thật (đáp án)"),
              ("Cảnh báo không khớp sự cố (%)", "Cảnh báo không khớp sự cố")])
    p.chart("clusteredColumnChart", 16, 112, 620, 290, ("dim_station", "station_name"),
            ["Cảnh báo Đỏ", "Cảnh báo Vàng", "Cảnh báo cảm biến"], "Cảnh báo theo trạm")
    p.chart("clusteredColumnChart", 644, 112, 620, 290, ("dim_date", "year_month"),
            ["Cảnh báo Đỏ", "Cảnh báo Vàng", "Cảnh báo cảm biến"], "Cảnh báo theo tháng")
    p.table(16, 410, 820, 302, "fact_overload_episode",
            ["event_id", "station_id", "t_start", "t_breach", "t_end", "duration_min", "peak_load_pct", "minutes_ge100"],
            "Các đợt quá tải thật: kéo dài bao lâu, đỉnh bao nhiêu %")
    p.chart("clusteredBarChart", 844, 410, 420, 302, ("dim_station", "station_name"),
            ["Đợt quá tải thật", "Giờ ≥ 100% trong các đợt"], "Trạm cần nâng cấp (số đợt × thời lượng ≥ 100%)")

    p = Page("3 · Đội vận hành xử lý có tốt không?"); pages.append(p)
    cards(p, [("% Đỏ đúng hạn", "Đỏ chuyển tải ≤ 30′ dữ liệu"), ("Đỏ bị bỏ lỡ", "Đỏ bị bỏ lỡ"),
              ("Đỏ xử lý trễ", "Đỏ xử lý trễ"), ("Tỷ lệ báo giả", "Tỷ lệ báo giả (người trực)"),
              ("Thời gian chuyển tải TB (phút dữ liệu)", "Chuyển tải TB (phút dữ liệu)"),
              ("% Vàng theo dõi đúng hạn", "Vàng theo dõi ≤ 60′")])
    p.chart("lineChart", 16, 112, 620, 290, ("dim_date", "year_month"), ["Thời gian chuyển tải TB (phút dữ liệu)"],
            "Thời gian phản ứng theo tháng dữ liệu (chỉ các lần phát lại)")
    p.chart("clusteredColumnChart", 644, 112, 620, 290, ("dim_station", "station_name"),
            ["Đỏ đúng hạn", "Đỏ xử lý trễ", "Đỏ bị bỏ lỡ", "Đỏ báo giả"], "Kết quả xử lý Đỏ theo trạm")
    p.table(16, 410, 1248, 302, "live_alert",
            ["alert_id", "station_id", "severity", "status", "opened_ts", "escalated_ts", "action_ts", "handle_min",
             "is_on_time", "is_late", "is_missed", "verdict", "acked_by", "received_load"],
            "Cảnh báo LIVE (thời gian xử lý tính bằng phút dữ liệu)")

    p = Page("4 · Chuyển tải có hiệu quả không?"); pages.append(p)
    cards(p, [("Số lần chuyển tải", "Số lần chuyển tải"), ("Tổng lượng chuyển", "Tổng lượng chuyển (S_H)"),
              ("% trả tải an toàn", "Về an toàn (tải đo < 85% 2 lần)"), ("Trả do hết 2 giờ", "Trả do hết 2 giờ"),
              ("Chuyển tải quá hạn", "Chuyển tải quá hạn"), ("Trạm nhận bị đẩy lên Vàng/Đỏ", "Trạm nhận bị đẩy lên Vàng/Đỏ")])
    p.table(16, 112, 1248, 600, "live_transfer",
            ["action_id", "alert_id", "source_station", "target_station", "amount", "effective_from_ts",
             "effective_to_ts", "return_reason", "active_min", "is_overdue", "receiver_level", "done_by"],
            "Từng lần chuyển tải (giờ dữ liệu)")

    p = Page("5 · Thiết bị đo có đáng tin không?"); pages.append(p)
    cards(p, [("Lỗi cảm biến", "Lỗi cảm biến (đáp án)"), ("Giờ mất dữ liệu tin cậy", "Giờ mất dữ liệu tin cậy"),
              ("% lỗi cảm biến được phát hiện", "Được phát hiện"), ("Trễ phát hiện TB (phút)", "Trễ phát hiện TB (phút)")])
    p.chart("clusteredColumnChart", 16, 112, 620, 290, ("dim_station", "station_name"),
            ["Lỗi cảm biến", "Giờ mất dữ liệu tin cậy"], "Trạm nào hay lỗi cảm biến nhất?")
    p.chart("columnChart", 644, 112, 620, 290, ("fact_sensor_fault", "start_day_of_month"), ["Lỗi cảm biến"],
            "Lỗi có lặp lại theo quy luật? (ngày bắt đầu trong tháng)")
    p.table(16, 410, 1248, 302, "fact_sensor_fault",
            ["event_id", "station_id", "detail", "t_start", "t_end", "duration_hours", "is_detected", "detect_delay_min"],
            "Danh sách lỗi cảm biến")

    write_json(RP / "definition/pages/pages.json", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.1.0/schema.json",
        "pageOrder": [pg.id for pg in pages], "activePageName": pages[0].id})
    return [pg.id for pg in pages]


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    write_json(OUT / f"{NAME}.pbip", {"version": "1.0", "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
                                      "settings": {"enableAutoRecovery": True}})
    build_model(columns())
    pages = build_report()
    print(f"OK: {OUT} · {len(TABLES)} bảng · {len(MEASURES)} measure · {len(pages)} trang")


if __name__ == "__main__":
    main()
