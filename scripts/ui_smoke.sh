#!/usr/bin/env bash
# Playwright 截图回归：复制当前库 → 临时端口 8021 → 截图/交互断言 → 关闭。
# 用法：scripts/ui_smoke.sh [输出目录]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-/tmp/oms-ui-smoke}"
TMPDB="/tmp/oms-ui-smoke.db"
PORT="${UI_SMOKE_PORT:-8021}"
PWPY="${PLAYWRIGHT_PY:-$HOME/playwright-venv/bin/python}"
USER="${OMS_USER:-admin}"
PASS="${OMS_PASS:-admin}"
BASELINE="${UI_BASELINE_DIR:-$HOME/oms-ui-screenshots/baseline}"
EXTRA=()
[ "${UI_UPDATE_BASELINE:-0}" = "1" ] && EXTRA+=(--update-baseline)

if [ ! -x "$PWPY" ]; then
  echo "未找到 Playwright Python：$PWPY（可设置 PLAYWRIGHT_PY）" >&2
  exit 2
fi

sqlite3 "$ROOT/data/ontology.db" ".backup $TMPDB"
rm -rf "$OUT"; mkdir -p "$OUT"

cd "$ROOT"
setsid env DB_URL="sqlite:///$TMPDB" SECRET_KEY="ui-smoke-secret" \
  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" \
  > "$OUT/server.log" 2>&1 < /dev/null &
SRV=$!
cleanup() { kill "$SRV" 2>/dev/null || true; wait "$SRV" 2>/dev/null || true; }
trap cleanup EXIT

for i in $(seq 1 30); do
  code=$(curl -s -m 2 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/login" || true)
  [ "$code" = "200" ] && break
  sleep 1
done
if [ "${code:-}" != "200" ]; then
  echo "临时服务未启动，日志：" >&2
  tail -30 "$OUT/server.log" >&2
  exit 3
fi

set +e
"$PWPY" "$ROOT/scripts/ui_smoke.py" \
  --base-url "http://127.0.0.1:$PORT" --user "$USER" --pass "$PASS" --out "$OUT" \
  --baseline-dir "$BASELINE" "${EXTRA[@]}"
rc=$?
set -e
"$PWPY" "$ROOT/scripts/ui_audit.py" \
  --base-url "http://127.0.0.1:$PORT" --user "$USER" --pass "$PASS" --out "$OUT"
arc=$?
mkdir -p "$HOME/oms-ui-screenshots"
rm -rf "$HOME/oms-ui-screenshots/current"
cp -r "$OUT" "$HOME/oms-ui-screenshots/current"
echo "截图目录：$OUT"
echo "基线目录：$BASELINE"
if [ "$rc" -ne 0 ] || [ "$arc" -ne 0 ]; then exit 1; fi
exit 0
