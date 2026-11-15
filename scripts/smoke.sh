#!/usr/bin/env bash
# End-to-end check of a running Compose stack: health, web, and accounts on real Postgres.
set -euo pipefail
API="${API:-http://localhost:${API_PORT:-8000}}"
WEB="${WEB:-http://localhost:${WEB_PORT:-3000}}"

curl -fsS "$API/health" >/dev/null
curl -fsS "$WEB/login" | grep -q "Ask your data a question"
# The web app refuses sign-in posts from other sites (login CSRF).
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$WEB/api/auth/login" \
  -H 'content-type: application/json' -H 'origin: https://evil.example' -d '{}')
test "$code" = 403

email="smoke-$(date +%s)@example.com"
token=$(curl -fsS -X POST "$API/auth/register" -H 'content-type: application/json' \
  -d "{\"email\":\"$email\",\"password\":\"smoke test passphrase\"}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
auth=(-H "Authorization: Bearer $token")

curl -fsS "${auth[@]}" "$API/me" | grep -q "$email"
curl -fsS "${auth[@]}" -X POST "$API/corrections" -H 'content-type: application/json' \
  -d '{"text":"Fiscal year starts in July."}' >/dev/null
curl -fsS "${auth[@]}" "$API/corrections" | grep -q "Fiscal year"
test "$(curl -fsS "${auth[@]}" "$API/history")" = "[]"
# Lakehouse: a CSV into bronze, a silver and a gold model, through ingest, Garage and DuckLake.
wait_run() {
  for _ in $(seq 60); do
    status=$(curl -fsS "${auth[@]}" "$API/lakehouse" | python3 -c 'import json,sys; print(json.load(sys.stdin)["runs"][0]["status"])')
    [ "$status" != running ] && break
    sleep 2
  done
  test "$status" = done
}
printf 'id,amount,status\n1,10.5,paid\n2,4,paid\n3,7.25,refunded\n' |
  curl -fsS "${auth[@]}" -X POST "$API/lakehouse/files?name=orders.csv" \
    -H 'content-type: application/octet-stream' --data-binary @- >/dev/null
wait_run
curl -fsS "${auth[@]}" -X PUT "$API/lakehouse/models" -H 'content-type: application/json' -d '{"models": [
  {"layer": "silver", "name": "paid", "sql": "SELECT id, amount FROM bronze_orders WHERE status = '"'"'paid'"'"'"},
  {"layer": "gold", "name": "revenue", "sql": "SELECT sum(amount) AS total FROM silver_paid"}]}' >/dev/null
curl -fsS "${auth[@]}" -X POST "$API/lakehouse/transform" >/dev/null
wait_run
curl -fsS "${auth[@]}" "$API/lakehouse/tables/gold/revenue" | grep -q '\[\[14.5\]\]'

curl -fsS "${auth[@]}" -X POST "$API/auth/logout" -o /dev/null
code=$(curl -s -o /dev/null -w '%{http_code}' "${auth[@]}" "$API/me")
test "$code" = 401

echo "smoke ok"
