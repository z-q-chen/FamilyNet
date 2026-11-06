#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

if [ "$(id -u)" -ne 0 ]; then
  echo "请以 root 身份运行（sudo bash）" >&2
  exit 1
fi
for cmd in python3 curl systemctl tar grep; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "缺少命令: $cmd" >&2
    exit 1
  fi
done
# FamilyNet bundles Caddy outside PATH and manages it with a shared service.
CADDY_BIN=/usr/local/lib/familynet/caddy
CADDY=/etc/familynet-subscription/Caddyfile
CADDY_SERVICE=familynet-subscription.service
if [ ! -x "$CADDY_BIN" ] || [ ! -f "$CADDY" ]; then
  echo "未发现 FamilyNet 内置 Caddy 或订阅 Caddyfile，原服务保持不变。" >&2
  exit 1
fi
if ! systemctl is-active --quiet "$CADDY_SERVICE"; then
  echo "FamilyNet HTTPS 服务没有运行，拒绝更改 9443。" >&2
  exit 1
fi

APP=/opt/familynet-monitor
ETC=/etc/familynet-monitor
DATA=/var/lib/familynet-monitor
BACK=/var/backups/familynet-monitor/$(date -u +%Y%m%dT%H%M%SZ)
UNIT=/etc/systemd/system/familynet-monitor.service
BASE=https://raw.githubusercontent.com/z-q-chen/FamilyNet/125741f2fc602bfee8bc6cc3a27c873f7068ae51/ops/monitor
TARGET=127.0.0.1:25775
CRED=/root/familynet-monitor-credentials.txt
CUTOVER=0
FINISHED=0

if [ ! -f "$CADDY" ]; then
  echo "Caddyfile 不存在；未修改旧监控。" >&2
  exit 1
fi
if [ ! -f /var/lib/komari/komari.db ] && [ ! -f "$ETC/config.json" ]; then
  echo "未发现 Komari 数据库，无法迁移配置；未执行清理。" >&2
  exit 1
fi
mkdir -p "$APP" "$ETC" "$DATA" "$BACK"
cp -p "$CADDY" "$BACK/Caddyfile.before"
if [ -f "$ETC/config.json" ]; then
  cp -p "$ETC/config.json" "$BACK/monitor-config.before.json"
fi
for unit in komari komari-agent komari-server; do
  systemctl cat "$unit" >"$BACK/$unit.unit" 2>/dev/null || true
done

reload_familynet_caddy(){
  # Prefer Caddy's graceful configuration API. The existing FamilyNet
  # process must stay running; never restart the subscription service.
  if "$CADDY_BIN" reload --config "$CADDY" --adapter caddyfile; then
    return 0
  fi
  local pid
  pid=$(systemctl show "$CADDY_SERVICE" -p MainPID --value)
  if [[ "$pid" =~ ^[0-9]+$ ]] && [ "$pid" -gt 1 ] && \
     [ -e "/proc/$pid/exe" ] && \
     [ "$(readlink -f "/proc/$pid/exe")" = "$CADDY_BIN" ]; then
    # This running instance was started with caddy run --config.
    # SIGUSR1 re-reads its Caddyfile when API reload is unavailable.
    kill -USR1 "$pid"
    sleep 2
    return 0
  fi
  return 1
}

rollback(){
  local status=$?
  if [ "$CUTOVER" -eq 1 ] && [ "$FINISHED" -eq 0 ]; then
    cp -p "$BACK/Caddyfile.before" "$CADDY"
    "$CADDY_BIN" validate --config "$CADDY" --adapter caddyfile >/dev/null 2>&1 || true
    reload_familynet_caddy || true
  fi
  if [ "$FINISHED" -eq 0 ]; then
    echo "未完成切换，原 Komari 保持不删除；备份: $BACK" >&2
  fi
  exit "$status"
}
trap rollback ERR

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
echo "获取 FamilyNet Monitor..."
curl --retry 3 --fail --silent --show-error --location "$BASE/monitor.py" -o "$TMP/monitor.py"
curl --retry 3 --fail --silent --show-error --location "$BASE/index.html" -o "$TMP/index.html"
[ -s "$TMP/monitor.py" ] && [ -s "$TMP/index.html" ]
python3 -m py_compile "$TMP/monitor.py"
install -m 0755 "$TMP/monitor.py" "$APP/monitor.py"
install -m 0644 "$TMP/index.html" "$APP/index.html"

if [ ! -f "$ETC/config.json" ]; then
  PASS=$(python3 -c 'import secrets;print(secrets.token_urlsafe(18))')
  python3 "$APP/monitor.py" --init-password "$PASS"
  printf '网页: https://207.57.74.12:9443\n流量审计账号: admin\n流量审计密码: %s\n' "$PASS" > "$CRED"
  chmod 600 "$CRED"
fi

# Enable accounting for new conntrack entries without changing firewall rules.
if ! command -v conntrack >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
  echo "安装连接统计命令..."
  if DEBIAN_FRONTEND=noninteractive apt-get update -qq && \
     DEBIAN_FRONTEND=noninteractive apt-get install -y -qq conntrack; then
    echo "conntrack 已就绪"
  else
    echo "conntrack 未安装成功，基本监控正常；连接审计暂不可用。"
  fi
fi
if [ -w /proc/sys/net/netfilter/nf_conntrack_acct ]; then
  if echo 1 > /proc/sys/net/netfilter/nf_conntrack_acct; then
    mkdir -p /etc/sysctl.d
    printf 'net.netfilter.nf_conntrack_acct = 1\n' > /etc/sysctl.d/91-familynet-monitor.conf
  else
    echo "内核未允许连接字节计数，目标访问审计可能不完整。"
  fi
fi

cat > "$UNIT" <<'SYSTEMD'
[Unit]
Description=FamilyNet lightweight VPS monitor
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/familynet-monitor
ExecStart=/usr/bin/python3 -u /opt/familynet-monitor/monitor.py
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/familynet-monitor
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
CapabilityBoundingSet=CAP_NET_ADMIN
AmbientCapabilities=CAP_NET_ADMIN

[Install]
WantedBy=multi-user.target
SYSTEMD
chmod 644 "$UNIT"
systemctl daemon-reload
systemctl enable --now familynet-monitor.service >/dev/null
systemctl restart familynet-monitor.service
echo "检查新监控..."
READY=0
for _ in $(seq 1 20); do
  if curl -fsS --max-time 2 "http://$TARGET/healthz" >/dev/null 2>&1; then
    READY=1; break
  fi
  sleep 1
done
if [ "$READY" -ne 1 ]; then
  journalctl -u familynet-monitor -n 30 --no-pager || true
  echo "新监控未通过检查，9443 保持原配置。" >&2
  exit 1
fi
# Verify the overview is public and the traffic APIs are private.
curl -fsS --max-time 7 "http://$TARGET/" | \
  python3 -c 'import sys; assert "id=\"auditGate\"" in sys.stdin.read()'
curl -fsS --max-time 7 "http://$TARGET/api/state" | \
  python3 -c 'import json,sys; d=json.load(sys.stdin); assert "cpu" in d and "quota" in d and "interface" in d'
curl -fsS --max-time 7 "http://$TARGET/api/pings" | \
  python3 -c 'import json,sys; d=json.load(sys.stdin); assert isinstance(d,list) and len(d)==12'
PUBLIC_AUDIT_CODE=$(curl -sS -o /dev/null -w '%{http_code}' \
  --max-time 7 "http://$TARGET/api/flows")
if [ "$PUBLIC_AUDIT_CODE" != 401 ]; then
  echo "流量审计接口未正确保护，拒绝切换 9443。" >&2
  exit 1
fi
if [ -f "$CRED" ]; then
  API_PW=$(sed -n -e 's/^流量审计密码: //p' -e 's/^密码: //p' "$CRED" | head -n 1)
  if [ -n "$API_PW" ]; then
    curl -fsS --max-time 7 --user "admin:$API_PW" \
      "http://$TARGET/api/auth" | \
      python3 -c 'import json,sys; assert json.load(sys.stdin)["ok"] is True'
    curl -fsS --max-time 7 --user "admin:$API_PW" \
      "http://$TARGET/api/history" | \
      python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin),list)'
  fi
fi

# Change only one known Komari reverse-proxy target. Unknown Caddy layouts abort.
CUTOVER=1
python3 - "$CADDY" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
s = path.read_text()
if not re.search(r'(?<!\d)9443(?!\d)', s):
    raise SystemExit("Caddyfile 未发现 9443 配置，拒绝更改。")
# Replace only a single literal old upstream. Do not touch the other
# FamilyNet subscription routes, ports or TLS settings.
pattern = r'((?:http://)?(?:127\.0\.0\.1|localhost):)25774\b'
found = list(re.finditer(pattern, s))
if len(found) != 1:
    raise SystemExit("Caddyfile 找不到唯一的 Komari 上游 25774；拒绝自动修改。")
s = re.sub(pattern, r'\g<1>25775', s, count=1)
path.write_text(s)
PY

"$CADDY_BIN" validate --config "$CADDY" --adapter caddyfile
reload_familynet_caddy
sleep 2
if ! curl -ksSf --connect-timeout 4 --max-time 8 \
     --resolve '207.57.74.12:9443:127.0.0.1' \
     'https://207.57.74.12:9443/healthz' | grep -q '"status":"ok"'; then
  echo "9443 未通过校验，自动恢复原 Caddy 上游。" >&2
  exit 1
fi

# From this point, 9443 serves the new monitor. Old data is backed up before deletion.
FINISHED=1
trap - ERR
echo "9443 已切换到 FamilyNet Monitor"

echo "停止旧 Komari..."
for unit in komari-agent.service komari.service komari-server.service; do
  systemctl disable --now "$unit" >/dev/null 2>&1 || true
done
sleep 2
if pgrep -x komari >/dev/null 2>&1 || pgrep -x komari-agent >/dev/null 2>&1; then
  echo "发现仍在运行的 Komari 进程；为安全起见，保留其原文件。" >&2
else
  if [ -d /var/lib/komari ]; then
    if tar -C /var/lib -czf "$BACK/komari-data.tar.gz" komari && \
       tar -tzf "$BACK/komari-data.tar.gz" >/dev/null; then
      rm -rf -- /var/lib/komari
      echo "Komari 数据已备份并删除"
    else
      echo "Komari 数据备份未完成，原数据保留。" >&2
    fi
  fi
  if [ -d /opt/komari ]; then
    if tar -C /opt -czf "$BACK/komari-opt.tar.gz" komari && \
       tar -tzf "$BACK/komari-opt.tar.gz" >/dev/null; then
      rm -rf -- /opt/komari
    fi
  fi
  for bin in /usr/local/bin/komari /usr/local/bin/komari-agent; do
    if [ -f "$bin" ]; then
      cp -p "$bin" "$BACK/$(basename "$bin").binary"
      rm -f -- "$bin"
    fi
  done
  for unit in komari.service komari-agent.service komari-server.service; do
    rm -f -- "/etc/systemd/system/$unit"
  done
  systemctl daemon-reload
fi

echo
echo "FamilyNet Monitor 已启用"
echo "网页: https://207.57.74.12:9443"
echo "凭据文件: $CRED"
if [ -f "$CRED" ]; then
  cat "$CRED"
fi
echo "备份: $BACK"
echo "服务: systemctl status familynet-monitor --no-pager"
echo "设置: $ETC/config.json"
