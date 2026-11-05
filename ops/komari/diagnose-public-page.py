#!/usr/bin/env python3
import json, pathlib, sqlite3, urllib.request, urllib.error, datetime
root=pathlib.Path('/var/lib/komari')
db=sqlite3.connect('file:'+str(root/'komari.db')+'?mode=ro',uri=True)
row=db.execute("SELECT value FROM configs WHERE key='theme'").fetchone()
theme=json.loads(row[0]) if row else 'not-set'
print('ACTIVE_THEME:',theme)
for p in [root/'data/theme/Glassmorphism/dist/index.html',root/'data/theme/Glassmorphism-six-stage/dist/index.html']:
 print('FILE:',str(p), 'exists',p.exists(), 'size',p.stat().st_size if p.exists() else '-')
 if p.exists():
  s=p.read_text(errors='replace')
  print('MARKERS:',{k:(k in s) for k in ['fn-twelve-ping.js','fn-six-stage.js','registerSW.js']})
print('DB_TASK_COUNT:',db.execute('SELECT count(*) FROM ping_tasks').fetchone()[0])
db.close()
def req(url, payload=None):
 try:
  hdr={'Content-Type':'application/json'} if payload else {}
  r=urllib.request.urlopen(urllib.request.Request(url,data=json.dumps(payload).encode() if payload else None,headers=hdr),timeout=6)
  return r.status,r.read(500000).decode(errors='replace')
 except Exception as e:return 'ERROR',str(e)
site='http://127.0.0.1:25774'
for path in ['/','/assets/fn-twelve-ping.js?v=11','/api/public','/api/task/ping']:
 status,body=req(site+path)
 print('LOCAL_HTTP:',path,status,'bytes',len(body),'snippet',body[:90].replace('\n',' '))
 if path=='/' and status==200:print('SERVED_HTML_MARKERS:',{k:(k in body) for k in ['fn-twelve-ping.js','fn-six-stage.js','registerSW.js']})
 if path=='/api/public' and status==200:
  try:
   d=json.loads(body).get('data',{})
   print('PUBLIC_THEME:',d.get('theme'),'hideGeneralCard:',d.get('theme_settings',{}).get('hideGeneralCard'))
  except Exception:pass
def rpc(method):
 status,body=req(site+'/api/rpc2',{'jsonrpc':'2.0','id':1,'method':method,'params':{}})
 if status!=200:print('RPC:',method,status,body[:130]);return {}
 try:
  obj=json.loads(body)
  if obj.get('error'):print('RPC_ERROR:',method,obj.get('error',{}).get('message'));return {}
  return obj.get('result') or {}
 except Exception:print('RPC_INVALID:',method,body[:150]);return {}
nodes=rpc('common:getNodes')
status=rpc('common:getNodesLatestStatus')
if isinstance(nodes,dict) and isinstance(status,dict):
 for uuid, meta in list(nodes.items())[:3]:
  st=status.get(uuid,{})
  print('NODE:',meta.get('name'),'UUID_short:',str(uuid)[:6], 'UPDATED:',st.get('time'),'ONLINE:',st.get('online'),
        'NET_TOTAL_UP:',st.get('net_total_up'),'NET_TOTAL_DOWN:',st.get('net_total_down'),
        'PING_TASKS_IN_STATUS:',len(st.get('ping') or {}))
print('CHECK_COMPLETE: read-only')
