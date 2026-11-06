#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[ "$(id -u)" -eq 0 ] || { echo "请用 sudo bash 运行" >&2; exit 1; }

APP=/opt/familynet-monitor
CFG=/etc/familynet-monitor/config.json
DB=/var/lib/familynet-monitor/monitor.db
PORT=25775
BASE=https://raw.githubusercontent.com/z-q-chen/FamilyNet/2357a1d5995792603c91430209d2619fd5f64b22/ops/monitor
BACK=/var/backups/familynet-monitor/ui-$(date -u +%Y%m%dT%H%M%SZ)
TMP=$(mktemp -d)
CHANGED=0
SUCCESS=0
trap 'rm -rf -- "$TMP"' EXIT

for x in python3 curl systemctl install; do
  command -v "$x" >/dev/null || { echo "缺少 $x" >&2; exit 1; }
done
for x in "$APP/monitor.py" "$APP/index.html" "$CFG" "$DB"; do
  [ -f "$x" ] || { echo "缺少现有监控文件：$x" >&2; exit 1; }
done
systemctl is-active --quiet familynet-monitor.service || {
  echo "现有监控服务未运行；拒绝更新" >&2; exit 1;
}
curl --noproxy '*' -fsS --max-time 5 "http://127.0.0.1:$PORT/healthz" \
  >/dev/null || { echo "监控端口 $PORT 未响应；拒绝更新" >&2; exit 1; }

rollback(){
  local status=$?
  trap - ERR
  if [ "$CHANGED" -eq 1 ] && [ "$SUCCESS" -eq 0 ]; then
    cp -p "$BACK/monitor.py" "$APP/monitor.py"
    cp -p "$BACK/index.html" "$APP/index.html"
    systemctl restart familynet-monitor.service || true
    echo "已恢复此前网页和监控程序。" >&2
  fi
  echo "升级未完成。备份：$BACK" >&2
  exit "$status"
}
trap rollback ERR

echo "下载新版白色卡片界面..."
curl -fsSL --retry 3 "$BASE/monitor.py" -o "$TMP/monitor.py"
curl -fsSL --retry 3 "$BASE/index.html" -o "$TMP/index.html"
python3 -m py_compile "$TMP/monitor.py"
python3 - "$TMP/index.html" <<'PY'
from pathlib import Path
import sys
s=Path(sys.argv[1]).read_text(encoding="utf-8")
for name in ("江苏", "河北", "IPv4", "IPv6", "id=\"pinggrid\"",
             "id=\"auditGate\"", "id=\"quotaValue\"", "id=\"totalUp\"",
             "id=\"totalDown\"", "id=\"quotaRemaining\""):
    assert name in s, name
assert "new Array(48).fill(null)" in s
PY

mkdir -p "$BACK"
cp -p "$APP/monitor.py" "$BACK/monitor.py"
cp -p "$APP/index.html" "$BACK/index.html"
cp -p "$CFG" "$BACK/config.json"

# Live SQLite backup; preserves traffic/pings and the current billing cycle.
python3 - "$DB" "$BACK/monitor.db" <<'PY'
import sqlite3,sys
with sqlite3.connect(sys.argv[1],timeout=20) as src:
    with sqlite3.connect(sys.argv[2],timeout=20) as dst:
        src.backup(dst)
PY
CHANGED=1
install -m 0755 "$TMP/monitor.py" "$APP/monitor.py"
install -m 0644 "$TMP/index.html" "$APP/index.html"
systemctl restart familynet-monitor.service

READY=0
for _ in $(seq 1 25); do
  if curl --noproxy '*' -fsS --max-time 4 "http://127.0.0.1:$PORT/api/state" \
      | python3 -c 'import json,sys; v=json.load(sys.stdin); assert v["quota"]>0 and "cycle_up" in v and "cycle_down" in v' 2>/dev/null \
    && curl --noproxy '*' -fsS --max-time 4 "http://127.0.0.1:$PORT/api/pings" \
      | python3 -c 'import json,sys; x=json.load(sys.stdin); assert len(x)==12 and all("history" in t for t in x)' 2>/dev/null; then
    READY=1
    break
  fi
  sleep 1
done
if [ "$READY" -ne 1 ]; then
  echo "新监控检查失败，恢复旧版..." >&2
  journalctl -u familynet-monitor.service -n 20 --no-pager || true
  false
fi
AUDIT_CODE=$(curl --noproxy '*' -sS --max-time 4 \
  -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/flows")
[ "$AUDIT_CODE" = 401 ] || { echo "流量分析未受密码保护" >&2; false; }

SUCCESS=1
trap - ERR
echo "更新完成：https://207.57.74.12:9443"
echo "12 组线路：江苏左、河北右 / IPv4 上、IPv6 下 / 电信-联通-移动"
echo "48 格延迟与 TCP 探测失败率上下对齐"
echo "原有周期流量、配置及监控数据库均已保留"
echo "备份：$BACK"
