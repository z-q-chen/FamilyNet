#!/usr/bin/env python3
"""Enable 3-second native-card network telemetry repaint, preserving original theme."""
from pathlib import Path
import json,re,sqlite3
root=Path('/var/lib/komari')
dist=root/'data/theme/Glassmorphism/dist'
index=dist/'index.html'
dbfile=root/'komari.db'
if not index.is_file() or not dbfile.is_file():
    raise SystemExit('未找到 Glassmorphism 主题或 Komari 数据库；未做修改。')
conn=sqlite3.connect(f'file:{dbfile}?mode=ro',uri=True)
row=conn.execute("SELECT data FROM theme_configurations WHERE short='Glassmorphism'").fetchone()
conn.close()
if not row:raise SystemExit('未找到 Glassmorphism 配置；未做修改。')
data=json.loads(row[0])
if not isinstance(data,dict):raise SystemExit('主题配置格式异常；未做修改。')
data['dataUpdateInterval']=3
data['rpcTransportMode']='http'
# Only a narrow script, no replacement of theme assets or task scheduling.
js="(()=>{\n\"use strict\";\nconst PERIOD=3000;\nconst rpcPaths=[\"/rpc2\",\"/api/rpc2\"];\nlet endpoint=rpcPaths[0], seq=0, busy=false, metaById={}, lastMeta=0;\nconst fmt=(raw,suffix=\"\")=>{\n const n=Number(raw);\n if(!Number.isFinite(n))return \"—\";\n let value=Math.abs(n),u=0;\n const units=[\"B\",\"KB\",\"MB\",\"GB\",\"TB\",\"PB\"];\n while(value>=1024&&u<units.length-1){value/=1024;u++;}\n return (n<0?\"-\":\"\")+value.toFixed(2)+\" \"+units[u]+suffix;\n};\nconst formatTime=stamp=>{\n const d=new Date(stamp);\n return isNaN(d.getTime())?\"未知\":d.toLocaleTimeString(\"zh-CN\",{hour12:false});\n};\nasync function rpc(method,params={}){\n const body=JSON.stringify({jsonrpc:\"2.0\",id:++seq,method,params});\n let res=await fetch(endpoint,{method:\"POST\",cache:\"no-store\",credentials:\"same-origin\",\n  headers:{\"Content-Type\":\"application/json\"},body});\n if(res.status===404&&endpoint===rpcPaths[0]){\n  endpoint=rpcPaths[1];\n  res=await fetch(endpoint,{method:\"POST\",cache:\"no-store\",credentials:\"same-origin\",\n   headers:{\"Content-Type\":\"application/json\"},body});\n }\n if(!res.ok)throw Error(\"HTTP \"+res.status);\n const obj=await res.json();\n if(obj.error)throw Error(obj.error.message||\"RPC error\");\n if(!obj.result||typeof obj.result!==\"object\")throw Error(\"missing status\");\n return obj.result;\n}\nfunction findCard(name,totalCount){\n const cards=[...document.querySelectorAll(\".node-card\")];\n let card=cards.find(c=>c.getAttribute(\"aria-label\")===(\"查看节点 \"+name+\" 详情\"));\n if(!card&&cards.length===1&&totalCount===1)card=cards[0];\n return card||null;\n}\nfunction updateCard(card,node,status){\n // Anchor to the exact two native traffic-speed <span> elements in NodeCard.vue.\n const speed=[...card.querySelectorAll(\"span\")].filter(el=>{\n   if(el.children.length)return false;\n   return /^-?[\\d,.]+\\s*(?:B|KB|MB|GB|TB|PB)\\/s$/.test(el.textContent.trim());\n });\n if(speed.length<2)return false;\n const speedPanel=speed[0].parentElement?.parentElement;\n const totalsPanel=speedPanel?.nextElementSibling;\n if(!speedPanel||!totalsPanel)return false;\n const totalRows=[...totalsPanel.children].filter(x=>x.tagName===\"DIV\");\n if(totalRows.length<2)return false;\n const upTotal=totalRows[0].querySelector(\"span:last-of-type\");\n const downTotal=totalRows[1].querySelector(\"span:last-of-type\");\n if(!upTotal||!downTotal)return false;\n speed[0].textContent=fmt(status.net_out,\"/s\");\n speed[1].textContent=fmt(status.net_in,\"/s\");\n upTotal.textContent=fmt(status.net_total_up);\n downTotal.textContent=fmt(status.net_total_down);\n\n // The native quota bar remains reactive. Replace only the displayed caption.\n const icon=card.querySelector('[data-node-metric-icon=\"traffic\"]');\n const trafficBlock=icon?.closest(\".flex.flex-col.gap-1\");\n if(trafficBlock){\n   const raw=[...trafficBlock.children].find(el=>\n      el.tagName===\"DIV\" && el.textContent.includes(\"/\") && /(?:B|KB|MB|GB|TB|∞)/.test(el.textContent));\n   if(raw){\n     raw.style.display=\"none\";\n     let label=trafficBlock.querySelector(\"[data-fn-traffic-live]\");\n     if(!label){\n       label=document.createElement(\"div\");\n       label.setAttribute(\"data-fn-traffic-live\",\"\");\n       label.className=\"text-[11px] text-muted-foreground truncate\";\n       raw.after(label);\n     }\n     const a=Number(status.net_total_up)||0,b=Number(status.net_total_down)||0;\n     const typ=node.traffic_limit_type||\"sum\";\n     const used=typ===\"up\"?a:typ===\"down\"?b:typ===\"max\"?Math.max(a,b):typ===\"min\"?Math.min(a,b):a+b;\n     const limit=Number(node.traffic_limit)||0;\n     label.textContent=fmt(used)+\" / \"+(limit>0?fmt(limit):\"∞\");\n   }\n }\n // Visible confirmation of the time of the actual agent report, not a fake ticker.\n let stamp=speedPanel.querySelector(\"[data-fn-net-report]\");\n if(!stamp){\n   stamp=document.createElement(\"div\");\n   stamp.setAttribute(\"data-fn-net-report\",\"\");\n   stamp.style.cssText=\"font-size:9px;line-height:1.15;opacity:.65;white-space:nowrap;overflow:hidden;text-overflow:ellipsis\";\n   speedPanel.appendChild(stamp);\n }\n stamp.textContent=\"上报 \"+formatTime(status.time);\n speedPanel.title=\"最新探针上报：\"+(status.time||\"未知\")+\"；网页每3秒查询一次\";\n return true;\n}\nasync function tick(){\n if(busy||document.hidden||navigator.onLine===false)return;\n if(location.pathname!==\"/\")return;\n busy=true;\n try{\n  if(!lastMeta||Date.now()-lastMeta>300000){\n   metaById=await rpc(\"common:getNodes\");\n   lastMeta=Date.now();\n  }\n  const statuses=await rpc(\"common:getNodesLatestStatus\");\n  const keys=Object.keys(statuses);\n  for(const [id,st] of Object.entries(statuses)){\n   const info=metaById[id];\n   if(!info)continue;\n   const card=findCard(info.name,keys.length);\n   if(card)updateCard(card,info,st);\n  }\n  document.documentElement.dataset.fnNetLive=\"ok\";\n }catch(e){\n  document.documentElement.dataset.fnNetLive=\"error\";\n  // Do not overwrite native values on transport failure.\n  console.warn(\"[FamilyNet live network]\",e.message||e);\n }finally{busy=false}\n}\nfunction boot(){\n tick();\n setInterval(tick,PERIOD);\n document.addEventListener(\"visibilitychange\",()=>{if(!document.hidden)tick()});\n window.addEventListener(\"online\",tick);\n}\nif(document.readyState===\"loading\")document.addEventListener(\"DOMContentLoaded\",boot,{once:true});\nelse boot();\n})();"
file=dist/'assets/fn-live-network-v1.js'
file.parent.mkdir(parents=True,exist_ok=True)
file.write_text(js,encoding='utf-8')
src=index.read_text(encoding='utf-8')
src=re.sub(r'<script\s+src=["\x27]/assets/fn-live-network-v\d+\.js(?:\?[^"\x27]*)?["\x27]\s+defer></script>','',src)
if '</body>' not in src:
    raise SystemExit('异常的主题 HTML；未修改入口。')
src=src.replace('</body>','<script src="/assets/fn-live-network-v1.js?v=20261105" defer></script></body>',1)
index.write_text(src,encoding='utf-8')
db=sqlite3.connect(str(dbfile),timeout=12)
db.execute("UPDATE theme_configurations SET data=? WHERE short='Glassmorphism'",(json.dumps(data,ensure_ascii=False),))
db.commit();db.close()
print('已启用：网页可见时每3秒读取并刷新原服务器卡片中的↑↓KB/s和↑↓累计量。')
print('累计使用量显示两位小数；价格、到期时间、12路Ping、探针间隔均未更改。')
print('原生网速面板附带“上报 HH:MM:SS”，用来核对真实探针更新时间。')
print('请在新无痕标签页打开 https://207.57.74.12:9443/?v=net-20261105')
