#!/usr/bin/env bash
# Phát lại MỘT sự cố có thật trên DB sạch để kiểm thử tiếp nhận và xử lý.
#   scripts/replay_event.sh EV029 [speed]      (mặc định speed = 60 → 15 giây / mốc 15 phút)
# Cần: docker compose đang chạy, đã nạp sql/elt.sql, Python có psycopg (pip install -r detector/requirements.txt).
# Các bước: reset dữ liệu phát lại → chạy detector (nền) → chạy producer --event → tắt detector khi xong.
set -euo pipefail
cd "$(dirname "$0")/.."

EVENT="${1:?Cách dùng: scripts/replay_event.sh EV029 [speed]}"
SPEED="${2:-60}"
PYTHON="${PYTHON:-python}"
export DATABASE_URL="${DATABASE_URL:-postgresql://${POSTGRES_USER:-energy}:${POSTGRES_PASSWORD:-energy}@localhost:5433/${POSTGRES_DB:-energy}}"

RUNNING=$(pgrep -f "detector/detector.py --source db|producer/producer.py --event" || true)
if [ -n "$RUNNING" ]; then
    echo "Đang có detector / producer của lần phát trước:" >&2
    ps -o pid=,stat=,args= -p $(echo $RUNNING | tr ' ' ',') | sed 's/^/  /' >&2
    echo "(STAT 'T' = đang bị treo do Ctrl+Z, chưa tắt.) Tắt rồi chạy lại script:" >&2
    echo "  kill -CONT $(echo $RUNNING) && kill $(echo $RUNNING)" >&2
    echo "Lưu ý: dừng script bằng Ctrl+C, không dùng Ctrl+Z." >&2
    exit 1
fi

echo "== Reset dữ liệu phát lại"
docker compose exec -T timescaledb psql -U "${POSTGRES_USER:-energy}" -d "${POSTGRES_DB:-energy}" \
    -v ON_ERROR_STOP=1 -q < sql/reset_replay.sql

echo "== Chạy detector (log: detector_${EVENT}.log)"
"$PYTHON" detector/detector.py --source db --method B --run-id LIVE --poll-seconds 2 > "detector_${EVENT}.log" 2>&1 &
DETECTOR=$!
trap 'kill $DETECTOR 2>/dev/null || true' EXIT
sleep 2
kill -0 $DETECTOR 2>/dev/null || { cat "detector_${EVENT}.log"; exit 1; }

echo "== Phát lại $EVENT ở x$SPEED. Mở http://localhost:8501 để tiếp nhận và xử lý."
"$PYTHON" producer/producer.py --event "$EVENT" --speed "$SPEED"
echo "== Chờ detector xử lý các bản ghi cuối"
sleep 15
