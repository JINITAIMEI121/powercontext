#!/usr/bin/env bash
set -euo pipefail

API="${API:-http://127.0.0.1:18700}"
USER_ID="${USER_ID:-demo}"
OBCLIENT="${OBCLIENT:-obclient}"
SEEKDB_PORT="${SEEKDB_PORT:-2882}"

post() {
  curl -s -m 10 -X POST "$API$1" -H 'Content-Type: application/json' -d "$2"
}

echo "[1/3] remember two notes"
post /remember "{\"user_id\":\"$USER_ID\",\"content\":\"今天在西湖边喝了一杯很好喝的龙井\"}"
echo
post /remember "{\"user_id\":\"$USER_ID\",\"content\":\"周末想去爬山，但天气预报说有雨\"}"
echo

echo "[2/3] recall by keyword"
post /recall "{\"user_id\":\"$USER_ID\",\"query\":\"西湖 龙井\"}"
echo

echo "[3/3] count rows in seekdb"
if command -v "$OBCLIENT" >/dev/null 2>&1; then
  "$OBCLIENT" -h127.0.0.1 -P"$SEEKDB_PORT" -uroot -N -s \
    -e "SELECT COUNT(*) FROM dify_memory.memories WHERE user_id='$USER_ID';"
else
  echo "obclient not found, skipped (API /stats still works)"
  post /stats "{\"user_id\":\"$USER_ID\"}"
  echo
fi

echo "ok"
