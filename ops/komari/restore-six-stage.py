from pathlib import Path

base = Path('/var/lib/komari/data/theme/Glassmorphism/dist')
index = base / 'index.html'
asset = base / 'assets/fn-six-stage.js'

js = r'''(() => {
  const GROUPS = [
    ['江苏', '电信', 39, 40],
    ['江苏', '联通', 27, 30],
    ['江苏', '移动', 28, 31],
    ['河北', '电信', 38, 34],
    ['河北', '联通', 32, 35],
    ['河北', '移动', 33, 36],
  ];
  const STYLE = `
#fn-six-stage { box-sizing:border-box; max-width:1460px; width:calc(100% - 40px); margin:26px auto 48px; position:relative; z-index:5; color:#f3f7fb; font-family:inherit; }
#fn-six-stage * {box-sizing:border-box}
#fn-six-stage .fn-title {display:flex; flex-wrap:wrap; gap:8px 18px; align-items:baseline; margin-bottom:14px;}
#fn-six-stage h2 {font-size:22px; font-weight:700; margin:0; color:#f3f7fb}
#fn-six-stage .fn-sub {font-size:12px; color:#c0d0e0}
#fn-six-stage .fn-grid {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
#fn-six-stage .fn-card {padding:17px 19px 16px; border-radius:17px; background:rgba(15,22,37,.85); border:1px solid rgba(255,255,255,.13); box-shadow:0 9px 24px rgba(0,0,0,.12); backdrop-filter:blur(10px)}
#fn-six-stage .fn-cardhead {display:flex; justify-content:space-between; gap:8px;align-items:center; font-size:16px; font-weight:700; margin-bottom:11px; }
#fn-six-stage .fn-region {font-size:11px;color:#a9bfd3;background:rgba(94,140,180,.15);padding:4px 8px;border-radius:100px}
#fn-six-stage .fn-lane {display:grid;grid-template-columns:40px minmax(0,1fr) auto;align-items:center;gap:8px;min-height:54px;border-top:1px solid rgba(255,255,255,.08)}
#fn-six-stage .fn-ip {font-weight:700;font-size:13px;color:#d9e8f5}
#fn-six-stage .fn-lat {font-size:14px;font-weight:700;min-width:67px;text-align:right;font-variant-numeric:tabular-nums}
#fn-six-stage .fn-loss {font-size:11px;font-weight:400;color:#a9bed0;margin-top:3px}
#fn-six-stage .fn-bars {height:25px;display:flex;align-items:flex-end;gap:2px;min-width:0}
#fn-six-stage .fn-bars i {display:block; flex:1; min-width:1px; border-radius:2px 2px 0 0; background:#48cf91;opacity:.88}
#fn-six-stage .fn-bars i.lost {background:#ef7777; height:6px !important}
#fn-six-stage .fn-empty {color:#8394a5;font-size:12px}
#fn-six-stage .fn-warn {color:#ffb76a}
@media(max-width:900px){#fn-six-stage .fn-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:620px){#fn-six-stage{width:calc(100% - 24px)} #fn-six-stage .fn-grid{grid-template-columns:1fr}}
`;
  function json(path) {
    return fetch(path, { credentials:'same-origin', cache:'no-store' })
      .then(r => { if (!r.ok) throw Error('HTTP ' + r.status); return r.json(); })
      .then(v => { if (v.status === 'error') throw Error(v.message || 'API error'); return v.data ?? v; });
  }
  const chart = rs => {
    if (!rs.length) return '<span class="fn-empty">暂无采样数据</span>';
    const valid = rs.filter(r => Number(r.value) >= 0).map(r => Number(r.value));
    const ceiling = Math.max(100, ...valid, 1);
    return '<div class="fn-bars">' + rs.slice(-30).map(r => {
      const v = Number(r.value);
      const lost = v < 0;
      return '<i class="' + (lost ? 'lost' : '') + '" style="height:' + (lost ? 6 : Math.max(12, Math.min(100, v/ceiling*100))) + '%"></i>';
    }).join('') + '</div>';
  };
  function lane(ip, id, byTask, available) {
    const rs = (byTask.get(id) || []).slice().sort((a,b) => new Date(a.time)-new Date(b.time));
    const last = rs.at(-1);
    const lost = rs.filter(r => Number(r.value) < 0).length;
    const loss = rs.length ? (100 * lost / rs.length).toFixed(1) + '%' : '—';
    const value = !available.has(id) ? '任务缺失' : !last ? '无数据' : Number(last.value) < 0 ? '不可达' : last.value + ' ms';
    return '<div class="fn-lane"><span class="fn-ip">' + ip + '</span>' + chart(rs) +
      '<div class="fn-lat' + (!last || Number(last.value)<0 ? ' fn-warn' : '') + '">' + value +
      '<div class="fn-loss">丢包 ' + loss + '</div></div></div>';
  }
  function render(root, records, tasks) {
    const available = new Set(tasks.map(t => Number(t.id)));
    const byTask = new Map();
    records.forEach(r => {
      const id = Number(r.task_id);
      if (!byTask.has(id)) byTask.set(id, []);
      byTask.get(id).push(r);
    });
    root.querySelector('.fn-grid').innerHTML = GROUPS.map(([region,isp,v4,v6],i) =>
      '<article class="fn-card"><div class="fn-cardhead"><span>'+(i+1)+'. '+region+' · '+isp+'</span><span class="fn-region">'+region+'</span></div>'+
      lane('IPv4',v4,byTask,available)+lane('IPv6',v6,byTask,available)+'</article>'
    ).join('');
  }
  async function refresh(root) {
    const status = root.querySelector('.fn-sub');
    try {
      const [nodes, tasks] = await Promise.all([json('/api/nodes'), json('/api/task/ping')]);
      const target = nodes.find(n => String(n.name || '').includes('HHost')) || nodes[0];
      if (!target || !target.uuid) throw Error('未找到可见 VPS 节点');
      const history = await json('/api/records/ping?uuid=' + encodeURIComponent(target.uuid) + '&hours=1');
      render(root, history.records || [], tasks);
      status.textContent = '江苏 / 河北 · 12 路实时测试 · 最近 1 小时 · 更新 ' + new Date().toLocaleTimeString('zh-CN');
    } catch (e) { status.textContent = '监控数据读取失败：' + e.message; }
  }
  function init() {
    if (document.getElementById('fn-six-stage')) return;
    const style = document.createElement('style'); style.textContent = STYLE; document.head.appendChild(style);
    const root = document.createElement('section'); root.id = 'fn-six-stage';
    root.innerHTML = '<div class="fn-title"><h2>六阶段网络监控</h2><span class="fn-sub">正在读取江苏、河北 12 组 Ping 数据…</span></div><div class="fn-grid"></div>';
    document.body.appendChild(root);
    const tick = () => {
      root.style.display = location.pathname === '/' ? '' : 'none';
      if (root.style.display !== 'none' && !document.hidden) refresh(root);
    };
    tick(); setInterval(tick, 60000);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();'''
asset.parent.mkdir(parents=True, exist_ok=True)
asset.write_text(js, encoding='utf-8')
html = index.read_text(encoding='utf-8')
marker = '<script src="/assets/fn-six-stage.js?v=1" defer></script>'
if marker not in html:
    if '</body>' not in html:
        raise SystemExit('index.html has no closing body tag')
    index.write_text(html.replace('</body>', marker + '</body>', 1), encoding='utf-8')
print('完成：六阶段监控页面（6 组 x IPv4/IPv6）')
print('刷新 https://207.57.74.12:9443 查看')
