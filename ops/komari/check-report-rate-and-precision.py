#!/usr/bin/env python3
"""Read-only Komari theme/agent probe (never prints credentials)."""
import json
import pathlib
import re
import sqlite3
import time
import urllib.request

root=pathlib.Path('/var/lib/komari')
db=sqlite3.connect('file:'+str(root/'komari.db')+'?mode=ro',uri=True)
row=db.execute("SELECT value FROM configs WHERE key='theme'").fetchone()
theme=json.loads(row[0]) if row else '?'
row=db.execute("SELECT data FROM theme_configurations WHERE short='Glassmorphism'").fetchone()
settings=json.loads(row[0]) if row else {}
print('ACTIVE_THEME:',theme)
print('THEME_SETTINGS:',{k:settings.get(k) for k in ('dataUpdateInterval','hideGeneralCard','hideEarth','rpcTransportMode')})
print('PING_60S:',db.execute("SELECT COUNT(*) FROM ping_tasks WHERE interval=60").fetchone()[0],
      '/ TOTAL:',db.execute("SELECT COUNT(*) FROM ping_tasks").fetchone()[0])
db.close()
idx=root/'data/theme/Glassmorphism/dist/index.html'
if idx.is_file():
    s=idx.read_text(errors='replace')
    print('INDEX_PATCHES:',{'fn-twelve': 'fn-twelve-ping.js' in s, 'custom-overview-css': 'fn-hide-duplicate-overview' in s})
assets=root/'data/theme/Glassmorphism/dist/assets'
candidates=[]
for file in assets.rglob('*.js'):
    s=file.read_text(errors='ignore')
    found=re.search(r'(?:(?:["\x27]GB["\x27])|\bGB)\s*:\s*([012])',s)
    if found:
        candidates.append((file.name,found.group(1),s[max(0,found.start()-65):found.end()+50]))
print('BUNDLE_GB_PRECISION_MATCHES:',len(candidates))
for name,value,snip in candidates[:6]:
    print(' BUNDLE:',name,'GB_DECIMALS:',value,'NEARBY:',repr(snip[:115]))
agents=[]
for proc in pathlib.Path('/proc').iterdir():
    if not proc.name.isdecimal():
        continue
    try:
        args=(proc/'cmdline').read_bytes().split(b'\0')
        argv=[a.decode(errors='replace') for a in args if a]
        if not argv or 'komari-agent' not in pathlib.Path(argv[0]).name:
            continue
        found='default(3s)'; config=False
        for i,v in enumerate(argv):
            if v in ('-i','--interval') and i+1<len(argv):found=argv[i+1]
            if v.startswith('--interval='):found=v.split('=',1)[1]
            if v in ('--config','-c'):config=True
        env=(proc/'environ').read_bytes().split(b'\0')
        envint=[x.decode(errors='replace').split('=',1)[1] for x in env if x.startswith(b'AGENT_INTERVAL=')]
        agents.append((proc.name,found,envint[0] if envint else '-',config))
    except (PermissionError,FileNotFoundError,ProcessLookupError,OSError):
        pass
print('AGENT_PROCESSES (PID, cli_interval, env_interval, config_file):',agents if agents else 'not found')
times=[]
previous=None
for n in range(7):
    payload=json.dumps({'jsonrpc':'2.0','id':n+1,'method':'common:getNodesLatestStatus','params':{}}).encode()
    try:
        req=urllib.request.Request('http://127.0.0.1:25774/api/rpc2',data=payload,
                                   headers={'Content-Type':'application/json'},method='POST')
        obj=json.load(urllib.request.urlopen(req,timeout=5))
        if 'error' in obj:raise RuntimeError(str(obj['error'].get('message')))
        nodes=obj['result']
        st=next(iter(nodes.values()),{})
        stamp=st.get('time')
        times.append(stamp)
        up=int(st.get('net_total_up',0));down=int(st.get('net_total_down',0))
        delta='first' if previous is None else ('delta_bytes='+str(up+down-previous))
        previous=up+down
        print('SAMPLE',n+1,'reported_at=',stamp,'upload_Bps=',st.get('net_out'),
              'download_Bps=',st.get('net_in'),'total_bytes=',up+down,delta)
    except Exception as exc:print('SAMPLE_ERROR',type(exc).__name__,str(exc)[:100])
    if n<6:time.sleep(1)
print('DIFFERENT_REPORT_TIMESTAMPS:',len(set(times)),'out of',len(times))
print('READ_ONLY_CHECK_COMPLETE')
