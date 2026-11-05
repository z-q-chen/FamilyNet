#!/usr/bin/env python3
"""Replace the previous appended Komari overlay with a standalone six-group theme."""
import json
import sqlite3
from pathlib import Path

root = Path("/var/lib/komari")
themes = root / "data/theme"
base = themes / "Glassmorphism"
custom = themes / "Glassmorphism-six-stage"
dbfile = root / "komari.db"

html = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<title>洛杉矶 VPS · 六组网络监控</title>
<style>
:root{color-scheme:dark;--surface:rgba(17,28,47,.82);--border:rgba(177,213,255,.13);--text:#f4f8ff;--muted:#a9bed3;--green:#5ce3af;--red:#ff8b8b;--blue:#79b5ff}
*{box-sizing:border-box}body{margin:0;color:var(--text);font:14px/1.5 system-ui,-apple-system,"Noto Sans SC",sans-serif;background:radial-gradient(ellipse 80% 45% at 90% -10%,#1e5482 0%,transparent 85%),radial-gradient(ellipse 75% 45% at 5% 0%,#265462 0%,transparent 85%),#0a1220;min-height:100vh}
body:before{content:"";pointer-events:none;position:fixed;inset:0;opacity:.14;background-image:linear-gradient(#8dbde622 1px,transparent 1px),linear-gradient(90deg,#8dbde622 1px,transparent 1px);background-size:44px 44px}
.shell{position:relative;max-width:1440px;margin:0 auto;padding:25px 20px 52px}
.header{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:24px}
.brand{display:flex;align-items:center;gap:13px}.logo{display:grid;place-items:center;width:48px;height:48px;border-radius:15px;background:#244968;color:#a5e5ff;font-weight:800;font-size:22px}
h1{font-size:25px;margin:0;letter-spacing:.3px}h2{font-size:18px;margin:0}.subtitle{color:var(--muted);font-size:12px}.live{border:1px solid var(--border);background:rgba(7,20,35,.5);padding:9px 12px;border-radius:100px;font-size:12px;color:#9edbbc}
.live:before{content:"";display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--green);margin-right:8px}
.live.bad:before{background:var(--red)}.section-head{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;margin:26px 0 13px}.section-head span{font-size:12px;color:var(--muted)}
.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:13px}
.card{border:1px solid var(--border);border-radius:19px;background:var(--surface);box-shadow:0 10px 35px #020a1633;backdrop-filter:blur(12px)}
.stat{padding:17px 19px}.k{color:var(--muted);font-size:12px}.v{font-size:25px;font-weight:750;letter-spacing:-.4px;margin-top:9px;font-variant-numeric:tabular-nums}.sub{font-size:12px;color:var(--muted);margin-top:3px}
.meter{width:100%;height:5px;border-radius:8px;background:#4c657250;overflow:hidden;margin-top:12px}.meter>div{height:100%;background:linear-gradient(90deg,#56c6aa,#8edaff);border-radius:8px;max-width:100%}
.group-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
.group{padding:17px}.group-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:13px}.group-head strong{font-size:16px}.region{border-radius:100px;background:#6fa8d21b;color:#b3d3f0;padding:3px 9px;font-size:11px}
.lane{border-top:1px solid #ffffff13;padding:12px 0 5px}.lane-row{display:flex;align-items:center;justify-content:space-between;gap:8px}.ip{font-weight:750;color:#d7e8fd}.value{font-size:18px;font-weight:750;font-variant-numeric:tabular-nums;color:var(--green)}.value.bad{color:var(--red)}
.details{font-size:11px;color:var(--muted);display:flex;justify-content:space-between;gap:8px;margin-top:2px}.bars{height:32px;display:flex;gap:2px;align-items:end;margin:9px 0 2px}.bars i{display:block;flex:1;border-radius:3px 3px 0 0;background:linear-gradient(0deg,#348e90,#6de4b5);min-width:1px}.bars i.fail{background:#ec7d7d}
.footer{color:#a4b8ca;font-size:12px;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-top:28px}.error{color:#ffb09c}
@media(max-width:900px){.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.group-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:620px){.shell{padding:18px 12px 40px}.stats{gap:9px}.group-grid{grid-template-columns:1fr}.stat{padding:15px}.v{font-size:21px}.header h1{font-size:20px}}
</style></head><body>
<main class="shell">
 <header class="header"><div class="brand"><div class="logo">洛</div><div><h1>洛杉矶 VPS 监控</h1><div class="subtitle">FamilyNet · 江苏 / 河北 · 六组十二路网络探测</div></div></div><div id="live" class="live">正在连接探针…</div></header>
 <div class="section-head"><h2>服务器状态</h2><span id="report">读取实时数据中</span></div>
 <section class="stats">
 <article class="stat card"><div class="k">CPU 使用率</div><div class="v" id="cpu">—</div><div class="sub" id="load">负载 —</div><div class="meter"><div id="cpu-p" style="width:0"></div></div></article>
 <article class="stat card"><div class="k">内存 / 硬盘</div><div class="v" id="ram">—</div><div class="sub" id="disk">硬盘 —</div><div class="meter"><div id="ram-p" style="width:0"></div></div></article>
 <article class="stat card"><div class="k">本次探针累计流量（双向）</div><div class="v" id="traffic">—</div><div class="sub" id="traffic-split">上行 — · 下行 —</div><div class="meter"><div id="traffic-p" style="width:0"></div></div></article>
 <article class="stat card"><div class="k">当前网速</div><div class="v" id="speed">—</div><div class="sub" id="network-status">上行 / 下行 · 等待探针上报</div><div class="meter"><div id="net-p" style="width:0"></div></div></article>
 </section>
 <div class="section-head"><h2>六组 · 12 路测试</h2><span>江苏、河北 × 电信 / 联通 / 移动 × IPv4 / IPv6 · 最近 1 小时</span></div>
 <section id="groups" class="group-grid"></section>
 <footer class="footer"><span id="billing">本页为 Komari 探针网卡计数，套餐剩余量请以服务商账单为准</span><span id="sync">每 60 秒检查更新 · 数据精度取决于探针上报频率</span></footer>
</main>
<script>
(function(){
"use strict";
var groupDefs=[["江苏","电信",39,40],["江苏","联通",27,30],["江苏","移动",28,31],["河北","电信",38,34],["河北","联通",32,35],["河北","移动",33,36]];
var $=function(id){return document.getElementById(id)};
var num=function(v){return Number.isFinite(Number(v))?Number(v):0};
var gb=function(n){return (num(n)/1000000000).toFixed(3)+" GB"};
var speed=function(n){var v=Math.max(0,num(n));return v<1024?v.toFixed(0)+" B/s":v<1048576?(v/1024).toFixed(1)+" KB/s":(v/1048576).toFixed(2)+" MB/s"};
var pct=function(x){return Math.max(0,Math.min(100,num(x)))};
var escapeHtml=function(s){return String(s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
var seq=0;
async function rpc(method,params){
 var res=await fetch("/api/rpc2",{method:"POST",headers:{"Content-Type":"application/json"},credentials:"same-origin",cache:"no-store",body:JSON.stringify({jsonrpc:"2.0",id:++seq,method:method,params:params||{}})});
 if(!res.ok)throw Error(method+" HTTP "+res.status);
 var data=await res.json();
 if(data.error)throw Error(data.error.message||method+" error");
 return data.result;
}
function renderGroups(recs){
 var map=new Map();(recs||[]).forEach(function(r){var id=num(r.task_id);if(!map.has(id))map.set(id,[]);map.get(id).push(r)});
 function one(version,id){
  var rs=(map.get(id)||[]).sort(function(a,b){return new Date(a.time)-new Date(b.time)}).slice(-30);
  var last=rs[rs.length-1], valid=rs.filter(function(r){return num(r.value)>=0}),lost=rs.length-valid.length;
  var latency=last?(num(last.value)<0?"丢包":num(last.value)+" ms"):"暂无数据";
  var loss=rs.length?(100*lost/rs.length).toFixed(1)+"%":"—";
  var high=Math.max(100,...valid.map(function(r){return num(r.value)}));
  var bars=rs.length?rs.map(function(r){
   var v=num(r.value),fail=v<0;
   return '<i class="'+(fail?"fail":"")+'" style="height:'+(fail?6:Math.max(8,Math.round(v/high*100)))+'%" title="'+escapeHtml(v<0?"丢包":v+" ms")+'"></i>';
  }).join(""):'<span class="sub">暂无历史采样</span>';
  return '<div class="lane"><div class="lane-row"><span class="ip">'+version+'</span><span class="value'+(latency==="丢包"?' bad':'')+'">'+latency+'</span></div><div class="details"><span>任务 #'+id+'</span><span>丢包 '+loss+' · '+rs.length+' 样本</span></div><div class="bars">'+bars+'</div></div>';
 }
 $("groups").innerHTML=groupDefs.map(function(g,i){
  return '<article class="group card"><div class="group-head"><strong>'+(i+1)+'. '+g[0]+' · '+g[1]+'</strong><span class="region">'+g[0]+'</span></div>'+one("IPv4",g[2])+one("IPv6",g[3])+'</article>';
 }).join("");
}
function renderStatus(meta,st){
 var up=num(st.net_total_up),down=num(st.net_total_down),total=up+down;
 var usedRam=num(st.ram),totalRam=num(st.ram_total),usedDisk=num(st.disk),totalDisk=num(st.disk_total);
 var cpu=pct(st.cpu);
 $("cpu").textContent=cpu.toFixed(1)+"%";$("cpu-p").style.width=cpu+"%";
 $("load").textContent="系统负载 "+(Number.isFinite(Number(st.load))?num(st.load).toFixed(2):"—");
 $("ram").textContent=totalRam?(usedRam/totalRam*100).toFixed(1)+"%":"—";
 $("ram-p").style.width=totalRam?pct(usedRam/totalRam*100)+"%":"0";
 $("disk").textContent="内存 "+gb(usedRam)+" / "+gb(totalRam)+" · 硬盘 "+gb(usedDisk)+" / "+gb(totalDisk);
 $("traffic").textContent=gb(total);
 $("traffic-split").textContent="上行 "+gb(up)+" · 下行 "+gb(down);
 var quota=num(meta.traffic_limit);
 $("traffic-p").style.width=quota?pct(total/quota*100)+"%":"0";
 $("billing").textContent=quota?"探针累计 "+gb(total)+" / 设置额度 "+gb(quota)+"；并非服务商账期用量":"流量为探针网卡累计；服务商套餐剩余额度无法仅凭此数据确认";
 $("speed").textContent=speed(st.net_in);
 $("network-status").textContent="下行 "+speed(st.net_in)+" / 上行 "+speed(st.net_out);
 $("net-p").style.width=pct((num(st.net_in)+num(st.net_out))/1048576*5)+"%";
 var timestamp=st.time?new Date(st.time):null;
 var valid=timestamp&&!isNaN(timestamp.getTime());
 var age=valid?Math.max(0,Math.round((Date.now()-timestamp.getTime())/60000)):9999;
 $("report").textContent=valid?"探针上报 "+timestamp.toLocaleString("zh-CN")+" · "+age+" 分钟前":"探针尚未提供上报时间";
 $("live").textContent=st.online===false?"节点离线":age>10?"数据未及时更新":"探针正常";
 $("live").className="live"+(st.online===false||age>10?" bad":"");
}
var busy=false, knownMeta=null, uuid=null;
async function update(){
 if(busy)return;busy=true;
 try{
  var result=await Promise.all([rpc("common:getNodes"),rpc("common:getNodesLatestStatus")]);
  var nodes=result[0]||{},statuses=result[1]||{},list=Object.values(nodes);
  var meta=list.find(function(n){return /HHost|洛杉矶/i.test(n.name||"")})||list[0];
  if(!meta||!meta.uuid)throw Error("监控节点不存在");
  uuid=meta.uuid;knownMeta=meta;
  var st=statuses[uuid];
  if(!st)throw Error("探针暂无最新状态");
  renderStatus(meta,st);
  var ping=await rpc("public:getPingRecords",{uuid:uuid,hours:"1",max_count:1200});
  renderGroups(ping.records||[]);
  $("sync").textContent="页面获取 "+new Date().toLocaleTimeString("zh-CN")+" · 每60秒刷新 · 最终以探针上报为准";
 }catch(e){
  $("sync").textContent="数据获取失败："+e.message;
  $("live").textContent="监控异常";$("live").className="live bad";
 }finally{busy=false}
}
renderGroups([]);update();setInterval(update,60000);document.addEventListener("visibilitychange",function(){if(!document.hidden)update()});
})();
</script></body></html>'''

custom.mkdir(parents=True, exist_ok=True)
dist = custom / "dist"
dist.mkdir(exist_ok=True)
(dist / "index.html").write_text(html, encoding="utf-8")

manifest = custom / "komari-theme.json"
if manifest.exists():
    m = json.loads(manifest.read_text(encoding="utf-8"))
else:
    m = {"name":"FamilyNet Six-Group","version":"1.0.0","configuration":{"type":"managed","data":[]}}
m["short"] = "Glassmorphism-six-stage"
m["name"] = "FamilyNet · 六组十二路监控"
manifest.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")

old_index = base / "dist/index.html"
if old_index.exists():
    s = old_index.read_text(encoding="utf-8")
    s2 = s.replace('<script src="/assets/fn-six-stage.js?v=1" defer></script>', "")
    if s2 != s:
        old_index.write_text(s2, encoding="utf-8")
    injected = base / "dist/assets/fn-six-stage.js"
    if injected.is_file():
        injected.unlink()

db = sqlite3.connect(str(dbfile), timeout=10)
db.execute('INSERT INTO configs ("key","value") VALUES (?,?) ON CONFLICT("key") DO UPDATE SET "value"=excluded."value"',
           ("theme", json.dumps("Glassmorphism-six-stage")))
db.commit()
db.close()

print("主题已切换：FamilyNet 六组 / 12 路监控")
print("原版上方卡片与旧追加补丁已解除")
print("CPU、网速、上下载累计量从 Komari 最新状态接口读取（精确到 MB）")
print("请刷新 https://207.57.74.12:9443/?v=six2")
