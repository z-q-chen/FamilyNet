#!/usr/bin/env python3
"""Deduplicate Glassmorphism network refresh; reuse original native RPC responses."""
from pathlib import Path
import json, re, sqlite3

root=Path("/var/lib/komari")
dist=root/"data/theme/Glassmorphism/dist"
index=dist/"index.html"
dbfile=root/"komari.db"
if not index.is_file() or not dbfile.is_file():
    raise SystemExit("原主题或数据库不存在，未修改。")

db=sqlite3.connect(f"file:{dbfile}?mode=ro",uri=True)
row=db.execute("SELECT value FROM configs WHERE key='theme'").fetchone()
active=json.loads(row[0]) if row else ""
row=db.execute("SELECT data FROM theme_configurations WHERE short='Glassmorphism'").fetchone()
db.close()
if active!="Glassmorphism" or not row:
    raise SystemExit("当前不是 Glassmorphism 主题，未修改。")
settings=json.loads(row[0])
if not isinstance(settings,dict):
    raise SystemExit("主题配置损坏，未修改。")
settings["dataUpdateInterval"]=3
settings["rpcTransportMode"]="http"

html=index.read_text(encoding="utf-8")
# Remove the previous standalone polling scripts so no duplicate RPC remains.
html=re.sub(
    r'<script\b[^>]*\bsrc=["\x27]/assets/(?:fn-live-network-v\d+|fn-twelve-ping|fn-six-stage)\.js(?:\?[^"\x27]*)?["\x27][^>]*>\s*</script>',
    "", html)
html=re.sub(r'<script\b[^>]*\bsrc=["\x27]/assets/fn-reuse-native-rpc-v\d+\.js(?:\?[^"\x27]*)?["\x27][^>]*>\s*</script>',"",html)
if not re.search(r'<script\b[^>]*\btype=["\x27]module["\x27]',html):
    raise SystemExit("找不到原生 Vite 主程序模块，未修改。")
js="(()=>{\n\"use strict\";\n// Observe the native Glassmorphism JSON-RPC response. No extra network requests.\nconst nativeFetch=window.fetch.bind(window);\nlet metadata={};\nconst fmt=(raw,suffix=\"\")=>{\n const n=Number(raw);\n if(!Number.isFinite(n))return \"—\";\n let value=Math.abs(n),u=0;\n const units=[\"B\",\"KB\",\"MB\",\"GB\",\"TB\",\"PB\"];\n while(value>=1024&&u<units.length-1){value/=1024;u++;}\n return (n<0?\"-\":\"\")+value.toFixed(2)+\" \"+units[u]+suffix;\n};\nconst formatTime=stamp=>{\n const d=new Date(stamp);\n return isNaN(d.getTime())?\"未知\":d.toLocaleTimeString(\"zh-CN\",{hour12:false});\n};\nfunction updateHeaderDate(stamp){\n  const title=[...document.querySelectorAll(\"div.sticky h3\")].find(\n    el=>/洛杉矶.*VPS.*监控|VPS.*监控/.test(el.textContent||\"\")\n  );\n  if(!title)return;\n  const box=title.parentElement;\n  if(!box)return;\n  box.style.position=\"relative\";\n  let date=box.querySelector(\"[data-fn-header-date]\");\n  if(!date){\n    date=document.createElement(\"span\");\n    date.setAttribute(\"data-fn-header-date\",\"\");\n    date.style.cssText=\"position:absolute;left:40px;top:calc(50% + 7px);max-width:calc(100% - 42px);font-size:10px;line-height:1;font-weight:400;opacity:.65;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;pointer-events:none\";\n    box.appendChild(date);\n    title.style.transform=\"translateY(-5px)\";\n  }\n  const d=new Date(stamp);\n  if(Number.isNaN(d.getTime())){date.textContent=\"\";return;}\n  const pad=n=>String(n).padStart(2,\"0\");\n  date.textContent=d.getFullYear()+\"-\"+pad(d.getMonth()+1)+\"-\"+pad(d.getDate())+\" \"+pad(d.getHours())+\":\"+pad(d.getMinutes())+\":\"+pad(d.getSeconds());\n  date.title=\"最近一次探针上报时间（浏览器本地时间）\";\n}\n\n\nfunction findCard(name,totalCount){\n const cards=[...document.querySelectorAll(\".node-card\")];\n let card=cards.find(c=>c.getAttribute(\"aria-label\")===(\"查看节点 \"+name+\" 详情\"));\n if(!card&&cards.length===1&&totalCount===1)card=cards[0];\n return card||null;\n}\nfunction updateCard(card,node,status){\n // Anchor to the exact two native traffic-speed <span> elements in NodeCard.vue.\n const speed=[...card.querySelectorAll(\"span\")].filter(el=>{\n   if(el.children.length)return false;\n   return /^-?[\\d,.]+\\s*(?:B|KB|MB|GB|TB|PB)\\/s$/.test(el.textContent.trim());\n });\n if(speed.length<2)return false;\n const speedPanel=speed[0].parentElement?.parentElement;\n const totalsPanel=speedPanel?.nextElementSibling;\n if(!speedPanel||!totalsPanel)return false;\n const totalRows=[...totalsPanel.children].filter(x=>x.tagName===\"DIV\");\n if(totalRows.length<2)return false;\n const upTotal=totalRows[0].querySelector(\"span:last-of-type\");\n const downTotal=totalRows[1].querySelector(\"span:last-of-type\");\n if(!upTotal||!downTotal)return false;\n speed[0].textContent=fmt(status.net_out,\"/s\");\n speed[1].textContent=fmt(status.net_in,\"/s\");\n upTotal.textContent=fmt(status.net_total_up);\n downTotal.textContent=fmt(status.net_total_down);\n\n // The native quota bar remains reactive. Replace only the displayed caption.\n const icon=card.querySelector('[data-node-metric-icon=\"traffic\"]');\n const trafficBlock=icon?.closest(\".flex.flex-col.gap-1\");\n if(trafficBlock){\n   const raw=[...trafficBlock.children].find(el=>\n      el.tagName===\"DIV\" && el.textContent.includes(\"/\") && /(?:B|KB|MB|GB|TB|∞)/.test(el.textContent));\n   if(raw){\n     raw.style.display=\"none\";\n     let label=trafficBlock.querySelector(\"[data-fn-traffic-live]\");\n     if(!label){\n       label=document.createElement(\"div\");\n       label.setAttribute(\"data-fn-traffic-live\",\"\");\n       label.className=\"text-[11px] text-muted-foreground truncate\";\n       raw.after(label);\n     }\n     const a=Number(status.net_total_up)||0,b=Number(status.net_total_down)||0;\n     const typ=node.traffic_limit_type||\"sum\";\n     const used=typ===\"up\"?a:typ===\"down\"?b:typ===\"max\"?Math.max(a,b):typ===\"min\"?Math.min(a,b):a+b;\n     const limit=Number(node.traffic_limit)||0;\n     label.textContent=fmt(used)+\" / \"+(limit>0?fmt(limit):\"∞\");\n   }\n }\n speedPanel.querySelector(\"[data-fn-net-report]\")?.remove();\n updateHeaderDate(status.time);\n speedPanel.title=\"最新探针上报：\"+(status.time||\"未知\")+\"；网页每3秒查询一次\";\n return true;\n}\n\nfunction updateStatusFromNative(results) {\n  const list=Object.entries(results||{});\n  if(!list.length)return;\n  let latest=null;\n  for(const [id,status] of list){\n    if(!status||typeof status!==\"object\")continue;\n    const info=metadata[id];\n    const cards=[...document.querySelectorAll(\".node-card\")];\n    const card=info?findCard(info.name,list.length):(cards.length===1?cards[0]:null);\n    if(card)updateCard(card,info||{},status);\n    if(status.time&&(!latest||new Date(status.time)>new Date(latest)))latest=status.time;\n  }\n  if(latest)updateHeaderDate(latest);\n}\nwindow.fetch=function(input,init){\n  const body=init&&init.body;\n  if(typeof body!==\"string\"||!body.includes(\"common:getNodes\"))return nativeFetch(input,init);\n  const url=typeof input===\"string\"?input:input&&input.url||\"\";\n  if(!/(?:\\/|^)rpc2(?:\\?|$)/.test(url))return nativeFetch(input,init);\n  let method=\"\";\n  try {method=JSON.parse(body).method||\"\";}catch(_){return nativeFetch(input,init);}\n  if(method!==\"common:getNodes\"&&method!==\"common:getNodesLatestStatus\")return nativeFetch(input,init);\n  const responsePromise=nativeFetch(input,init);\n  responsePromise.then(response=>{\n    if(!response.ok)return;\n    response.clone().json().then(data=>{\n      if(!data||!data.result||data.error)return;\n      if(method===\"common:getNodes\") metadata=data.result;\n      else requestAnimationFrame(()=>updateStatusFromNative(data.result));\n    }).catch(()=>{});\n  }).catch(()=>{});\n  return responsePromise; // Preserve the original fetch response and timing.\n};\n})();"
dest=dist/"assets/fn-reuse-native-rpc-v1.js"
dest.parent.mkdir(parents=True,exist_ok=True)
dest.write_text(js,encoding="utf-8")
# Regular synchronous script executes before the deferred Vite module.
html=re.sub(r'(?=<script\b[^>]*\btype=["\x27]module["\x27])',
            '<script src="/assets/fn-reuse-native-rpc-v1.js?v=1"></script>\n',
            html,count=1)
index.write_text(html,encoding="utf-8")
db=sqlite3.connect(str(dbfile),timeout=15)
db.execute("UPDATE theme_configurations SET data=? WHERE short='Glassmorphism'",
           (json.dumps(settings,ensure_ascii=False),))
db.commit();db.close()
for name in ("fn-live-network-v1.js","fn-live-network-v2.js","fn-twelve-ping.js","fn-six-stage.js"):
    p=dist/"assets"/name
    if p.is_file():p.unlink()
print("完成：保留 Glassmorphism 原生每3秒轮询，删除额外的每3秒 RPC 请求。")
print("网速、累计流量和顶部日期复用同一份原生实时状态响应。")
print("未改探针、12路Ping任务、价格、到期、流量数据。")
print("请打开新的无痕标签页：https://207.57.74.12:9443/?v=single-rpc1")
