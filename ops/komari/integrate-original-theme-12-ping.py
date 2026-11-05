#!/usr/bin/env python3
import json, sqlite3, re
from pathlib import Path
root=Path('/var/lib/komari')
folder=root/'data/theme/Glassmorphism/dist'
index=folder/'index.html'
if not index.is_file(): raise SystemExit('原 Glassmorphism 主题入口不存在')
js=r"""(() => {
'use strict';
const groups=[['江苏','电信',39,40],['江苏','联通',27,30],['江苏','移动',28,31],['河北','电信',38,34],['河北','联通',32,35],['河北','移动',33,36]];
const style=document.createElement('style');
style.textContent=`
.fn12{width:100%;margin:6px 0;color:inherit;cursor:default;container-type:inline-size;font-size:12px}
.fn12 *{box-sizing:border-box}.fn12-h{display:flex;justify-content:space-between;gap:6px;margin-bottom:8px}
.fn12-info{font-size:10px;opacity:.7}.fn12-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.fn12-group{border:1px solid rgba(140,170,200,.13);border-radius:10px;padding:8px;background:rgba(110,145,170,.09);min-width:0}
.fn12-title{font-weight:700;margin-bottom:5px}
.fn12-lane{display:grid;grid-template-columns:23px 55px minmax(22px,1fr) 33px;gap:4px;align-items:center;border-top:1px solid rgba(140,170,200,.12);min-height:25px}
.fn12-ip,.fn12-loss{font-size:10px;opacity:.75}.fn12-v{font-size:11px;font-weight:700;white-space:nowrap;font-variant-numeric:tabular-nums}
.fn12-bars{height:15px;display:flex;gap:1px;align-items:end;min-width:0}
.fn12-bars i{display:block;flex:1;background:#47c797;border-radius:2px 2px 0 0;min-width:1px}.fn12-bars i.bad{background:#e27676}
.fn12-total{opacity:.75;font-size:10px;text-align:right;margin-top:6px}
@container(max-width:750px){.fn12-grid{grid-template-columns:repeat(2,minmax(0,1fr))!important}}
@container(max-width:470px){.fn12-grid{grid-template-columns:1fr!important}}
`;document.head.appendChild(style);
let req=0,busy=false;
async function rpc(method,params={}) {
const r=await fetch('/api/rpc2',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify({jsonrpc:'2.0',id:++req,method,params})});
if(!r.ok)throw Error('HTTP '+r.status);
const v=await r.json();if(v.error)throw Error(v.error.message);return v.result;
}
function mount(){
for(const card of document.querySelectorAll('.node-card')){
if(card.querySelector('.fn12'))continue;
const bar=card.querySelector('[data-node-ping-bars="latency"]');
const old=bar?.closest('button')?.parentElement || card.querySelector('[data-three-net-ping]');
if(!old||!old.parentElement)continue;
const box=document.createElement('section');box.className='fn12';
box.innerHTML='<div class="fn12-h"><b>江苏 / 河北 · 六组十二路</b><span class="fn12-info">读取中…</span></div><div class="fn12-grid"></div><div class="fn12-total"></div>';
box.addEventListener('click',e=>e.stopPropagation());
old.before(box);old.style.display='none';old.setAttribute('data-fn12-old','1');
}
}
function bars(rs){
if(!rs.length)return '—';
let hi=Math.max(100,...rs.filter(r=>Number(r.value)>=0).map(r=>Number(r.value)));
return '<span class="fn12-bars">'+rs.map(r=>{let v=Number(r.value);return '<i'+(v<0?' class="bad"':'')+' style="height:'+(v<0?5:Math.max(8,Math.round(100*v/hi)))+'%"></i>'}).join('')+'</span>';
}
function lane(ip,id,m,now){
let rs=(m.get(id)||[]).sort((a,b)=>new Date(a.time)-new Date(b.time)).slice(-24);
let p=now?.[String(id)];
let value=p&&Number.isFinite(Number(p.latest))?Number(p.latest):rs.length?Number(rs.at(-1).value):null;
let loss=p&&Number.isFinite(Number(p.loss))?Number(p.loss).toFixed(1):rs.length?(rs.filter(r=>Number(r.value)<0).length*100/rs.length).toFixed(1):'—';
return '<div class="fn12-lane"><span class="fn12-ip">'+ip+'</span><span class="fn12-v">'+(value===null?'—':value<0?'丢包':value+'ms')+'</span>'+bars(rs)+'<span class="fn12-loss">'+(loss==='—'?'—':loss+'%')+'</span></div>';
}
async function update(){
mount();if(busy||document.hidden||location.pathname!=='/')return;busy=true;
try{
const [nodes,sts]=await Promise.all([rpc('common:getNodes'),rpc('common:getNodesLatestStatus')]);
const list=Object.values(nodes||{});
const node=list.find(n=>/HHost|洛杉矶/i.test(n.name||''))||list[0];
if(!node)return;
const cards=[...document.querySelectorAll('.node-card')];
const card=cards.find(c=>(c.getAttribute('aria-label')||'').includes(node.name))||(cards.length===1?cards[0]:null);
const el=card?.querySelector('.fn12');if(!el)return;
const hist=await rpc('public:getPingRecords',{uuid:node.uuid,hours:'1'});
let m=new Map();(hist.records||[]).forEach(r=>{let id=Number(r.task_id);if(!m.has(id))m.set(id,[]);m.get(id).push(r)});
const st=sts?.[node.uuid]||{};
el.querySelector('.fn12-grid').innerHTML=groups.map(g=>'<div class="fn12-group"><div class="fn12-title">'+g[0]+' · '+g[1]+'</div>'+lane('v4',g[2],m,st.ping)+lane('v6',g[3],m,st.ping)+'</div>').join('');
el.querySelector('.fn12-info').textContent='最近1小时 · 获取 '+new Date().toLocaleTimeString('zh-CN');
const up=Number(st.net_total_up),down=Number(st.net_total_down);
el.querySelector('.fn12-total').textContent=Number.isFinite(up)&&Number.isFinite(down)?'探针累计 ↑'+(up/1e9).toFixed(4)+'GB ↓'+(down/1e9).toFixed(4)+'GB'+(st.time?' · 上报 '+new Date(st.time).toLocaleTimeString('zh-CN'):''):'';
}catch(e){document.querySelectorAll('.fn12-info').forEach(e1=>e1.textContent='数据错误：'+e.message)}finally{busy=false}
}
function start(){mount();update();setInterval(mount,3000);setInterval(update,30000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)update()})}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();"""
asset=folder/'assets/fn-twelve-ping.js'
asset.parent.mkdir(parents=True,exist_ok=True)
asset.write_text(js,encoding='utf-8')
html=index.read_text(encoding='utf-8')
html=html.replace('<script src="/assets/fn-six-stage.js?v=1" defer></script>','')
html=re.sub(r'<script src="/assets/fn-twelve-ping\.js\?v=\d+" defer></script>','',html)
if '</body>' not in html: raise SystemExit('主题入口异常')
index.write_text(html.replace('</body>','<script src="/assets/fn-twelve-ping.js?v=5" defer></script></body>',1),encoding='utf-8')
db=sqlite3.connect(str(root/'komari.db'))
db.execute('INSERT INTO configs (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('theme',json.dumps('Glassmorphism')))
row=db.execute('SELECT data FROM theme_configurations WHERE short=?',('Glassmorphism',)).fetchone()
if row:
 cfg=json.loads(row[0]);cfg['threeNetPingEnabled']=False;cfg['threeNetPingSparkline']=False
 db.execute('UPDATE theme_configurations SET data=? WHERE short=?',(json.dumps(cfg,ensure_ascii=False),'Glassmorphism'))
db.commit();db.close()
print('完成：原 Glassmorphism 页面 + 12 路 Ping（替换原延迟区，不重复）')
print('其余在线时长、价格、剩余价值、流量、详情图表继续由原主题显示')
print('打开 https://207.57.74.12:9443/?v=5')
