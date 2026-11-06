#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

[ "$(id -u)" = 0 ] || { echo "请用 sudo bash 运行" >&2; exit 1; }
APP=/opt/familynet-monitor
CONF=/etc/familynet-monitor/config.json
SERVICE=familynet-monitor.service
BACK=/var/backups/familynet-monitor/fix-audit-$(date -u +%Y%m%dT%H%M%SZ)
BASE=https://raw.githubusercontent.com/z-q-chen/FamilyNet/7510eb42bc9c75c982cce1c5d198438a08e02d07/ops/monitor
TMP=$(mktemp -d)
CHANGED=0
DONE=0
trap 'rm -rf -- "$TMP"' EXIT

for program in curl python3 systemctl install; do
  command -v "$program" >/dev/null || { echo "缺少 $program" >&2; exit 1; }
done
for file in "$APP/monitor.py" "$APP/index.html" "$CONF"; do
  [ -f "$file" ] || { echo "原监控文件不存在：$file" >&2; exit 1; }
done
systemctl is-active --quiet "$SERVICE" || {
  echo "监控服务未运行，停止修改。" >&2
  exit 1
}
curl --noproxy '*' -fsS --max-time 6 http://127.0.0.1:25775/api/state \
  | python3 -c 'import json,sys;s=json.load(sys.stdin);assert "cycle_up" in s and "quota" in s' || {
    echo "原监控 API 不可用，停止修改。" >&2
    exit 1
  }

mkdir -p "$BACK"
cp -p "$APP/monitor.py" "$BACK/monitor.py"
cp -p "$APP/index.html" "$BACK/index.html"
cp -p "$CONF" "$BACK/config.json"

rollback(){
  local code=$?
  trap - ERR
  if [ "$CHANGED" -eq 1 ] && [ "$DONE" -eq 0 ]; then
    cp -p "$BACK/monitor.py" "$APP/monitor.py"
    cp -p "$BACK/index.html" "$APP/index.html"
    cp -p "$BACK/config.json" "$CONF"
    systemctl restart "$SERVICE" || true
  fi
  echo "修复失败；已尝试恢复此前程序和密码配置。备份：$BACK" >&2
  exit "$code"
}
trap rollback ERR

curl -fsSL --retry 3 "$BASE/monitor.py" -o "$TMP/monitor.py"
curl -fsSL --retry 3 "$BASE/index.html" -o "$TMP/index.html"
curl -fsSL --retry 3 "$BASE/repair-auth.py" -o "$TMP/repair-auth.py"
python3 -m py_compile "$TMP/monitor.py" "$TMP/repair-auth.py"
python3 - "$TMP/index.html" <<'PY'
from pathlib import Path
import sys
s=Path(sys.argv[1]).read_text()
for token in (
    'api("/api/state?audit=auth",true)',
    'api("/api/state?audit=history&hours="+hours,true)',
    'api("/api/state?audit=flows&hours="+hours,true)',
):
    assert token in s, token
PY

CHANGED=1
install -m 0755 "$TMP/monitor.py" "$APP/monitor.py"
install -m 0644 "$TMP/index.html" "$APP/index.html"
systemctl restart "$SERVICE"
for attempt in $(seq 1 15); do
  if curl --noproxy '*' -fsS --max-time 3 http://127.0.0.1:25775/api/state \
    | python3 -c 'import json,sys;x=json.load(sys.stdin);assert "used" in x and "cycle_up" in x' 2>/dev/null; then
    break
  fi
  sleep 1
done

# The new private route must reject unauthenticated visitors.
STATUS=$(curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 5 \
  'http://127.0.0.1:25775/api/state?audit=flows')
[ "$STATUS" = 401 ] || {
  echo "私有流量接口的访问控制未通过校验。" >&2
  false
}

# This generates a fresh password, then verifies it locally and through
# the actual HTTPS listener before saving it as the new credential.
python3 "$TMP/repair-auth.py"
DONE=1
trap - ERR
echo
echo "流量分析已修复，请刷新 https://207.57.74.12:9443 后输入上面的新密码。"
echo "监控流量和历史数据不变；Caddy 未修改。"
echo "程序备份：$BACK"
