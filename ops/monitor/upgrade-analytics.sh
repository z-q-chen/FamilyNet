#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

[ "$(id -u)" = 0 ] || { echo "请用 sudo bash 运行" >&2; exit 1; }
APP=/opt/familynet-monitor
CFG=/etc/familynet-monitor/config.json
DB=/var/lib/familynet-monitor/monitor.db
PORT=25775
HOST=207.57.74.12
BASE=https://raw.githubusercontent.com/z-q-chen/FamilyNet/c9fdbc976be8008f79fb7feb4de9f14a1220a0bf/ops/monitor
BACK=/var/backups/familynet-monitor/analytics-$(date -u +%Y%m%dT%H%M%SZ)
TMP=$(mktemp -d)
CHANGED=0
FINISHED=0
trap 'rm -rf -- "$TMP"' EXIT

for cmd in curl python3 systemctl; do
  command -v "$cmd" >/dev/null || { echo "缺少 $cmd" >&2; exit 1; }
done
for file in "$APP/monitor.py" "$APP/index.html" "$CFG" "$DB"; do
  [ -f "$file" ] || { echo "没有找到现有监控文件：$file" >&2; exit 1; }
done
systemctl is-active --quiet familynet-monitor.service || {
  echo "监控服务未运行，终止更新" >&2
  exit 1
}
curl --noproxy '*' -fsS --max-time 5 \
  "http://127.0.0.1:$PORT/healthz" >/dev/null || {
  echo "监控 $PORT 未响应，终止更新" >&2
  exit 1
}

mkdir -p "$BACK"
cp -p "$APP/monitor.py" "$BACK/monitor.py"
cp -p "$APP/index.html" "$BACK/index.html"
cp -p "$CFG" "$BACK/config.json"
python3 - "$DB" "$BACK/monitor.db" <<'PY'
import sqlite3,sys
with sqlite3.connect(sys.argv[1],timeout=20) as src:
    with sqlite3.connect(sys.argv[2],timeout=20) as dst:
        src.backup(dst)
PY

rollback(){
  local code=$?
  trap - ERR
  if [ "$CHANGED" = 1 ] && [ "$FINISHED" = 0 ]; then
    cp -p "$BACK/monitor.py" "$APP/monitor.py"
    cp -p "$BACK/index.html" "$APP/index.html"
    systemctl restart familynet-monitor.service || true
    echo "已恢复此前的监控程序和网页" >&2
  fi
  echo "备份：$BACK" >&2
  exit "$code"
}
trap rollback ERR

curl -fsSL --retry 3 "$BASE/monitor.py" -o "$TMP/monitor.py"
curl -fsSL --retry 3 "$BASE/index.html" -o "$TMP/index.html"
python3 -m py_compile "$TMP/monitor.py"
python3 - "$TMP/index.html" <<'PY'
from pathlib import Path
import sys
html=Path(sys.argv[1]).read_text(encoding="utf-8")
for token in (
    'id="serviceRows"', 'id="selectedPeriod"', 'id="flowRows"',
    "function selectBucket(", "audit=rank&start=", 'id="auditGate"',
):
    assert token in html,token
PY

# Enable kernel accounting for new connections when available.
# Existing established conntrack entries cannot acquire retroactive counters.
if [ -w /proc/sys/net/netfilter/nf_conntrack_acct ]; then
  echo 1 >/proc/sys/net/netfilter/nf_conntrack_acct || true
fi

CHANGED=1
install -m 0755 "$TMP/monitor.py" "$APP/monitor.py"
install -m 0644 "$TMP/index.html" "$APP/index.html"
systemctl restart familynet-monitor.service

READY=0
for attempt in $(seq 1 25); do
  if curl --noproxy '*' -fsS --max-time 4 \
       "http://127.0.0.1:$PORT/api/state" |
       python3 -c 'import json,sys; s=json.load(sys.stdin); assert "cycle_up" in s and s["quota"]>0' 2>/dev/null &&
     curl --noproxy '*' -fsS --max-time 4 \
       "http://127.0.0.1:$PORT/api/pings" |
       python3 -c 'import json,sys; assert len(json.load(sys.stdin))==12' 2>/dev/null; then
    READY=1
    break
  fi
  sleep 1
done
[ "$READY" = 1 ] || { echo "新监控健康检查失败" >&2; false; }

AUTH_LOCAL=$(curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' \
  --max-time 6 "http://127.0.0.1:$PORT/api/state?audit=rank&start=0&end=1")
[ "$AUTH_LOCAL" = 401 ] || {
  echo "本机排行权限检查异常：$AUTH_LOCAL" >&2
  false
}
AUTH_HTTPS=$(curl --noproxy '*' --insecure -sS -o /dev/null -w '%{http_code}' \
  --max-time 7 --resolve "$HOST:9443:127.0.0.1" \
  "https://$HOST:9443/api/state?audit=rank&start=0&end=1")
[ "$AUTH_HTTPS" = 401 ] || {
  echo "9443 排行权限检查异常：$AUTH_HTTPS" >&2
  false
}

FINISHED=1
trap - ERR
echo "已完成：https://$HOST:9443"
echo "点击流量趋势可查看对应时间段的应用、域名及目的 IP 排行"
echo "不会修改 Caddy、监控密码、原有流量数据"
echo "备份：$BACK"
