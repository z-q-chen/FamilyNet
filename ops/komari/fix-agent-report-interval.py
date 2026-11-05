#!/usr/bin/env python3
"""Normalize the active Komari Agent report interval to 3s when safely managed by systemd."""
from pathlib import Path
import json
import os
import re
import subprocess
import sys

procs = []
for proc in Path("/proc").iterdir():
    if not proc.name.isdecimal(): continue
    try:
        argv = [v.decode(errors="replace") for v in (proc/"cmdline").read_bytes().split(b"\0") if v]
        if not argv or "komari-agent" not in Path(argv[0]).name: continue
        procs.append((int(proc.name), proc, argv))
    except (OSError, PermissionError): pass
if len(procs)!=1:
    sys.exit("安全停止：未唯一定位正在运行的 Komari Agent（数量 %d）。" % len(procs))
pid, proc, argv = procs[0]
config_file = None
for i, a in enumerate(argv):
    if a == "--config" and i+1<len(argv):
        config_file=argv[i+1]
    elif a.startswith("--config="):
        config_file=a.split("=",1)[1]
if not config_file:
    sys.exit("安全停止：Agent 未使用 --config 文件；不擅自改其他启动参数。")
path=Path(config_file)
if not path.is_absolute():
    path=Path(os.readlink(proc/"cwd"))/path
path=path.resolve()
if not path.is_file():
    sys.exit("安全停止：Agent 配置路径不是普通文件。")
data=json.loads(path.read_text(encoding="utf-8"))
if not isinstance(data,dict):
    sys.exit("安全停止：Agent 配置不是 JSON 对象。")
actual=data.get("interval",3)
if isinstance(actual,bool) or not isinstance(actual,(int,float)):
    sys.exit("安全停止：Agent 配置 interval 非数值。")
print("Agent PID:",pid)
print("配置文件:",path)
print("配置文件中的 interval:",actual,"秒（这项优先于命令行默认值）")

# Reload automatically only for systemd services whose MainPID is this exact agent.
units=[]
try:
    cgroup=(proc/"cgroup").read_text()
    units+=re.findall(r"([A-Za-z0-9_.@-]+\.service)(?:/|$)",cgroup,re.M)
except OSError: pass
units.append("komari-agent.service")
unit=None
for candidate in dict.fromkeys(units):
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+\.service",candidate):continue
    try:
        p=subprocess.run(["systemctl","show","--property=MainPID","--value",candidate],
                         capture_output=True,text=True,timeout=5)
        if p.returncode==0 and p.stdout.strip()==str(pid):
            unit=candidate
            break
    except (OSError, subprocess.TimeoutExpired):pass
if actual==3:
    print("配置已是 3 秒，不需要重启；如果仍每 6 秒上报，需要检查 Agent 到 Komari 的传输链路。")
    print("找到服务:",unit or "无法确认 systemd 服务")
    sys.exit(0)
if not unit:
    sys.exit("安全停止：当前上报间隔为 %s 秒，但无法验证其 systemd 服务；没有修改。" % actual)

# Preserve all other Agent configuration settings and file permissions.
updated=dict(data)
updated["interval"]=3
path.write_text(json.dumps(updated,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
res=subprocess.run(["systemctl","restart",unit],capture_output=True,text=True,timeout=25)
if res.returncode:
    sys.exit("配置已设成 3 秒，但服务重启失败：%s" % ((res.stderr or res.stdout)[:350]))
print("已修改 Agent 的 interval 为 3 秒并重启:",unit)
print("网页配置仍为 3 秒；不会修改 Ping 任务、流量累计、主题或 Komari 主服务。")
print("打开网站后 KB/s 将随最新上报更新，通常每约 3 秒变化（数值可能相同）。")
