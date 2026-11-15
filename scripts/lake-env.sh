#!/usr/bin/env bash
# Puts random lakehouse secrets in .env, once. Existing values are kept, so it is safe to rerun.
set -euo pipefail
cd "$(dirname "$0")/.."
touch .env
chmod 600 .env
add() { grep -q "^$1=" .env || echo "$1=$2" >> .env; }
add GARAGE_RPC_SECRET "$(openssl rand -hex 32)"
add LAKE_S3_KEY "GK$(openssl rand -hex 12)"   # Garage key ids are GK + 24 hex digits
add LAKE_S3_SECRET "$(openssl rand -hex 32)"
add LAKE_DB_PASSWORD "$(openssl rand -hex 24)"
add INGEST_TOKEN "$(openssl rand -hex 32)"
echo "lakehouse secrets are in .env"
