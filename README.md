# Energy-IoT-Obs-System

Real-time BI and exception-alerting system for energy asset monitoring.
Pipeline: sensor data → detection → alert ranking → acknowledgement → dispatch decision on Red Alert.

Data source: ETTm1 + ETTm2 (transformer load and oil temperature, 15-minute interval), mapped to 4 simulated substations.

## Architecture

```
Producer (replay) → TimescaleDB [raw → cleaned → agg] → Detector (Python) → fact_alert
                                        │                                       │
                                        └──────────────► Grafana ◄──────────────┘
                                                            │  "Acknowledge" link
                                                            ▼
                                                         FastAPI (/ack)
```

| Service       | Tech                  | Port | Role                                   |
|---------------|-----------------------|------|----------------------------------------|
| `timescaledb` | TimescaleDB (PG16)    | 5432 | Time-series warehouse (raw/cleaned/agg)|
| `grafana`     | Grafana               | 3000 | 4-screen dashboard                     |
| `api`         | FastAPI + Uvicorn     | 8000 | Handles alert acknowledgement          |

Producer and detector are added in later tasks.

## Project structure

```
Energy-IoT-Obs-System/
├── docker-compose.yml          # TimescaleDB + Grafana + FastAPI
├── .env.example                # Environment template (copy to .env)
├── .gitignore
├── README.md
│
├── db/
│   └── init/
│       └── 001_extensions.sql  # Runs once on first start: enable timescaledb, create schemas
│
├── grafana/
│   ├── provisioning/
│   │   ├── datasources/
│   │   │   └── timescaledb.yml # Auto-registers the TimescaleDB datasource
│   │   └── dashboards/
│   │       └── dashboards.yml  # Tells Grafana to load JSON from grafana/dashboards
│   └── dashboards/             # Exported dashboard JSON (dashboard task)
│
├── api/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       └── main.py             # /health, /ack (stub for now)
│
├── sql/                        # DDL, ELT stored procedures, continuous aggregates
├── producer/                   # Replay producer (writes to raw)
├── detector/                   # Detection, ranking and alert lifecycle job
├── data/                       # Scenario CSVs, param_threshold.csv, param_profile.csv
└── notebooks/                  # EDA notebooks (eda_ett.ipynb, eda_load_volatility.ipynb)
```

## Quick start

Requirements: Docker and Docker Compose v2.

```bash
cp .env.example .env        # then edit passwords if needed
docker compose up -d
docker compose ps           # all services should be "healthy" / "running"
```

| What      | URL                           | Login                               |
|-----------|-------------------------------|-------------------------------------|
| Grafana   | http://localhost:3000         | `GF_SECURITY_ADMIN_*` from `.env`   |
| FastAPI   | http://localhost:8000/health  | none                                |
| Postgres  | `localhost:5432`              | `POSTGRES_*` from `.env`            |

## Verify the setup

1. Database: `docker compose exec timescaledb psql -U $POSTGRES_USER -d $POSTGRES_DB -c "\dx"` should list `timescaledb`.
2. Grafana: Connections → Data sources → TimescaleDB → **Save & test** should return success.
3. API: `curl localhost:8000/health` should return `{"status":"ok"}`.

Inside the Compose network, services reach the database at `timescaledb:5432` (not `localhost`).

## Common commands

```bash
docker compose logs -f <service>     # follow logs
docker compose restart <service>
docker compose down                  # stop, keep data
docker compose down -v               # stop and DELETE volumes (full reset)
```

Files in `db/init/` run only when the database volume is empty. After editing them, run `docker compose down -v && docker compose up -d`.

## Data provenance

Every row carries `source_type`, `scenario_id`, `mapping_version`:

- `REAL`: Substations 1–2 (original ETT data)
- `DERIVED`: Substations 3–4 (time-shifted 7–14 days, with noise)
- `SIMULATED`: injected incidents (overload, spike, sensor fault)

## Git workflow

- One branch per task, named as in the task tracker (e.g. `feature/docker-setup`, `feature/elt-pipeline`).
- Open a PR into `main`; the assigned checker reviews before merge.
- Never commit `.env`.