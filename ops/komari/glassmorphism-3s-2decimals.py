#!/usr/bin/env python3
"""Keep Glassmorphism and 12 Ping tasks; 3s polling and two-decimal byte formatting."""
from pathlib import Path
import json
import re
import shutil
import sqlite3
import sys

base = Path('/var/lib/komari')
dist = base / 'data/theme/Glassmorphism/dist'
assets = dist / 'assets'
index = dist / 'index.html'
dbfile = base / 'komari.db'

if not index.is_file() or not assets.is_dir() or not dbfile.is_file():
    sys.exit('错误：当前 Glassmorphism 主题文件或数据库不存在；未修改。')

# Only match the native byte-decimal configuration object in Vite JS.
# The original object is {B:0, KB:0, MB:1, GB:1, TB:2}.
def key(name):
    return r'(?:' + name + r'|"' + name + r'"|\x27' + name + r'\x27)'

fmt = re.compile(
    r'(?<![\w$])(?P<B>' + key('B') + r')\s*:\s*0\s*,\s*'
    r'(?P<KB>' + key('KB') + r')\s*:\s*[012]\s*,\s*'
    r'(?P<MB>' + key('MB') + r')\s*:\s*[012]\s*,\s*'
    r'(?P<GB>' + key('GB') + r')\s*:\s*[012]\s*,\s*'
    r'(?P<TB>' + key('TB') + r')\s*:\s*[012](?![0-9])'
)

# Avoid reading any prior versioned runtime build as the source.
files = [p for p in assets.rglob('*') if p.is_file()
         and not any(part.startswith('fn-precision-') for part in p.relative_to(assets).parts)]
patched_js = {}
found = []
for p in files:
    if p.suffix != '.js':
        continue
    s = p.read_text(encoding='utf-8', errors='replace')
    matches = list(fmt.finditer(s))
    if not matches:
        continue
    def to_two(m):
        return f"{m.group('B')}:0,{m.group('KB')}:2,{m.group('MB')}:2,{m.group('GB')}:2,{m.group('TB')}:2"
    updated = fmt.sub(to_two, s)
    patched_js[p] = updated
    found.append((p.name, len(matches)))

if not found:
    sample = []
    for p in files:
        if p.suffix != '.js':
            continue
        s = p.read_text(errors='replace')
        m = re.search(r'GB\s*:\s*[012]', s)
        if m:
            sample.append((p.name, s[max(m.start()-70, 0):m.end()+70]))
        if len(sample) >= 3:
            break
    print('错误：当前编译 JS 未定位到格式化精度对象；为避免误改，未进行任何修改。')
    print('候选片段：', sample)
    sys.exit(2)

html = index.read_text(encoding='utf-8')
html = re.sub(r'<script\s+src=["\x27]/assets/(?:fn-six-stage|fn-twelve-ping)\.js(?:\?v=\d+)?["\x27]\s+defer></script>', '', html)
html = re.sub(r'<style\s+id=["\x27]fn-hide-duplicate-overview["\x27][^>]*>.*?</style>', '', html, flags=re.S)

# A different path for the Vite JS graph invalidates old immutable module cache.
version = 'fn-precision-2dp-3s-v3'
html = re.sub(r'/assets/fn-precision-[^/"\x27]+/', '/assets/', html)
url = re.compile(r'(?P<before>(?:src|href)=["\x27])/assets/(?P<name>[^"\x27?]+\.js)(?P<after>(?:\?[^"\x27]*)?["\x27])')
refs = [m.group('name') for m in url.finditer(html)]
if not refs or not any(p.name in refs for p in files if p.suffix == '.js'):
    sys.exit('错误：找不到原主题的 JS 入口引用；未修改。')
new_html = url.sub(lambda m: m.group('before') + '/assets/' + version + '/' + m.group('name') + m.group('after'), html)
if new_html == html:
    sys.exit('错误：前端模块引用没有改变；未修改。')

ro = sqlite3.connect(f'file:{dbfile}?mode=ro', uri=True)
row = ro.execute("SELECT data FROM theme_configurations WHERE short='Glassmorphism'").fetchone()
if not row:
    sys.exit('错误：Glassmorphism 配置不存在；未修改。')
settings = json.loads(row[0])
ro.close()
if not isinstance(settings, dict):
    sys.exit('错误：主题配置不是 JSON 对象；未修改。')
settings['dataUpdateInterval'] = 3
settings['hideGeneralCard'] = True
settings['hideEarth'] = True
settings['rpcTransportMode'] = 'http'

# Copy only into a versioned runtime directory; original app assets untouched.
target = assets / version
for source in files:
    dst = target / source.relative_to(assets)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if source in patched_js:
        dst.write_text(patched_js[source], encoding='utf-8')
    else:
        shutil.copyfile(source, dst)
for p in patched_js:
    assert (target / p.relative_to(assets)).is_file()
for ref in refs:
    if not (target / ref).is_file():
        sys.exit(f'错误：版本化 JS 入口缺失 {ref}；未切换页面。')

index.write_text(new_html, encoding='utf-8')
db = sqlite3.connect(str(dbfile), timeout=15)
try:
    db.execute('BEGIN IMMEDIATE')
    db.execute('UPDATE theme_configurations SET data=? WHERE short=?',
               (json.dumps(settings, ensure_ascii=False), 'Glassmorphism'))
    db.commit()
finally:
    db.close()

print('成功：Glassmorphism 网页轮询间隔 = 3 秒，探针上报间隔不变。')
print('成功：KB、MB、GB 的原生格式化统一保留两位小数。')
print('成功：使用新的 JS 资源地址，避开浏览器旧资源缓存。')
print('原主题、价格/价值、12 路 Ping 任务、代理配置均保留。')
print('生效的精度模块：', found)
print('请在新的无痕标签页打开 https://207.57.74.12:9443/')
