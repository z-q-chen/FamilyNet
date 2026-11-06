#!/usr/bin/env python3
"""Set exactly 12 existing Komari Ping tasks to 30 minutes; retain Ping metrics for 1 day.

Only changes ping_tasks.interval and metric definition retention for ping.latency_ms/ping.loss.
Does not alter the Glassmorphism theme, system telemetry polling, or other metric retention.
"""
from pathlib import Path
import datetime
import json
import re
import sqlite3
import subprocess
import sys

ROOT = Path("/var/lib/komari")
MAIN = ROOT / "komari.db"
METRICS = ROOT / "data/metrics.db"
IDS = {27,28,30,31,32,33,34,35,36,38,39,40}
PING_NAMES = ("ping.latency_ms", "ping.loss")
INTERVAL = 1800

if not MAIN.is_file():
    sys.exit("未找到 Komari 数据库；未修改。")

def jsonval(raw):
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw

def read_config(conn, key, default):
    row=conn.execute("SELECT value FROM configs WHERE key=?", (key,)).fetchone()
    value=jsonval(row[0]) if row else default
    return value if value is not None else default

with sqlite3.connect(f"file:{MAIN}?mode=ro", uri=True, timeout=10) as con:
    rows=con.execute("SELECT id, interval FROM ping_tasks").fetchall()
    ids={id for id, _ in rows}
    if ids != IDS or len(rows)!=12:
        sys.exit("Ping 任务数量或 ID 与预期不符；未修改。" + str(sorted(ids)))
    if not all(isinstance(iv,int) and iv>0 for _,iv in rows):
        sys.exit("发现异常 Ping 间隔；未修改。")
    dsn=str(read_config(con,"metric_db_dsn","./data/metrics.db"))
    prefix=str(read_config(con,"metric_table_prefix","metric_"))
    if dsn not in ("", "./data/metrics.db", "data/metrics.db", str(METRICS)):
        sys.exit("指标存储不是默认 SQLite，不能安全修改 Ping 留存；未修改。")
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z_0-9]{0,35}", prefix):
        sys.exit("指标数据表前缀异常；未修改。")

table=prefix+"definitions"
if not METRICS.is_file():
    sys.exit("找不到 metric store 默认数据库；未修改。")
with sqlite3.connect(f"file:{METRICS}?mode=ro", uri=True, timeout=10) as m:
    schema=m.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
    if not schema:
        sys.exit("未找到 Ping 指标留存表；未修改。")
    cols={x[1] for x in m.execute('PRAGMA table_info("'+table+'")')}
    if not {"name","retention_days","updated_at_milli"}.issubset(cols):
        sys.exit("Ping 指标留存表结构不匹配；未修改。")
    ret=m.execute('SELECT name, retention_days FROM "'+table+'" WHERE name IN (?,?)',PING_NAMES).fetchall()
    if {x[0] for x in ret}!=set(PING_NAMES):
        sys.exit("找不到两种 Ping 指标定义；未修改。")

need_intervals=any(iv!=INTERVAL for _,iv in rows)
need_retention=any(days!=1 for _,days in ret)
print("原来的12组Ping间隔(秒):", {str(id):iv for id,iv in rows})
print("原来的Ping历史保留(天):", dict(ret))

if not need_intervals and not need_retention:
    print("这12组Ping已经每30分钟检测、保留24小时；无需修改或重启。")
    sys.exit(0)

# Schedule lives in process memory. Require a managed service for safe reload.
check=subprocess.run(["systemctl","is-active","--quiet","komari.service"],check=False)
if check.returncode!=0:
    sys.exit("未发现运行中的 komari.service，无法确保调度热加载；未修改。")

# Small, independent backups of the two SQLite files, with WAL-consistent snapshots.
backup_dir=ROOT/"backup-ping-interval"
backup_dir.mkdir(exist_ok=True)
stamp=datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
for filename, source in (("komari",MAIN),("metrics",METRICS)):
    dest=backup_dir/(filename+"-"+stamp+".db")
    with sqlite3.connect(str(source),timeout=20) as source_db:
        with sqlite3.connect(str(dest),timeout=20) as backup_db:
            source_db.backup(backup_db)
    print("备份:", dest)

# sqlite ATTACH to commit all relevant changes in one transaction.
db=sqlite3.connect(str(MAIN),timeout=20)
try:
    db.execute("ATTACH DATABASE ? AS metrics", (str(METRICS),))
    db.execute("BEGIN IMMEDIATE")
    if need_intervals:
        db.execute(
            "UPDATE ping_tasks SET interval=? WHERE id IN ("+",".join("?" for _ in IDS)+")",
            (INTERVAL,*sorted(IDS)))
    if need_retention:
        now=int(datetime.datetime.now(datetime.timezone.utc).timestamp()*1000)
        db.execute(
            'UPDATE metrics."'+table+'" SET retention_days=1, updated_at_milli=? WHERE name IN (?,?)',
            (now,*PING_NAMES))
    verify_tasks=db.execute("SELECT id, interval FROM ping_tasks").fetchall()
    verify_metrics=db.execute(
        'SELECT name, retention_days FROM metrics."'+table+'" WHERE name IN (?,?)',PING_NAMES
    ).fetchall()
    if len(verify_tasks)!=12 or any(iv!=INTERVAL for _,iv in verify_tasks) or \
       {name:days for name,days in verify_metrics}!={n:1 for n in PING_NAMES}:
        raise RuntimeError("数据库验证不通过，已回滚")
    db.commit()
except:
    db.rollback()
    raise
finally:
    db.close()

restart=subprocess.run(["systemctl","restart","komari.service"],capture_output=True,text=True,timeout=40)
if restart.returncode!=0:
    print("警告：配置已保存，但 Komari 重启失败。请检查服务状态：")
    print((restart.stderr or restart.stdout)[:700])
    sys.exit(2)
print("成功：12组Ping每1800秒（30分钟）执行一次。")
print("成功：仅 ping.latency_ms 和 ping.loss 的保留时间设为1天（24小时）。")
print("已重启 Komari 主服务以加载新的 Ping 任务调度。")
print("保留原主题、原生3秒状态刷新、服务器累计流量和其它指标历史。")
print("注意：主题卡片短摘要仍默认看最近1小时；完整趋势请在Ping图表选择“1天”。")
