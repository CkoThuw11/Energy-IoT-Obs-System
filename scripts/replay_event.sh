#!/usr/bin/env bash
# Phát lại MỘT sự cố có thật trên DB sạch để kiểm thử xử lý (v2, sped.md §4).
#   scripts/replay_event.sh EV029 [speed]      (mặc định speed = 20 → 45 giây / mốc 15 phút)
# Dữ liệu KHÔNG dừng khi lên Đỏ: thời hạn xử lý tính theo giờ dữ liệu
#   (Đỏ ≤ 30 phút ≈ 1 phút 30 giây thật ở ×20; Vàng ≤ 60 phút ≈ 3 phút thật).
# Cần: docker compose đang chạy, đã nạp sql/elt.sql.
#   - Mặc định chạy detector / producer bằng Python trên máy (pip install -r detector/requirements.txt).
#   - USE_DOCKER=1: chạy cả hai trong container python:3.12-slim (máy không cài được psycopg).
# Biến tùy chọn: COMPOSE_PROJECT_NAME (tên project docker compose), PYTHON (lệnh python).
# Các bước: reset dữ liệu phát lại → chạy detector (nền) → chạy producer --event → tắt detector khi xong.
set -euo pipefail
cd "$(dirname "$0")/.."

EVENT="${1:?Cách dùng: scripts/replay_event.sh EV029 [speed]}"
SPEED="${2:-20}"
PYTHON="${PYTHON:-python}"
DB_USER="${POSTGRES_USER:-energy}"; DB_PASS="${POSTGRES_PASSWORD:-energy}"; DB_NAME="${POSTGRES_DB:-energy}"

echo "== Reset dữ liệu phát lại"
docker compose exec -T timescaledb psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -q < sql/reset_replay.sql

if [ "${USE_DOCKER:-0}" = "1" ]; then
    NETWORK=$(docker inspect "$(docker compose ps -q timescaledb)" \
              --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}')
    URL="postgresql://${DB_USER}:${DB_PASS}@timescaledb:5432/${DB_NAME}"
    PY_IMAGE="python:3.12-slim"
    HOST_DIR=$(pwd -W 2>/dev/null || pwd)   # Git Bash trên Windows: E:/... cho Docker Desktop
    run_py() {  # run_py <tên container> <lệnh python...>  (chạy nền, tự xóa khi xong)
        local name=$1; shift
        docker rm -f "$name" >/dev/null 2>&1 || true
        MSYS_NO_PATHCONV=1 docker run -d --name "$name" --network "$NETWORK" \
            -v "$HOST_DIR/detector:/app/detector:ro" -v "$HOST_DIR/producer:/app/producer:ro" -v "$HOST_DIR/data:/app/data:ro" \
            -w /app "$PY_IMAGE" sh -c "pip install -q 'psycopg[binary]>=3.1,<4' 2>/dev/null; python -u $*" >/dev/null
    }
    echo "== Chạy detector trong container energy-detector-live"
    run_py energy-detector-live "detector/detector.py --source db --run-id LIVE --poll-seconds 2 --database-url $URL"
    trap 'docker rm -f energy-detector-live >/dev/null 2>&1 || true' EXIT
    sleep 20
    echo "== Phát lại $EVENT ở x$SPEED. Mở http://localhost:8501 để xử lý."
    run_py energy-producer-live "producer/producer.py --event $EVENT --speed $SPEED --dsn $URL"
    docker logs -f energy-producer-live
else
    export DATABASE_URL="${DATABASE_URL:-postgresql://${DB_USER}:${DB_PASS}@localhost:5433/${DB_NAME}}"
    echo "== Chạy detector (log: detector_${EVENT}.log)"
    "$PYTHON" detector/detector.py --source db --run-id LIVE --poll-seconds 2 > "detector_${EVENT}.log" 2>&1 &
    DETECTOR=$!
    trap 'kill $DETECTOR 2>/dev/null || true' EXIT
    sleep 2
    kill -0 $DETECTOR 2>/dev/null || { cat "detector_${EVENT}.log"; exit 1; }
    echo "== Phát lại $EVENT ở x$SPEED. Mở http://localhost:8501 để xử lý."
    "$PYTHON" producer/producer.py --event "$EVENT" --speed "$SPEED"
fi
echo "== Chờ detector xử lý các bản ghi cuối"
sleep 15
