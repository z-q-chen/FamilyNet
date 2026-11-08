#!/usr/bin/env bash
# Update probe scheduler, 48-slot API history, and frontend without replacing local hotfixes.
set -Eeuo pipefail
umask 077
[ "$(id -u)" -eq 0 ] || { echo "请使用 sudo bash 运行" >&2; exit 1; }
APP=/opt/familynet-monitor
PIN=1a26a7d485d20b2a3e7ab4121b927a227eaa3e54
BASE="https://raw.githubusercontent.com/z-q-chen/FamilyNet/$PIN/ops/monitor"
TMP=$(mktemp -d)
CHANGED=0
SUCCESS=0

cleanup() {
  code=$?
  if [ "$CHANGED" = 1 ] && [ "$SUCCESS" != 1 ]; then
    echo "部署未通过验证，恢复现有监控文件…" >&2
    cp -p "$TMP/original-monitor.py" "$APP/monitor.py" || true
    cp -p "$TMP/original-index.html" "$APP/index.html" || true
    systemctl restart familynet-monitor.service || true
  fi
  rm -rf "$TMP"
  exit "$code"
}
trap cleanup EXIT

for path in "$APP/monitor.py" "$APP/index.html"; do
  [ -f "$path" ] || { echo "缺少 $path" >&2; exit 1; }
done
systemctl is-active --quiet familynet-monitor.service || {
  echo "监控服务未运行，未做任何修改" >&2; exit 1;
}

cp -p "$APP/monitor.py" "$TMP/original-monitor.py"
cp -p "$APP/index.html" "$TMP/original-index.html"
curl -fLsS --retry 3 "$BASE/index.html" -o "$TMP/index.html"
curl -fLsS --retry 3 "$BASE/monitor.py" -o "$TMP/upstream-monitor.py"

# Copy only the probe-related methods and scheduler; keep unrelated local hotfixes.
python3 - "$APP/monitor.py" "$TMP/upstream-monitor.py" "$TMP/monitor.py" <<'PY'
import ast
from pathlib import Path
import sys

local = Path(sys.argv[1]).read_text(encoding="utf-8")
upstream = Path(sys.argv[2]).read_text(encoding="utf-8")

def block(code, node):
    return "".join(code.splitlines(keepends=True)[node.lineno - 1:node.end_lineno])

def method(code, name, cls_name="Store"):
    tree = ast.parse(code)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)
    return next((n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name), None)

def top_function(code, name):
    return next(
        (n for n in ast.parse(code).body if isinstance(n, ast.FunctionDef) and n.name == name),
        None,
    )

src_loop = top_function(upstream, "ping_loop")
dst_loop = top_function(local, "ping_loop")
if not src_loop or not dst_loop:
    raise SystemExit("ping_loop 未找到，原文件不变")
lines = local.splitlines(keepends=True)
lines[dst_loop.lineno - 1:dst_loop.end_lineno] = [block(upstream, src_loop).rstrip("\n") + "\n"]
local = "".join(lines)

src_batch = method(upstream, "ping_batch")
dst_batch = method(local, "ping_batch")
if src_batch is None:
    raise SystemExit("GitHub 缺少 ping_batch")
lines = local.splitlines(keepends=True)
if dst_batch is None:
    dst_ping = method(local, "ping")
    if dst_ping is None:
        raise SystemExit("当前版本缺少 Store.ping")
    lines[dst_ping.end_lineno:dst_ping.end_lineno] = [
        "\n" + block(upstream, src_batch).rstrip("\n") + "\n"
    ]
else:
    lines[dst_batch.lineno - 1:dst_batch.end_lineno] = [
        block(upstream, src_batch).rstrip("\n") + "\n"
    ]
patched = "".join(lines)

# Keep the last 48 probe rounds, not merely the last 24 wall-clock hours.
# Preserve all locally customized functions unrelated to the probe history.
for cls_name, name in (("Store", "cleanup"), ("Monitor", "pings")):
    source_node = method(upstream, name, cls_name)
    target_node = method(patched, name, cls_name)
    if source_node is None or target_node is None:
        raise SystemExit("缺少探针历史方法：" + cls_name + "." + name)
    lines = patched.splitlines(keepends=True)
    lines[target_node.lineno - 1:target_node.end_lineno] = [
        block(upstream, source_node).rstrip("\n") + "\n"
    ]
    patched = "".join(lines)

ast.parse(patched)
assert "store.ping_batch(readings)" in patched
assert "ThreadPoolExecutor(max_workers=len(tasks)" in patched
assert '"history": by_id.get(str(task["id"]), [])[-48:]' in patched
assert "(int(time.time()) - 48 * 3600,)" in patched
Path(sys.argv[3]).write_text(patched, encoding="utf-8")
PY

python3 -m py_compile "$TMP/monitor.py"
python3 - "$TMP/index.html" <<'PY'
from pathlib import Path
import sys
s = Path(sys.argv[1]).read_text(encoding="utf-8")
for marker in ('class="probe-legend"', 'id="pinggrid"', 'mostRecentTs',
               'var columnCount=48;', 'Math.round((Number(q.ts)-lastRound)/1800)',
               'function classifyDelay', 'function classifyLoss', 'audit=auth'):
    assert marker in s, marker
PY

CHANGED=1
install -m 0755 "$TMP/monitor.py" "$APP/monitor.py"
install -m 0644 "$TMP/index.html" "$APP/index.html"
systemctl restart familynet-monitor.service

OK=0
for i in $(seq 1 20); do
  if curl --noproxy '*' -fsS --max-time 3 http://127.0.0.1:25775/healthz >/dev/null &&
     curl --noproxy '*' -fsS --max-time 4 http://127.0.0.1:25775/api/pings |
       python3 -c 'import json,sys; assert len(json.load(sys.stdin))==12' 2>/dev/null; then
    OK=1
    break
  fi
  sleep 1
done
[ "$OK" = 1 ] || { echo "健康检查未通过" >&2; exit 1; }

SUCCESS=1
echo "FamilyNet 已更新：每轮 12 组同步检测，固定 48 格历史仅在新结果产生时平移。"
echo "原有记录、密码及其他本地 Python 修改均保留。"
