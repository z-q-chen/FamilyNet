#!/usr/bin/env python3
"""Set the existing 12 Komari Ping tasks to 30 minutes and Ping history to 1 day.

This alters only Ping interval and the two Ping metric retention definitions.
The Komari server systemd service is restarted to reload its scheduler/cache.
"""
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

ROOT = Path("/var/lib/komari")
MAIN = ROOT / "komari.db"
KNOWN_TASK_IDS = {27,28,30,31,32,33,34,35,36,38,39,40}
PING_METRICS = ("ping.latency_ms", "ping.loss")

def stop(message):
    raise RuntimeError(message)

def run(*args, timeout=30):
    return subprocess.run(list(args), capture_output=True, text=True, timeout=timeout)

def locate_server_service():
    candidates = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            exe_name = Path(os.readlink(proc / "exe")).name
            argv = [(v.decode(errors="replace")) for v in (proc / "cmdline").read_bytes().split(b"\x00") if v]
            if exe_name != "komari" and (not argv or Path(argv[0]).name != "komari"):
                continue
            candidates.append((int(proc.name), proc, argv))
        except (OSError, PermissionError):
            continue
    if len(candidates) != 1:
        stop("无法唯一确认 Komari 主服务进程（找到 %d 个），没有修改。" % len(candidates))
    pid, proc, argv = candidates[0]
    unit_candidates = []
    try:
        cg = (proc/"cgroup").read_text()
        unit_candidates.extend(re.findall(r"([\w.@-]+\.service)(?:/|$)", cg, re.M))
    except OSError:
        pass
    unit_candidates += ["komari.service", "komari-server.service"]
    for unit in dict.fromkeys(unit_candidates):
        if not re.fullmatch(r"[\w.@-]+\.service", unit):
            continue
        q = run("systemctl", "show", unit, "--property=MainPID", "--value", timeout=6)
        if q.returncode == 0 and q.stdout.strip() == str(pid):
            return pid, unit
    stop("Komari 主服务未由可确认的 systemd 服务管理。为了避免修改后调度不生效，未修改数据库。")

def connect(path, *, readonly=False):
    if readonly:
        return sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True, timeout=20)
    conn = sqlite3.connect(str(path), timeout=20)
    conn.execute("PRAGMA busy_timeout=20000")
    return conn

def get_metric_database():
    # Fail closed for explicitly configured external metric backends.
    with connect(MAIN, readonly=True) as con:
        rows = dict(con.execute("SELECT key,value FROM configs WHERE key IN ('metric_db_driver','metric_db_dsn','metric_table_prefix')").fetchall())
    def parse(key, default=""):
        val=rows.get(key)
        if val is None:
            return default
        try:
            x=json.loads(val)
            return str(x) if x is not None else default
        except (ValueError, TypeError):
            return str(val)
    driver=parse("metric_db_driver","sqlite").strip().lower()
    if driver not in ("", "sqlite"):
        stop("监控指标采用 %s 而非本地 SQLite；为避免误改，未修改。" % driver)
    dsn=parse("metric_db_dsn","./data/metrics.db").strip()
    if dsn.startswith("file:") or "?" in dsn:
        stop("指标数据库使用特殊 SQLite 连接串，未修改（请提供配置方式）。")
    metricfile=Path(dsn)
    if not metricfile.is_absolute():
        metricfile=ROOT/metricfile
    metricfile=metricfile.resolve()
    if not metricfile.is_file():
        stop("未找到指标数据库 %s，未修改。" % metricfile)
    prefix=parse("metric_table_prefix","metric_")
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*",prefix):
        stop("指标表前缀不合法，未修改。")
    return metricfile, prefix+"definitions"

def read_current(metricfile, tablename):
    with connect(MAIN, readonly=True) as main:
        names={row[0] for row in main.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "ping_tasks" not in names:
            stop("未找到 ping_tasks 表，未修改。")
        tasks=main.execute("SELECT id, interval, name FROM ping_tasks ORDER BY id").fetchall()
        ids={int(r[0]) for r in tasks}
        if ids!=KNOWN_TASK_IDS:
            stop("当前 Ping 任务并非先前确认的 12 个（现为 %d 个）；未修改，避免影响其他任务。" % len(ids))
    with connect(metricfile, readonly=True) as metrics:
        tables={row[0] for row in metrics.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tablename not in tables:
            stop("指标库缺少 %s 表，未修改。" % tablename)
        sql="SELECT name, retention_days FROM \"" + tablename + "\" WHERE name IN (?,?)"
        old=dict(metrics.execute(sql,PING_METRICS).fetchall())
        if set(old)!=set(PING_METRICS):
            stop("指标定义缺少延迟或丢包率指标，未修改。")
    return tasks,old

def restore(tasks, old, metricfile, tablename):
    with connect(MAIN) as main:
        main.executemany("UPDATE ping_tasks SET interval=? WHERE id=?",
                         [(int(interval),int(id_)) for id_,interval,_ in tasks])
    with connect(metricfile) as metrics:
        sql="UPDATE \"" + tablename + "\" SET retention_days=? WHERE name=?"
        metrics.executemany(sql,[(int(days),name) for name,days in old.items()])

def apply(metricfile, tablename):
    with connect(MAIN) as main:
        main.execute("UPDATE ping_tasks SET interval=1800 WHERE id IN ("+",".join("?"*len(KNOWN_TASK_IDS))+")",
                     tuple(sorted(KNOWN_TASK_IDS)))
    with connect(metricfile) as metrics:
        sql="UPDATE \"" + tablename + "\" SET retention_days=1, updated_at_milli=? WHERE name IN (?,?)"
        metrics.execute(sql, (int(time.time()*1000),*PING_METRICS))

def main():
    if not MAIN.is_file():
        stop("找不到 "+str(MAIN))
    pid,unit=locate_server_service()
    metricfile,table=get_metric_database()
    tasks,previous=read_current(metricfile,table)
    print("确认 Komari 服务:",unit,"PID",pid, flush=True)
    print("确认 Ping 任务：12 组；原间隔:",sorted({x[1] for x in tasks}), flush=True)
    print("确认 Ping 记录保留天数:",previous,flush=True)
    if all(int(x[1])==1800 for x in tasks) and all(int(x)==1 for x in previous.values()):
        print("配置已是 30 分钟测试、24 小时保留；无需重启。")
        return
    backupdir=ROOT/"backups"
    backupdir.mkdir(exist_ok=True)
    backupfile=backupdir/("ping-30m-24h-before-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+".json")
    backupfile.write_text(json.dumps({"unit":unit,"metric_database":str(metricfile),
      "tasks":[{"id":id_,"interval":secs,"name":name} for id_,secs,name in tasks],
      "metric_retention_days":previous},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("旧配置已备份:",backupfile,flush=True)
    try:
        apply(metricfile,table)
        print("配置写入成功，正在重启 Komari 主服务以加载新的 Ping 调度……",flush=True)
        result=run("systemctl","restart",unit,timeout=45)
        if result.returncode!=0:
            stop("服务重启失败：" + (result.stderr or result.stdout).strip()[:400])
        active=run("systemctl","is-active",unit,timeout=8)
        if active.returncode!=0 or active.stdout.strip()!="active":
            stop("服务重启后未处于 active 状态")
    except Exception:
        print("应用失败，尝试恢复旧设置并重新启动服务……",file=sys.stderr,flush=True)
        restore(tasks,previous,metricfile,table)
        run("systemctl","restart",unit,timeout=45)
        raise
    now,ret=read_current(metricfile,table)
    print("已设置：12 组 Ping 间隔 1800 秒（30 分钟）。")
    print("已设置：Ping 延迟与丢包率记录保留 1 天（24 小时），旧记录由后端清理周期逐步清除。")
    print("复查：间隔",sorted({x[1] for x in now}),"; 指标保留",ret)
    print("不修改监控网页 3 秒刷新、探针、套餐价格、其他指标历史记录。")

if __name__=="__main__":
    try:
        main()
    except Exception as e:
        sys.exit("未完成："+str(e))
