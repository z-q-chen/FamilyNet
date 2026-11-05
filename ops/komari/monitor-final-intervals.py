#!/usr/bin/env python3
"""Apply final Glassmorphism monitoring preferences without replacing the theme."""
import json
import re
import sqlite3
import subprocess
from pathlib import Path

base = Path("/var/lib/komari")
dist = base / "data/theme/Glassmorphism/dist"
index = dist / "index.html"
database = base / "komari.db"
task_ids = [27,28,30,31,32,33,34,35,36,38,39,40]

if not index.is_file() or not database.is_file():
    raise SystemExit("未找到当前主题或 Komari 数据库；没有修改任何内容。")
ro = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
tasks = dict(ro.execute(
    "SELECT id, interval FROM ping_tasks WHERE id IN (" +
    ",".join("?" for _ in task_ids) + ")", task_ids
).fetchall())
if set(tasks) != set(task_ids):
    raise SystemExit("12 个 Ping 任务不完整；不修改。")
settings_row = ro.execute(
    "SELECT data FROM theme_configurations WHERE short = ?", ("Glassmorphism",)
).fetchone()
if not settings_row:
    raise SystemExit("当前 Glassmorphism 配置不存在；不修改。")
settings = json.loads(settings_row[0])
ro.close()
if not isinstance(settings, dict):
    raise SystemExit("主题配置格式错误；不修改。")

# Komari reads task schedules into memory. Only a systemd-controlled instance
# can be safely restarted after any necessary task interval change.
need_schedule_reload = [task_id for task_id in task_ids if tasks[task_id] != 60]
if need_schedule_reload:
    try:
        active = subprocess.run(
            ["systemctl", "is-active", "--quiet", "komari.service"], check=False
        ).returncode == 0
    except FileNotFoundError:
        active = False
    if not active:
        raise SystemExit(
            f"Ping 任务 {need_schedule_reload} 不是 60 秒，且找不到运行中的 "
            "komari.service。为避免数据库与调度器不同步，未作任何修改。"
        )

# Locate the theme's original byte-format configuration, rather than writing
# a separate DOM overlay. Both first-install and previously patched variants
# are handled.
quoted = r'(?:[A-Za-z]+|"[A-Za-z]+"|\x27[A-Za-z]+\x27)'
pattern = re.compile(
    r'(?<![\w$])(?P<q1>B|"B"|\x27B\x27)\s*:\s*0\s*,\s*'
    r'(?P<q2>KB|"KB"|\x27KB\x27)\s*:\s*[012]\s*,\s*'
    r'(?P<q3>MB|"MB"|\x27MB\x27)\s*:\s*[012]\s*,\s*'
    r'(?P<q4>GB|"GB"|\x27GB\x27)\s*:\s*[012]\s*,\s*'
    r'(?P<q5>TB|"TB"|\x27TB\x27)\s*:\s*[012]'
)
changes = {}
match_files = []
for f in (dist / "assets").rglob("*.js"):
    code = f.read_text(encoding="utf-8", errors="replace")
    found = list(pattern.finditer(code))
    if not found:
        continue
    def two_decimals(m):
        return (
            f"{m.group('q1')}:0,{m.group('q2')}:2,"
            f"{m.group('q3')}:2,{m.group('q4')}:2,{m.group('q5')}:2"
        )
    updated = pattern.sub(two_decimals, code)
    if updated != code:
        changes[f] = updated
    match_files.append((f.name, len(found)))

if not match_files:
    raise SystemExit(
        "未定位到当前编译包的字节精度配置（B/KB/MB/GB/TB）。"
        "为避免错误补丁，本次没有修改。"
    )

# The upstream hideGeneralCard setting removes both the globe and the six
# summary cards while keeping node cards and all their pricing/traffic data.
settings["hideGeneralCard"] = True
settings["hideEarth"] = True
settings["dataUpdateInterval"] = 1
settings["rpcTransportMode"] = "http"

# Remove earlier hand-added CSS only. Keep all original Vite assets and
# native Ping code unchanged.
html = index.read_text(encoding="utf-8")
clean_html = re.sub(
    r'<style\s+id=["\x27]fn-hide-duplicate-overview["\x27][^>]*>.*?</style>',
    "", html, flags=re.S,
)

for f, content in changes.items():
    f.write_text(content, encoding="utf-8")
if clean_html != html:
    index.write_text(clean_html, encoding="utf-8")

db = sqlite3.connect(str(database), timeout=15)
db.execute("BEGIN IMMEDIATE")
if need_schedule_reload:
    db.execute(
        "UPDATE ping_tasks SET interval = 60 WHERE id IN (" +
        ",".join("?" for _ in task_ids) + ")", task_ids
    )
db.execute(
    "UPDATE theme_configurations SET data = ? WHERE short = ?",
    (json.dumps(settings, ensure_ascii=False), "Glassmorphism"),
)
db.commit()
db.close()

if need_schedule_reload:
    restarted = subprocess.run(
        ["systemctl", "restart", "komari.service"], capture_output=True, text=True
    )
    if restarted.returncode != 0:
        print("注意：Ping interval 已写入数据库，但重启 Komari 失败，调度器可能仍使用旧值。")
        print((restarted.stderr or restarted.stdout)[:1000])
    else:
        print("Komari 已重启一次，以加载 12 个 Ping 任务的 60 秒调度。")
else:
    print("12 个 Ping 任务原本就是每 60 秒执行，无需重启 Komari。")

print("已隐藏地球与六张概览卡，保留原生服务器卡片。")
print("实时状态轮询：每 1 秒（网页关闭后自然停止；后台标签页由浏览器节流）。")
print("Ping 历史视图：沿用主题原生的约每分钟刷新。")
print("KB/MB/GB 的显示精度：两位小数；流量原始计数未更改。")
print("JS 精度配置：", match_files)
print("请用新无痕标签页刷新 https://207.57.74.12:9443/")
