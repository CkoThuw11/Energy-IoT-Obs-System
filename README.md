# Energy-IoT-Obs-System

Real-time BI and exception-alerting system for transformer overload.
Pipeline: sensor replay → warehouse (raw → cleaned → agg_hourly) → detection → alert ranking → acknowledgement → dispatch decision on Red Alert.

Data source: ETTm1 + ETTm2 (transformer load and oil temperature, 15-minute interval), mapped to 4 substations.

## Architecture

```
scenario_data.csv → Producer → raw → cleaned → agg_hourly
                                       │
                                       ▼
                                   Detector ──► fact_alert ──► Grafana ◄──► FastAPI (/ack)
```

| Service       | Tech               | Host port | Role                                   |
|---------------|--------------------|-----------|----------------------------------------|
| `timescaledb` | TimescaleDB (PG16) | 5433      | Time-series warehouse                  |
| `grafana`     | Grafana            | 3000      | 4-screen dashboard                     |
| `api`         | FastAPI + Uvicorn  | 8000      | Alert acknowledgement (`/ack`)         |

Producer/ELT: see producer/README.md. Detector: see "Alert detector" below.

## Project structure

```
Energy-IoT-Obs-System/
├── docker-compose.yml          # TimescaleDB + Grafana + FastAPI
├── .env.example                # Environment template (copy to .env)
├── README.md
│
├── db/init/                    # Runs once, only when the DB volume is empty
│   ├── 001_schema.sql          # Full warehouse schema + dimension data
│   └── 002_load_data.sql       # Loads param_threshold.csv, event_label.csv
│
├── data/
│   ├── ETTm1.csv, ETTm2.csv    # Original data
│   ├── known_anomaly.csv       # Known faults in the original data (Task 1.1)
│   ├── param_threshold.csv     # Rated load and alert thresholds per substation (Task 1.2)
│   ├── scenario_data.csv       # 4 substations, 8-month test period, injected incidents (Task 1.3)
│   └── event_label.csv         # Answer key for evaluation (Task 1.3)
│
├── grafana/
│   ├── provisioning/           # Auto-registers datasource and dashboards
│   └── dashboards/             # Exported dashboard JSON
│
├── api/app/main.py             # /health, /ack
├── sql/                        # ELT procedures (raw → cleaned)
├── producer/                   # Replay producer (writes to raw)
├── detector/                   # Detection and alert lifecycle
├── notebooks/                  # EDA, parameter and scenario notebooks
└── docs/                       # architecture.md, erd.md, guide.md
```

## Warehouse schema

Time convention: `*_ts` = data time (used by all detection logic); `*_at` = wall-clock time (used only to measure human response).

| Table / view          | Content                                                             | Written by     |
|-----------------------|---------------------------------------------------------------------|----------------|
| `dim_substation`      | 4 substations, data origin, importance, neighbour for load transfer | `001_schema`   |
| `dim_customer_group`  | Customer groups per substation (SIMULATED)                          | `001_schema`   |
| `param_threshold`     | Rated load, Yellow/Red/close thresholds, stuck rule                 | `002_load_data`|
| `raw` (hypertable)    | Sensor values as received; key `(station_id, ts)`                   | Producer       |
| `cleaned` (hypertable)| raw + `s_h`, `load_pct`, `quality_flag`                             | ELT job        |
| `etl_watermark`       | Last processed `ts` per substation                                  | ELT job        |
| `agg_hourly`          | Hourly avg/max load and OT (continuous aggregate, auto-refresh)     | TimescaleDB    |
| `fact_alert`          | Alerts: OPEN → ACK → RESOLVED; one open alert per (run, station, type) | Detector, API |
| `eval.event_label`    | Answer key (38 events); not read by the detector                    | `002_load_data`|
| `v_station_latest`    | Latest load per substation (dashboard screen 1)                     | view           |
| `v_alert_queue`       | Alerts ordered Red → Yellow → Sensor, then TTB, then load           | view           |

## Quick start

Requirements: Docker and Docker Compose v2.

```bash
cp .env.example .env        # then edit passwords if needed
docker compose up -d
docker compose ps           # all services should be "healthy" / "running"
```

| What      | URL / address                 | Login                               |
|-----------|-------------------------------|-------------------------------------|
| Grafana   | http://localhost:3000         | `GRAFANA_*` from `.env`             |
| FastAPI   | http://localhost:8000/health  | none                                |
| Postgres  | `localhost:5433`              | `POSTGRES_*` from `.env`            |

Inside the Compose network, services reach the database at `timescaledb:5432`.

## Verify the setup

```bash
# 1. Schema and data loaded
docker compose exec timescaledb psql -U energy -d energy \
  -c "\dt *.*" \
  -c "SELECT count(*) FROM param_threshold" \
  -c "SELECT count(*) FROM eval.event_label"
# expect: 4 and 38

# 2. Init errors (should print nothing)
docker compose logs timescaledb | grep -i error

# 3. API
curl localhost:8000/health        # {"status":"ok"}
```

Grafana: Connections → Data sources → TimescaleDB → **Save & test**.
The provisioned dashboard already uses time zone **UTC**; for new dashboards set UTC, otherwise data times are shown shifted by +7 hours.

## Dashboard and alert acknowledgement

`grafana/dashboards/dashboard.json` is provisioned automatically into folder **Energy IoT** → **Energy Alert**
(http://localhost:3000/d/energy-alert/energy-alert). Auto-refresh 5 s, time zone UTC.
Colours: **Red** = overload ≥ 100 % or TTB ≤ 30 min, **Yellow** = ≥ 90 % or TTB ≤ 60 min, **Gray** = sensor fault.

| Screen | Content | Source |
|---|---|---|
| 1. Overview | Current `load_pct` of 4 substations, status (Gray if an open SENSOR alert), `load_pct` over the last 24 h | `v_station_latest`, `cleaned`, `fact_alert` |
| 2. Alert list | Red → Yellow → Sensor, then shortest TTB, then highest load; status column; **Tiếp nhận** link | `v_alert_queue` |
| 3. Red Alert detail (variable *Trạm*) | `load_pct`, OT, speed, TTB, customer groups, spare capacity of TRAM_3 = 90 % × rated_load − S_H | `cleaned`, `v_station_latest`, `dim_customer_group` |
| 4. KPI | Alerts per severity/substation, avg response time (`acked_at − opened_at`), unacknowledged, missed | `fact_alert` |

The data is 2017–2018, so panels do not use the Grafana time picker: "last 24 h" means 24 h back from the newest
record in `cleaned`, and the X axis is *hours relative to the newest record* (0 = now).
The dashboard reads alerts with `run_id = 'LIVE'` (detector default for `--source db`).

**Tiếp nhận** calls `GET http://localhost:8000/ack?alert_id=<id>&user=<grafana login>`:
only an `OPEN` alert becomes `ACK` with `acked_by` and `acked_at = now()`; clicking again does not overwrite it.
The API then redirects (303) back to the dashboard. Unknown `alert_id` → 404.

Try the dashboard without producer/detector (**overwrites LIVE alerts and cleaned for 2017-11-01**):

```bash
docker compose exec -T timescaledb psql -U energy -d energy < sql/demo_dashboard_seed.sql
# open the dashboard, click "Tiếp nhận" on the Trạm Y Red alert, then check:
docker compose exec timescaledb psql -U energy -d energy \
  -c "SELECT alert_id, status, acked_by, acked_at FROM fact_alert WHERE run_id='LIVE' ORDER BY alert_id"
```

If you edit the dashboard in the Grafana UI, export it (Share → Export → *Save to file*) back to
`grafana/dashboards/dashboard.json` and keep `uid = energy-alert` (the API redirect uses it).

## Alert detector

Python 3.10 or newer is required. CSV replay uses only the Python standard library:

```bash
python detector/detector.py --source csv --method A
python detector/detector.py --source csv --method B
python -m unittest discover -s detector -v
```

The commands write `alerts_A.csv` and `alerts_B.csv` in the current directory. Override the input, parameters, or output with `--input`, `--params`, and `--output`. Each output row is the latest snapshot of one alert, with `opened_ts`, `escalated_ts`, and `resolved_ts` in data time; a still-open alert has an empty `resolved_ts`. These files do not contain the answer key. A uses load thresholds; B adds stuck-sensor detection and time-to-breach (TTB).

For database mode, install `pip install -r detector/requirements.txt`, start a compatible TimescaleDB with this repository's schema and populated `param_threshold` and `cleaned` tables, and set `DATABASE_URL` locally. Run:

```bash
python detector/detector.py --source db --method B --run-id LIVE
python detector/detector.py --source db --method B --run-id EVAL_B --once
```

The detector polls every 10 seconds by default (`--poll-seconds`). `--once` drains current data and exits. Its database user must be able to create `detector_checkpoint` and read `cleaned` and `param_threshold` and write `fact_alert`. Checkpoints and alert changes commit together; restarting with the same run ID resumes per station and preserves ACK status. Use a different run ID for each method or independent replay. Only one detector may run with a given run ID. The producer/ELT pipeline must supply `cleaned` in timestamp order per station; late rows older than a checkpoint are not replayed.

Both modes calculate the 1-hour rate only across consecutive 15-minute samples. A stuck sensor is detected at the fourth identical S_H sample; overload checks are skipped while stuck, and the sensor alert resolves when S_H changes. An overload alert resolves after two consecutive samples below the station's close threshold; RED stays RED until resolution, retaining `escalated_ts`. If a TTB jumps directly to the RED condition, an alert can open RED without a preceding YELLOW. These conventions are shared by both modes. The detector never uses `event_label` for detection.

## Common commands

```bash
docker compose logs -f <service>     # follow logs
docker compose restart <service>
docker compose down                  # stop, keep data
docker compose down -v               # stop and DELETE volumes (full reset)
```

Files in `db/init/` run only when the database volume is empty. After editing them, or after changing `param_threshold.csv` / `event_label.csv`, run:

```bash
docker compose down -v && docker compose up -d
```

## Substations and data provenance

| Substation | Source | Origin  | Note                                        |
|------------|--------|---------|---------------------------------------------|
| `TRAM_Y`   | ETTm1  | REAL    | Near its limit (k = 1.05); Red Alert case   |
| `TRAM_2`   | ETTm2  | REAL    |                                             |
| `TRAM_3`   | ETTm1  | DERIVED | Shifted +7 days; spare capacity (k = 1.43), receives load transfer from TRAM_Y |
| `TRAM_4`   | ETTm2  | DERIVED | Shifted +14 days                            |

- Injected overload incidents are `SIMULATED`. Real overloads (TRAM_Y) and real sensor faults are `REAL`.
- Provenance is stored **only** in `eval.event_label` (`source_type`, `detail`). `raw` and `cleaned` carry no labels, so the detector cannot see the answers.
- Calibration period: 2016-07-01 → 2017-10-31. Test period: 2017-11-01 → 2018-06-26. All thresholds are computed on the calibration period only.

## Git workflow

- Never commit `.env`.