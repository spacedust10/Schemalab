#!/usr/bin/env bash
# End-to-end check of a running Compose stack: health, web, and accounts on real Postgres.
set -euo pipefail
API="${API:-http://localhost:${API_PORT:-8000}}"
WEB="${WEB:-http://localhost:${WEB_PORT:-3000}}"

curl -fsS "$API/health" >/dev/null
curl -fsS "$WEB" | grep -q "API ok"

email="smoke-$(date +%s)@example.com"
token=$(curl -fsS -X POST "$API/auth/register" -H 'content-type: application/json' \
  -d "{\"email\":\"$email\",\"password\":\"smoke test passphrase\"}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
auth=(-H "Authorization: Bearer $token")

curl -fsS "${auth[@]}" "$API/me" | grep -q "$email"
curl -fsS "${auth[@]}" -X POST "$API/corrections" -H 'content-type: application/json' \
  -d '{"text":"Fiscal year starts in July."}' >/dev/null
curl -fsS "${auth[@]}" "$API/corrections" | grep -q "Fiscal year"
test "$(curl -fsS "${auth[@]}" "$API/history")" = "[]"
curl -fsS "${auth[@]}" -X POST "$API/auth/logout" -o /dev/null
code=$(curl -s -o /dev/null -w '%{http_code}' "${auth[@]}" "$API/me")
test "$code" = 401

echo "smoke ok"
