#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

[ "$(id -u)" -eq 0 ] || { echo "请使用 sudo bash" >&2; exit 1; }
for x in python3 curl systemctl tar; do
  command -v "$x" >/dev/null || { echo "缺少命令: $x" >&2; exit 1; }
done

APP=/opt/familynet-monitor
ETC=/etc/familynet-monitor
DATA=/var/lib/familynet-monitor
UNIT=/etc/systemd/system/familynet-monitor.service
CRED=/root/familynet-monitor-credentials.txt
BACK=/var/backups/familynet-monitor/$(date -u +%Y%m%dT%H%M%SZ)
BASE=https://raw.githubusercontent.com/z-q-chen/FamilyNet/0676f5d0d32d60e15d2e080a591ab8d4005fb80f/ops/monitor
PUBLIC_IP=207.57.74.12
STAGING_PORT=25775
LIVE_PORT=25774
CUTOVER=0
SUCCESS=0
ALREADY_MIGRATED=0
TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT

local_http(){
  curl --noproxy '*' -fsS --max-time 7 "http://127.0.0.1:$1$2"
}
live_health(){
  curl --noproxy '*' -ksSf --connect-timeout 3 --max-time 8 \
    --resolve "$PUBLIC_IP:9443:127.0.0.1" \
    "https://$PUBLIC_IP:9443/healthz" \
    | python3 -c 'import json,sys; assert json.load(sys.stdin)["status"]=="ok"'
}
write_unit(){
  local port="$1"
  cat >"$UNIT" <<EOF
[Unit]
Description=FamilyNet VPS Monitor
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP
Environment=FAMILYNET_MONITOR_PORT=$port
ExecStart=/usr/bin/python3 -u $APP/monitor.py
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=$DATA
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
CapabilityBoundingSet=CAP_NET_ADMIN
AmbientCapabilities=CAP_NET_ADMIN

[Install]
WantedBy=multi-user.target
EOF
  chmod 644 "$UNIT"
  systemctl daemon-reload
}
rollback(){
  local status=$?
  trap - ERR
  if [ "$CUTOVER" -eq 1 ] && [ "$SUCCESS" -eq 0 ]; then
    echo "切换未通过验证，恢复 Komari..."
    write_unit "$STAGING_PORT" || true
    systemctl restart familynet-monitor.service || true
    systemctl start komari.service || true
    systemctl start komari-agent.service || true
    echo "Komari 已尝试恢复。"
  fi
  echo "安装未完成；没有删除 Komari。备份目录：$BACK" >&2
  exit "$status"
}
trap rollback ERR

# The FamilyNet subscription service owns Caddy and 9443. Never edit/reload it.
if [ ! -x /usr/local/lib/familynet/caddy ]; then
  echo "FamilyNet 自带 Caddy 未找到，拒绝切换。"
  exit 1
fi
if ! systemctl is-active --quiet familynet-subscription.service; then
  echo "FamilyNet 订阅 HTTPS 服务未运行，拒绝切换。"
  exit 1
fi

# Detect a previous successful port takeover (safe reinstall/upgrade).
if local_http "$LIVE_PORT" /healthz 2>/dev/null | grep -q '"status":"ok"'; then
  ALREADY_MIGRATED=1
elif ! systemctl is-active --quiet komari.service; then
  echo "25774 不是已运行的 Komari，且不存在已运行的 FamilyNet Monitor；拒绝切换。"
  exit 1
fi

mkdir -p "$APP" "$ETC" "$DATA" "$BACK"
echo "下载新版监控..."
curl -fsSL --retry 3 "$BASE/monitor.py" -o "$TMP/monitor.py"
curl -fsSL --retry 3 "$BASE/index.html" -o "$TMP/index.html"
python3 -m py_compile "$TMP/monitor.py"
test -s "$TMP/index.html"
install -m 0755 "$TMP/monitor.py" "$APP/monitor.py"
install -m 0644 "$TMP/index.html" "$APP/index.html"

if [ ! -f "$ETC/config.json" ]; then
  PASS=$(python3 -c 'import secrets;print(secrets.token_urlsafe(18))')
  python3 "$APP/monitor.py" --init-password "$PASS"
  printf '地址: https://%s:9443\n流量审计账号: admin\n流量审计密码: %s\n' "$PUBLIC_IP" "$PASS" > "$CRED"
  chmod 600 "$CRED"
fi

# Preserve original Komari settings and unit definitions before touching services.
cp -a "$ETC/config.json" "$BACK/monitor-config.json.before"
for service in komari.service komari-agent.service; do
  systemctl cat "$service" >"$BACK/$service.txt" 2>/dev/null || true
done

# Install conntrack helpers opportunistically; network or package errors
# must not prevent the monitor from serving normal metrics.
if ! command -v conntrack >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
  echo "安装连接审计工具..."
  (DEBIAN_FRONTEND=noninteractive apt-get update -qq &&
   DEBIAN_FRONTEND=noninteractive apt-get install -y -qq conntrack) ||
   echo "连接审计工具暂不可用；其他监控照常运行。"
fi
if [ -w /proc/sys/net/netfilter/nf_conntrack_acct ]; then
  if echo 1 >/proc/sys/net/netfilter/nf_conntrack_acct; then
    mkdir -p /etc/sysctl.d
    printf 'net.netfilter.nf_conntrack_acct = 1\n' \
      > /etc/sysctl.d/91-familynet-monitor.conf
  fi
fi

if [ "$ALREADY_MIGRATED" -eq 1 ]; then
  write_unit "$LIVE_PORT"
  systemctl enable --now familynet-monitor.service >/dev/null
  systemctl restart familynet-monitor.service
  for _ in $(seq 1 25); do
    if local_http "$LIVE_PORT" /healthz >/dev/null 2>&1; then break; fi
    sleep 1
  done
  live_health
  SUCCESS=1
  trap - ERR
  echo "新版监控已更新，Caddy 与旧数据均未改动。"
else
  # Keep Komari serving 9443 while validating new monitor on a staging port.
  write_unit "$STAGING_PORT"
  systemctl enable --now familynet-monitor.service >/dev/null
  systemctl restart familynet-monitor.service
  for _ in $(seq 1 25); do
    if local_http "$STAGING_PORT" /healthz >/dev/null 2>&1; then break; fi
    sleep 1
  done
  local_http "$STAGING_PORT" /api/state |
    python3 -c 'import json,sys;d=json.load(sys.stdin);assert "uptime" in d and "quota" in d'
  local_http "$STAGING_PORT" /api/pings |
    python3 -c 'import json,sys;d=json.load(sys.stdin);assert len(d)==12'
  AUTH_CODE=$(curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' \
    "http://127.0.0.1:$STAGING_PORT/api/flows")
  [ "$AUTH_CODE" = "401" ] || { echo "审计接口未受保护" >&2; exit 1; }

  # The old agent's cumulative totals are the starting point, not zero.
  # Also imports 24h of old colored ping history when the RPC exposes it.
  python3 "$APP/monitor.py" --import-komari
  python3 "$APP/monitor.py" --check | python3 -c \
    'import json,sys; d=json.load(sys.stdin); assert d["up_total"]+d["down_total"]>0'

  # Backup current Komari DBs including WAL. Full archive follows after stop.
  if [ -f /var/lib/komari/komari.db ]; then
    python3 - "$BACK" <<'PY'
from pathlib import Path
import sqlite3,sys
b=Path(sys.argv[1])
for source,dest in (
    (Path("/var/lib/komari/komari.db"),b/"komari-db.sqlite"),
    (Path("/var/lib/komari/data/metrics.db"),b/"komari-metrics.sqlite"),
):
    if source.is_file():
        with sqlite3.connect(str(source),timeout=25) as src:
            with sqlite3.connect(str(dest)) as target:
                src.backup(target)
PY
  fi

  echo "接管原监控端口..."
  CUTOVER=1
  systemctl stop komari-agent.service
  systemctl stop komari.service
  # The old files are immutable while Komari is stopped: archive them
  # before any destructive operation. If archiving fails, roll back.
  tar -C /var/lib -czf "$BACK/komari-data.tar.gz" komari
  tar -tzf "$BACK/komari-data.tar.gz" >/dev/null
  write_unit "$LIVE_PORT"
  systemctl restart familynet-monitor.service
  for _ in $(seq 1 25); do
    if local_http "$LIVE_PORT" /healthz >/dev/null 2>&1; then break; fi
    sleep 1
  done
  local_http "$LIVE_PORT" /api/state |
    python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["up_total"]+d["down_total"]>0'
  live_health
  SUCCESS=1
  trap - ERR

  # The public 9443 panel now serves the new monitor. Preserve all backup
  # archives, but clean obsolete Komari process files once it is verified.
  systemctl disable komari-agent.service komari.service >/dev/null 2>&1 || true
  if [ -d /opt/komari ]; then
    if tar -C /opt -czf "$BACK/komari-opt.tar.gz" komari &&
       tar -tzf "$BACK/komari-opt.tar.gz" >/dev/null; then
      rm -rf -- /opt/komari
    fi
  fi
  rm -rf -- /var/lib/komari
  for bin in /usr/local/bin/komari /usr/local/bin/komari-agent; do
    if [ -f "$bin" ]; then
      cp -a "$bin" "$BACK/$(basename "$bin").binary"
      rm -f -- "$bin"
    fi
  done
  for service in komari.service komari-agent.service; do
    rm -f -- "/etc/systemd/system/$service"
  done
  systemctl daemon-reload
  echo "原 Komari 已备份并清理。"
fi

echo
echo "已完成： https://$PUBLIC_IP:9443"
if [ -f "$CRED" ]; then
  echo "流量审计凭据："
  cat "$CRED"
fi
echo "Komari 备份：$BACK"
echo "监控状态：systemctl status familynet-monitor --no-pager"
