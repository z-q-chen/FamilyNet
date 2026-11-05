#!/usr/bin/env python3
"""Restore live 3-second network values, without a second unconditional poll."""
from pathlib import Path
import json
import re
import sqlite3
import sys

root=Path("/var/lib/komari")
dist=root/"data/theme/Glassmorphism/dist"
index=dist/"index.html"
dbfile=root/"komari.db"
if not index.is_file() or not dbfile.is_file():
    sys.exit("主题入口或数据库不存在，未做修改。")
db=sqlite3.connect(f"file:{dbfile}?mode=ro",uri=True)
row=db.execute("SELECT data FROM theme_configurations WHERE short='Glassmorphism'").fetchone()
theme=db.execute("SELECT value FROM configs WHERE key='theme'").fetchone()
db.close()
if not row or not theme or json.loads(theme[0])!="Glassmorphism":
    sys.exit("当前未启用 Glassmorphism，未做修改。")
settings=json.loads(row[0])
if not isinstance(settings,dict):
    sys.exit("主题配置不正确，未做修改。")
settings["dataUpdateInterval"]=3
settings["rpcTransportMode"]="http"

html=index.read_text(encoding="utf-8")
if not re.search(r'<script\b[^>]*\btype=["\x27]module["\x27]',html):
    sys.exit("没有找到原主题主程序入口，未做修改。")
# Discard all previously added network-refresh scripts (including the failed
# response-only version) before adding this single smarter fallback.
html=re.sub(
    r'<script\b[^>]*\bsrc=["\x27]/assets/(?:fn-live-network-v\d+|fn-reuse-native-rpc-v\d+|fn-smart-refresh-v\d+|fn-twelve-ping|fn-six-stage)\.js(?:\?[^"\x27]*)?["\x27][^>]*>\s*</script>',
    "",html)
js="(function(){\n\"use strict\";\nif(window.__fnSmartRefreshInstalled)return;\nwindow.__fnSmartRefreshInstalled=true;\nconst INTERVAL=3000;\nconst FRESH_NATIVE_MS=4500;\nconst nativeFetch=window.fetch.bind(window);\nlet latestNativeSuccess=0, seq=0, inFlight=false;\nlet endpoint=\"/api/rpc2\", meta={}, lastMetaAt=0;\nconst fmt=(raw,suffix=\"\")=>{\n const n=Number(raw);\n if(!Number.isFinite(n))return \"—\";\n let value=Math.abs(n),u=0;\n const units=[\"B\",\"KB\",\"MB\",\"GB\",\"TB\",\"PB\"];\n while(value>=1024&&u<units.length-1){value/=1024;u++;}\n return (n<0?\"-\":\"\")+value.toFixed(2)+\" \"+units[u]+suffix;\n};\nconst formatTime=stamp=>{\n const d=new Date(stamp);\n return isNaN(d.getTime())?\"未知\":d.toLocaleTimeString(\"zh-CN\",{hour12:false});\n};\nfunction updateHeaderDate(stamp){\n  const title=[...document.querySelectorAll(\"div.sticky h3\")].find(\n    el=>/洛杉矶.*VPS.*监控|VPS.*监控/.test(el.textContent||\"\")\n  );\n  if(!title)return;\n  const box=title.parentElement;\n  if(!box)return;\n  box.style.position=\"relative\";\n  let date=box.querySelector(\"[data-fn-header-date]\");\n  if(!date){\n    date=document.createElement(\"span\");\n    date.setAttribute(\"data-fn-header-date\",\"\");\n    date.style.cssText=\"position:absolute;left:40px;top:calc(50% + 7px);max-width:calc(100% - 42px);font-size:10px;line-height:1;font-weight:400;opacity:.65;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;pointer-events:none\";\n    box.appendChild(date);\n    title.style.transform=\"translateY(-5px)\";\n  }\n  const d=new Date(stamp);\n  if(Number.isNaN(d.getTime())){date.textContent=\"\";return;}\n  const pad=n=>String(n).padStart(2,\"0\");\n  date.textContent=d.getFullYear()+\"-\"+pad(d.getMonth()+1)+\"-\"+pad(d.getDate())+\" \"+pad(d.getHours())+\":\"+pad(d.getMinutes())+\":\"+pad(d.getSeconds());\n  date.title=\"最近一次探针上报时间（浏览器本地时间）\";\n}\n\n\nfunction findCard(name,totalCount){\n const cards=[...document.querySelectorAll(\".node-card\")];\n let card=cards.find(c=>c.getAttribute(\"aria-label\")===(\"查看节点 \"+name+\" 详情\"));\n if(!card&&cards.length===1&&totalCount===1)card=cards[0];\n return card||null;\n}\nfunction updateCard(card,node,status){\n // Anchor to the exact two native traffic-speed <span> elements in NodeCard.vue.\n const speed=[...card.querySelectorAll(\"span\")].filter(el=>{\n   if(el.children.length)return false;\n   return /^-?[\\d,.]+\\s*(?:B|KB|MB|GB|TB|PB)\\/s$/.test(el.textContent.trim());\n });\n if(speed.length<2)return false;\n const speedPanel=speed[0].parentElement?.parentElement;\n const totalsPanel=speedPanel?.nextElementSibling;\n if(!speedPanel||!totalsPanel)return false;\n const totalRows=[...totalsPanel.children].filter(x=>x.tagName===\"DIV\");\n if(totalRows.length<2)return false;\n const upTotal=totalRows[0].querySelector(\"span:last-of-type\");\n const downTotal=totalRows[1].querySelector(\"span:last-of-type\");\n if(!upTotal||!downTotal)return false;\n speed[0].textContent=fmt(status.net_out,\"/s\");\n speed[1].textContent=fmt(status.net_in,\"/s\");\n upTotal.textContent=fmt(status.net_total_up);\n downTotal.textContent=fmt(status.net_total_down);\n\n // The native quota bar remains reactive. Replace only the displayed caption.\n const icon=card.querySelector('[data-node-metric-icon=\"traffic\"]');\n const trafficBlock=icon?.closest(\".flex.flex-col.gap-1\");\n if(trafficBlock){\n   const raw=[...trafficBlock.children].find(el=>\n      el.tagName===\"DIV\" && el.textContent.includes(\"/\") && /(?:B|KB|MB|GB|TB|∞)/.test(el.textContent));\n   if(raw){\n     raw.style.display=\"none\";\n     let label=trafficBlock.querySelector(\"[data-fn-traffic-live]\");\n     if(!label){\n       label=document.createElement(\"div\");\n       label.setAttribute(\"data-fn-traffic-live\",\"\");\n       label.className=\"text-[11px] text-muted-foreground truncate\";\n       raw.after(label);\n     }\n     const a=Number(status.net_total_up)||0,b=Number(status.net_total_down)||0;\n     const typ=node.traffic_limit_type||\"sum\";\n     const used=typ===\"up\"?a:typ===\"down\"?b:typ===\"max\"?Math.max(a,b):typ===\"min\"?Math.min(a,b):a+b;\n     const limit=Number(node.traffic_limit)||0;\n     label.textContent=fmt(used)+\" / \"+(limit>0?fmt(limit):\"∞\");\n   }\n }\n speedPanel.querySelector(\"[data-fn-net-report]\")?.remove();\n updateHeaderDate(status.time);\n speedPanel.title=\"最新探针上报：\"+(status.time||\"未知\")+\"；网页每3秒查询一次\";\n return true;\n}\n\nasync function rpc(method, params={}){\n  const payload=JSON.stringify({jsonrpc:\"2.0\",id:++seq,method,params});\n  const options={method:\"POST\",credentials:\"same-origin\",cache:\"no-store\",\n     headers:{\"Content-Type\":\"application/json\"},body:payload};\n  let res=await nativeFetch(endpoint,options);\n  if(res.status===404){\n    endpoint=endpoint===\"/api/rpc2\"?\"/rpc2\":\"/api/rpc2\";\n    res=await nativeFetch(endpoint,options);\n  }\n  if(!res.ok)throw Error(\"RPC \"+res.status);\n  const data=await res.json();\n  if(data.error)throw Error(data.error.message||\"RPC error\");\n  if(!data.result||typeof data.result!==\"object\")throw Error(\"Empty RPC\");\n  return data.result;\n}\nfunction applyStatuses(statuses){\n  if(!statuses || typeof statuses!==\"object\")return;\n  const list=Object.entries(statuses);\n  const cards=[...document.querySelectorAll(\".node-card\")];\n  let topStamp=\"\";\n  for(const [id,status] of list){\n    if(!status||typeof status!==\"object\")continue;\n    const node=meta[id]||{};\n    let target=node.name ? findCard(node.name,list.length) : null;\n    if(!target && cards.length===1 && list.length===1) target=cards[0];\n    if(target)updateCard(target,node,status);\n    if(status.time && (!topStamp || Date.parse(status.time)>Date.parse(topStamp)))topStamp=status.time;\n  }\n  if(topStamp)updateHeaderDate(topStamp);\n}\nfunction observedNative(input, init){\n  const url=typeof input===\"string\"?input:(input&&input.url)||\"\";\n  if(!/(?:\\/|^)rpc2(?:\\?|$)/.test(url))return null;\n  let body=init&&init.body;\n  if(typeof body!==\"string\")return null;\n  try{\n    const data=JSON.parse(body);\n    return data.method===\"common:getNodes\"||data.method===\"common:getNodesLatestStatus\"\n      ? data.method : null;\n  }catch(_){return null;}\n}\nwindow.fetch=function(input,init){\n  const method=observedNative(input,init);\n  const promise=nativeFetch(input,init);\n  if(method){\n    promise.then(response=>{\n      if(!response.ok)return;\n      response.clone().json().then(data=>{\n        if(!data || data.error || !data.result)return;\n        if(method===\"common:getNodes\"){\n          meta=data.result;\n          lastMetaAt=Date.now();\n        }else{\n          latestNativeSuccess=Date.now();\n          requestAnimationFrame(()=>applyStatuses(data.result));\n        }\n      }).catch(()=>{});\n    }).catch(()=>{});\n  }\n  return promise;\n};\nasync function fallback(){\n  if(inFlight || document.hidden || navigator.onLine===false || location.pathname!==\"/\")return;\n  // When the original Vue app is refreshing correctly, do not duplicate its request.\n  if(Date.now()-latestNativeSuccess<FRESH_NATIVE_MS)return;\n  inFlight=true;\n  try{\n    if(!lastMetaAt||Date.now()-lastMetaAt>300000){\n      meta=await rpc(\"common:getNodes\");\n      lastMetaAt=Date.now();\n    }\n    const statuses=await rpc(\"common:getNodesLatestStatus\");\n    requestAnimationFrame(()=>applyStatuses(statuses));\n    document.documentElement.dataset.fnSmartRefresh=\"fallback-active\";\n  }catch(e){\n    console.warn(\"[FamilyNet smart refresh]\",e&&e.message||e);\n    document.documentElement.dataset.fnSmartRefresh=\"error\";\n  }finally{inFlight=false;}\n}\nfunction start(){\n  fallback();\n  setInterval(fallback,INTERVAL);\n  document.addEventListener(\"visibilitychange\",()=>{if(!document.hidden)fallback()});\n  window.addEventListener(\"online\",fallback);\n}\nif(document.readyState===\"loading\")document.addEventListener(\"DOMContentLoaded\",start,{once:true});\nelse start();\n})();"
asset=dist/"assets/fn-smart-refresh-v3.js"
asset.parent.mkdir(parents=True,exist_ok=True)
asset.write_text(js,encoding="utf-8")
# Run before Vite, so we can observe native fetches without changing their results.
new_html=re.sub(
    r'(?=<script\b[^>]*\btype=["\x27]module["\x27])',
    '<script src="/assets/fn-smart-refresh-v3.js?v=3"></script>\n',
    html,count=1)
if new_html==html:sys.exit("无法注入页面，未修改原入口。")
index.write_text(new_html,encoding="utf-8")
db=sqlite3.connect(str(dbfile),timeout=15)
db.execute("UPDATE theme_configurations SET data=? WHERE short='Glassmorphism'",
           (json.dumps(settings,ensure_ascii=False),))
db.commit();db.close()
# Remove obsolete unreferenced helper scripts. Native built JS is untouched.
for name in ("fn-live-network-v1.js","fn-live-network-v2.js",
             "fn-reuse-native-rpc-v1.js","fn-twelve-ping.js","fn-six-stage.js"):
    f=dist/"assets"/name
    if f.is_file():f.unlink()
print("已应用：网页默认每3秒读取最新状态；原生状态请求正常时不重复发送。")
print("如原生页面没有持续请求，自动补充3秒查询并刷新网速与流量。")
print("保留顶部上报日期、两位小数、12路Ping、价格/到期等功能。")
print("不修改探针和 Ping 调度，不重启服务。")
print("请打开新无痕标签页：https://207.57.74.12:9443/?v=smart3")
