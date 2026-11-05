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
style.textContent=`.fn12{display:flex;flex-direction:column;gap:12px;width:100%;margin:0;padding:0;color:inherit;cursor:default;font-size:12px}
.fn12 *{box-sizing:border-box}.fn12-panel{background:rgba(122,142,179,.085);border-radius:17px;padding:16px 18px}
.fn12-title{display:flex;align-items:center;gap:10px;font-weight:650;font-size:14px;margin-bottom:12px}
.fn12-dot{width:12px;height:12px;flex:none;border-radius:100%;background:#a4e73c}.fn12-dot.warn{background:#efda41}.fn12-dot.bad{background:#e77777}.fn12-dot.empty{background:#8b9bad}
.fn12-summary{display:flex;justify-content:space-between;gap:8px;font-size:12px;margin-bottom:5px}
.fn12-line{display:grid;grid-template-columns:107px minmax(0,1fr);gap:6px;align-items:center;margin-top:8px}
.fn12-label{font-size:12px;opacity:.75;white-space:nowrap}.fn12-bars{display:grid;grid-template-columns:repeat(20,minmax(0,1fr));gap:3px;align-items:center;min-width:0}
.fn12-bars i{display:block;border-radius:2px;height:11px;background:#a4e63d}.fn12-bars i.warn{background:#f2d53c}.fn12-bars i.bad{background:#e47777}.fn12-bars i.empty{background:rgba(140,160,180,.16)}
.fn12-bars.failure i{background:#2ac996}.fn12-bars.failure i.bad{background:#e47777}
@media(max-width:600px){.fn12-panel{padding:14px}.fn12-line{grid-template-columns:90px minmax(0,1fr);gap:4px}.fn12-bars{gap:2px}.fn12-label,.fn12-summary{font-size:11px}}`;document.head.appendChild(style);
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
box.innerHTML='<div class="fn12-list"></div>';
box.addEventListener('click',e=>e.stopPropagation());
old.before(box);old.style.display='none';old.setAttribute('data-fn12-old','1');
}
}
function barrow(records, failures){
const rs=records.slice(-20);let html='';
for(let i=rs.length;i<20;i++)html+='<i class="empty"></i>';
for(const r of rs){const v=Number(r.value);
const color=v<0?'bad':(!failures&&v>150?'warn':'');
html+='<i class="'+color+'" title="'+(v<0?'探测失败':v+'ms')+'"></i>';}
return '<span class="fn12-bars'+(failures?' failure':'')+'">'+html+'</span>';
}
function panel(id,name,byId,ping){
const rs=(byId.get(id)||[]).slice().sort((a,b)=>new Date(a.time)-new Date(b.time));
const p=ping?.[String(id)],last=rs.at(-1);
const latest=p&&Number.isFinite(Number(p.latest))?Number(p.latest):last?Number(last.value):null;
const loss=p&&Number.isFinite(Number(p.loss))?Number(p.loss):rs.length?100*rs.filter(r=>Number(r.value)<0).length/rs.length:null;
const dot=latest===null?'empty':latest<0?'bad':latest>150?'warn':'';
return '<section class="fn12-panel"><div class="fn12-title"><i class="fn12-dot '+dot+'"></i>'+name+'</div>'+
'<div class="fn12-summary"><span>延迟 '+(latest===null?'—':latest<0?'不可达':latest+' ms')+'</span>'+
'<span>连接失败率 '+(loss===null?'—':loss.toFixed(1)+'%')+'</span></div>'+
'<div class="fn12-line"><span class="fn12-label">延迟</span>'+barrow(rs,false)+'</div>'+
'<div class="fn12-line"><span class="fn12-label">连接失败率</span>'+barrow(rs,true)+'</div></section>';
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
const tasks=[...groups.map(g=>[g[2],g[0]+' · '+g[1]+' IPv4']),...groups.map(g=>[g[3],g[0]+' · '+g[1]+' IPv6'])];
el.querySelector('.fn12-list').innerHTML=tasks.map(([id,name])=>panel(id,name,m,st.ping)).join('');
}catch(e){document.querySelectorAll('.fn12-list').forEach(e1=>e1.textContent='Ping 数据读取失败：'+e.message)}finally{busy=false}
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
index.write_text(html.replace('</body>','<script src="/assets/fn-twelve-ping.js?v=11" defer></script></body>',1),encoding='utf-8')
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
