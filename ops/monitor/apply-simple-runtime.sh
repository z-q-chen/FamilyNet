#!/usr/bin/env bash
# Apply FamilyNet's simplified monitor runtime without overwriting unrelated VPS hotfixes.
set -Eeuo pipefail
umask 077
[ "$(id -u)" -eq 0 ] || { echo "请使用 sudo bash 运行" >&2; exit 1; }

APP=/opt/familynet-monitor
DB=/var/lib/familynet-monitor/monitor.db
UNIT=familynet-monitor.service
PORT=25775
PIN=9cde47064ad128dd0a119cdb682a903540ae59b0
BASE="https://raw.githubusercontent.com/z-q-chen/FamilyNet/$PIN/ops/monitor"
TMP=$(mktemp -d)
CHANGED=0
OK=0

rollback() {
  status=$?
  trap - EXIT
  if [ "$CHANGED" = 1 ] && [ "$OK" != 1 ]; then
    echo "更新未通过检查，正在恢复原有监控和数据…" >&2
    systemctl stop "$UNIT" >/dev/null 2>&1 || true
    cp -p "$TMP/original.py" "$APP/monitor.py" || true
    cp -p "$TMP/original.html" "$APP/index.html" || true
    if [ -f "$TMP/db-backup.sqlite" ]; then
      cp -p "$TMP/db-backup.sqlite" "$DB" || true
      rm -f "$DB-wal" "$DB-shm"
    fi
    systemctl start "$UNIT" || true
  fi
  rm -rf -- "$TMP"
  exit "$status"
}
trap rollback EXIT

for command in curl python3 systemctl; do
  command -v "$command" >/dev/null || { echo "缺少 $command" >&2; exit 1; }
done
for path in "$APP/monitor.py" "$APP/index.html" "$DB"; do
  [ -f "$path" ] || { echo "缺少现有文件 $path" >&2; exit 1; }
done
systemctl is-active --quiet "$UNIT" || { echo "原监控服务未运行，已取消更新" >&2; exit 1; }
cp -p "$APP/monitor.py" "$TMP/original.py"
cp -p "$APP/index.html" "$TMP/original.html"

curl -fLsS --retry 3 "$BASE/monitor.py" -o "$TMP/upstream.py"
curl -fLsS --retry 3 "$BASE/index.html" -o "$TMP/index.html"
curl -fLsS --retry 3 "$BASE/test-simple-runtime.py" -o "$TMP/test.py"

# Replace only the monitor state/round methods. Keep independent local patches.
python3 - "$TMP/original.py" "$TMP/upstream.py" "$TMP/merged.py" <<'PY'
import ast
from pathlib import Path
import sys

local = Path(sys.argv[1]).read_text(encoding="utf-8")
upstream = Path(sys.argv[2]).read_text(encoding="utf-8")

def method(text, cls_name, name):
    tree = ast.parse(text)
    cls = next((n for n in tree.body
                if isinstance(n, ast.ClassDef) and n.name == cls_name), None)
    if cls is None:
        raise SystemExit("找不到类 " + cls_name)
    return next((n for n in cls.body
                 if isinstance(n, ast.FunctionDef) and n.name == name), None)

def function(text, name):
    return next((n for n in ast.parse(text).body
                 if isinstance(n, ast.FunctionDef) and n.name == name), None)

def block(text, node):
    lines = text.splitlines(keepends=True)
    return "".join(lines[node.lineno - 1:node.end_lineno]).rstrip("\n") + "\n"

def patch_method(cls, name, anchor=None):
    global local
    src = method(upstream, cls, name)
    if src is None:
        raise SystemExit("升级包缺少 " + cls + "." + name)
    dst = method(local, cls, name)
    lines = local.splitlines(keepends=True)
    if dst is not None:
        lines[dst.lineno - 1:dst.end_lineno] = [block(upstream, src)]
    else:
        target = method(local, cls, anchor)
        if target is None:
            raise SystemExit("本地版本无法放置 " + cls + "." + name)
        lines[target.end_lineno:target.end_lineno] = ["\n" + block(upstream, src)]
    local = "".join(lines)
    ast.parse(local)

def patch_function(name, anchor=None):
    global local
    src = function(upstream, name)
    dst = function(local, name)
    if src is None:
        raise SystemExit("升级包缺少 " + name)
    lines = local.splitlines(keepends=True)
    if dst is not None:
        lines[dst.lineno - 1:dst.end_lineno] = [block(upstream, src)]
    else:
        target = function(local, anchor)
        if target is None:
            raise SystemExit("本地版本无法放置 " + name)
        lines[target.end_lineno:target.end_lineno] = ["\n\n" + block(upstream, src)]
    local = "".join(lines)
    ast.parse(local)

for name, anchor in (
    ("__init__", None), ("_trim_probe_rounds", "__init__"),
    ("ping", None), ("ping_batch", "ping"), ("cleanup", None),
):
    patch_method("Store", name, anchor)

for name, anchor in (
    ("__init__", None), ("state", None), ("sample_state", "state"),
    ("pings", None),
):
    patch_method("Monitor", name, anchor)

for name, anchor in (
    ("collector_loop", None), ("state_loop", "collector_loop"),
    ("ping_loop", None), ("main", None),
):
    patch_function(name, anchor)

ast.parse(local)
required = [
    "CREATE TABLE IF NOT EXISTS probe_rounds(",
    "SELECT id,ts FROM pings WHERE round_id IS NULL",
    "def sample_state(self):",
    "return dict(self.cached_state)",
    "def state_loop(app):",
    "name=\"fn-state\"",
    "\"rounds\": [{\"id\": rid, \"ts\": ts} for rid, ts in rounds]",
]
assert all(item in local for item in required), "新逻辑检查未通过"
Path(sys.argv[3]).write_text(local, encoding="utf-8")
PY

python3 -m py_compile "$TMP/merged.py"
python3 "$TMP/test.py" "$TMP/merged.py"
# Check the embedded browser script as well, when Node.js is available.
if command -v node >/dev/null 2>&1; then
  python3 - "$TMP/index.html" "$TMP/ui.js" <<'PY'
from pathlib import Path
import sys
html = Path(sys.argv[1]).read_text(encoding="utf-8")
assert html.count("<script>") == 1 and html.count("</script>") == 1
Path(sys.argv[2]).write_text(html.split("<script>", 1)[1].split("</script>", 1)[0], encoding="utf-8")
PY
  node --check "$TMP/ui.js"
fi
python3 - "$TMP/index.html" <<'PY'
from pathlib import Path
import sys
data = Path(sys.argv[1]).read_text(encoding="utf-8")
for needle in (
    'var data=payload.targets||[]',
    'positions[String(q.round_id)]',
    'Number(s.round_id)!==lastPingRound',
    'columnCount=48',
    'id="auditGate"',
):
    assert needle in data, needle
PY

# SQLite's online backup includes committed WAL data, without disrupting the live service.
python3 - "$DB" "$TMP/db-backup.sqlite" <<'PY'
import sqlite3
import sys
with sqlite3.connect(sys.argv[1], timeout=15) as source:
    with sqlite3.connect(sys.argv[2]) as backup:
        source.backup(backup)
PY

CHANGED=1
install -m 0755 "$TMP/merged.py" "$APP/monitor.py"
install -m 0644 "$TMP/index.html" "$APP/index.html"
systemctl restart "$UNIT"

# Check the new live JSON contract rather than just a process being alive.
for trial in $(seq 1 20); do
  if python3 - "$PORT" <<'PY'
import json
import sys
from urllib.request import urlopen
base = "http://127.0.0.1:" + sys.argv[1]
with urlopen(base + "/healthz", timeout=2) as r:
    assert json.load(r)["status"] == "ok"
with urlopen(base + "/api/state", timeout=3) as r:
    state = json.load(r)
assert isinstance(state["round_id"], int) and state["time"] > 0
with urlopen(base + "/api/pings", timeout=3) as r:
    data = json.load(r)
assert data["round_id"] == state["round_id"] or data["round_id"] >= state["round_id"]
assert len(data["targets"]) == 12
assert len(data["rounds"]) <= 48
assert all("round_id" in item for target in data["targets"]
           for item in target["history"])
PY
  then
    OK=1
    break
  fi
  sleep 1
done
[ "$OK" = 1 ] || { echo "运行健康检查未通过" >&2; exit 1; }
echo "FamilyNet 更新成功：后台 3 秒采样、48 轮队列、只在新轮次更新历史。"
echo "服务器已有的监控配置、密码、历史数据及代理服务均未主动修改。"
