#!/usr/bin/env bash
# End-to-end proof that the Dify chatflow really reaches seekdb through the SSRF proxy.
# Any step that fails prints its evidence and exits non-zero.
set -uo pipefail

MEMORY_URL="${MEMORY_URL:-http://127.0.0.1:18700}"
DIFY_BASE="${DIFY_BASE:-http://127.0.0.1:18080}"
DIFY_API_KEY="${DIFY_API_KEY:-}"
SEEKDB_HOST="${SEEKDB_HOST:-127.0.0.1}"
SEEKDB_PORT="${SEEKDB_PORT:-2882}"
API_CONTAINER="${API_CONTAINER:-docker-api-1}"
PROXY_CONTAINER="${PROXY_CONTAINER:-docker-ssrf_proxy-1}"
MEMORY_HOST="${MEMORY_HOST:-host.docker.internal}"
MEMORY_PORT="${MEMORY_PORT:-18700}"

fail() { echo "FAIL: $1"; exit 1; }
ok()   { echo "OK:   $1"; }

# Any of obclient / mysql / python+pymysql works; pick the first available so the
# script is reproducible on machines without the OceanBase client installed.
sql() {
  if command -v obclient >/dev/null 2>&1; then
    obclient -h"$SEEKDB_HOST" -P"$SEEKDB_PORT" -uroot -N -s -e "$1"
  elif command -v mysql >/dev/null 2>&1; then
    mysql -h"$SEEKDB_HOST" -P"$SEEKDB_PORT" -uroot -N -s -e "$1"
  else
    command -v python3 >/dev/null 2>&1 || return 127
    SEEKDB_HOST="$SEEKDB_HOST" SEEKDB_PORT="$SEEKDB_PORT" SEEKDB_SQL="$1" python3 - <<'PY'
import os, pymysql
cur = pymysql.connect(host=os.environ["SEEKDB_HOST"], port=int(os.environ["SEEKDB_PORT"]),
                      user="root", password="", charset="utf8mb4").cursor()
cur.execute(os.environ["SEEKDB_SQL"])
for row in cur.fetchall():
    print("\t".join("" if v is None else str(v) for v in row))
PY
  fi
}

row_count() { sql "select count(*) from dify_memory.memories;"; }

echo "== 1. memory service + seekdb"
health=$(curl -sS -o /dev/null -w '%{http_code}' "$MEMORY_URL/health") || fail "memory service unreachable at $MEMORY_URL"
[ "$health" = "200" ] || fail "GET $MEMORY_URL/health -> $health"
before=$(row_count) || fail "cannot read dify_memory.memories on $SEEKDB_HOST:$SEEKDB_PORT"
[ -n "$before" ] || fail "empty row count from seekdb"
ok "service 200, seekdb rows=$before"

echo "== 2. proxy can resolve the host mapping"
gw=$(docker exec "$PROXY_CONTAINER" getent hosts "$MEMORY_HOST" | awk '{print $1; exit}')
[ -n "$gw" ] || fail "$PROXY_CONTAINER cannot resolve $MEMORY_HOST (missing extra_hosts)"
ok "$MEMORY_HOST -> $gw inside $PROXY_CONTAINER"

echo "== 3. request through squid reaches the host"
# Same method and path the chatflow's HTTP node uses, so this proves the real link.
body='{"user_id":"e2e-verifier","query":"proxy probe"}'
via_proxy=$(docker exec "$API_CONTAINER" sh -lc \
  "curl -sS -o /dev/null -w '%{http_code}' -x http://ssrf_proxy:3128 -H 'Content-Type: application/json' -d '$body' http://$MEMORY_HOST:$MEMORY_PORT/chat") \
  || fail "curl inside $API_CONTAINER errored"
[ "$via_proxy" = "200" ] || {
  docker logs --tail 20 "$PROXY_CONTAINER" 2>&1 | grep -E "$MEMORY_HOST|$MEMORY_PORT" | tail -5
  fail "squid returned $via_proxy (expect 200; TCP_DENIED/403 means SSRF_PROXY_ALLOW_PRIVATE_* is not set)"
}
ok "api -> ssrf_proxy -> POST $MEMORY_HOST:$MEMORY_PORT/chat = 200"

echo "== 4. Dify chatflow, two turns"
[ -n "$DIFY_API_KEY" ] || fail "set DIFY_API_KEY to the published app's API key"
run_turn() {
  curl -sS -X POST "$DIFY_BASE/v1/chat-messages" \
    -H "Authorization: Bearer $DIFY_API_KEY" -H 'Content-Type: application/json' \
    -d "{\"inputs\":{},\"query\":\"$1\",\"response_mode\":\"blocking\",\"conversation_id\":\"$2\",\"user\":\"e2e-verifier\"}"
}
first=$(run_turn "e2e 龙井测试甲" "") || fail "first chat call failed"
conv=$(printf '%s' "$first" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("conversation_id",""))' 2>/dev/null)
[ -n "$conv" ] || fail "no conversation_id in response: ${first:0:200}"
second=$(run_turn "e2e 龙井测试乙" "$conv") || fail "second chat call failed"
answer=$(printf '%s' "$second" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("answer",""))' 2>/dev/null)
printf '%s' "$answer" | grep -q '旧回忆' || fail "second answer does not recall the earlier memory: $answer"
ok "recall present in second answer"

echo "== 5. rows actually landed in seekdb"
after=$(row_count)
[ "${after:-0}" -gt "$before" ] || fail "row count did not grow ($before -> $after)"
latest=$(sql "select content from dify_memory.memories order by id desc limit 1;")
printf '%s' "$latest" | grep -q 'e2e 龙井测试乙' || fail "newest row is not this run's input: $latest"
ok "rows $before -> $after, newest matches this run"

echo "ALL PASS"
