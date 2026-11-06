#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

[ "$(id -u)" -eq 0 ] || { echo "请用 sudo bash 执行" >&2; exit 1; }
for tool in curl python3 systemctl; do
  command -v "$tool" >/dev/null || { echo "缺少 $tool" >&2; exit 1; }
done

APP=/opt/familynet-monitor
CFG=/etc/familynet-monitor/config.json
UNIT=/etc/systemd/system/familynet-monitor.service
BACK=/var/backups/familynet-monitor/upgrade-$(date -u +%Y%m%dT%H%M%SZ)
BASE=https://raw.githubusercontent.com/z-q-chen/FamilyNet/1002665ae2bfdcc7638d19ff8fd1b01d4fb03a93/ops/monitor
TMP=$(mktemp -d)
CHANGED=0
SUCCESS=0
trap 'rm -rf "$TMP"' EXIT

[ -f "$APP/monitor.py" ] && [ -f "$APP/index.html" ] && \
  [ -f "$CFG" ] && [ -f "$UNIT" ] || {
  echo "未检测到完整的现有 FamilyNet Monitor，未修改任何文件。" >&2
  exit 1
}
if ! systemctl is-active --quiet familynet-monitor.service; then
  echo "FamilyNet Monitor 当前未运行，拒绝覆盖旧文件。" >&2
  exit 1
fi
if ! curl --noproxy '*' -fsS --max-time 5 \
  http://127.0.0.1:25775/healthz >/dev/null; then
  echo "25775 监控未响应，拒绝更新。" >&2
  exit 1
fi

mkdir -p "$BACK"
cp -p "$APP/monitor.py" "$BACK/monitor.py"
cp -p "$APP/index.html" "$BACK/index.html"
cp -p "$CFG" "$BACK/config.json"
cp -p "$UNIT" "$BACK/familynet-monitor.service"

rollback(){
  local error=$?
  trap - ERR
  if [ "$CHANGED" = 1 ] && [ "$SUCCESS" != 1 ]; then
    cp -p "$BACK/monitor.py" "$APP/monitor.py"
    cp -p "$BACK/index.html" "$APP/index.html"
    cp -p "$BACK/config.json" "$CFG"
    systemctl restart familynet-monitor.service || true
  fi
  echo "更新未完成。原监控文件已保留，备份：$BACK" >&2
  exit "$error"
}
trap rollback ERR

curl --fail --silent --show-error --location --retry 3 \
  "$BASE/monitor.py" -o "$TMP/monitor.py"
curl --fail --silent --show-error --location --retry 3 \
  "$BASE/index.html" -o "$TMP/index.html"
python3 -m py_compile "$TMP/monitor.py"
python3 - "$TMP/index.html" <<'PY'
from pathlib import Path
import sys
html=Path(sys.argv[1]).read_text()
for marker in ('id="quotaValue"', 'id="totalUp"', 'id="totalDown"', 'id="auditGate"'):
    assert marker in html, marker
assert "fmt(s.cycle_up)" in html and "fmt(s.cycle_down)" in html
PY

CHANGED=1
install -m 0755 "$TMP/monitor.py" "$APP/monitor.py"
install -m 0644 "$TMP/index.html" "$APP/index.html"

# Initialize the period once outside the service's read-only /etc sandbox.
python3 "$APP/monitor.py" --check >"$TMP/state.json"
python3 - "$TMP/state.json" "$CFG" <<'PY'
import json,sys,time
state=json.load(open(sys.argv[1]))
cfg=json.load(open(sys.argv[2]))
assert state["billing_source"]=="first_boot_then_monthly"
assert 1<=int(cfg["reset_day"])<=31
assert int(cfg["first_reset_at"])>0
assert state["cycle_up"]>=0 and state["cycle_down"]>=0
assert state["quota"]>0
PY

systemctl restart familynet-monitor.service
READY=0
for _ in $(seq 1 20); do
  if curl --noproxy '*' -fsS --max-time 3 \
     http://127.0.0.1:25775/api/state | \
     python3 -c 'import json,sys; d=json.load(sys.stdin); assert "cycle_up" in d and "cycle_down" in d and "used" in d' 2>/dev/null; then
    READY=1
    break
  fi
  sleep 1
done
[ "$READY" = 1 ] || { echo "更新后监控未恢复。" >&2; exit 1; }

AUDIT_HTTP=$(curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' \
    --max-time 5 http://127.0.0.1:25775/api/flows)
[ "$AUDIT_HTTP" = 401 ] || { echo "流量分析鉴权检查失败。" >&2; exit 1; }

SUCCESS=1
trap - ERR
echo "更新完成：https://207.57.74.12:9443"
echo "本周期已用流量："
curl --noproxy '*' -fsS --max-time 5 \
    http://127.0.0.1:25775/api/state | \
    python3 -c 'import json,sys,datetime; s=json.load(sys.stdin); print("上传 %.2f GiB / 下载 %.2f GiB / 合计 %.2f GiB"%(s["cycle_up"]/1024**3,s["cycle_down"]/1024**3,s["used"]/1024**3)); print("下次重置："+datetime.datetime.fromtimestamp(s["next_reset_at"],datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))'
echo "备份：$BACK"
