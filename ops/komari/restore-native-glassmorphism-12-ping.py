#!/usr/bin/env python3
"""Restore Glassmorphism's NATIVE ping component for twelve existing tasks."""
from pathlib import Path
import sqlite3, json, re, sys

base = Path('/var/lib/komari')
dist = base / 'data/theme/Glassmorphism/dist'
idx = dist / 'index.html'
dbfile = base / 'komari.db'
ids = [39,40,27,30,28,31,38,34,32,35,33,36]
if not idx.exists() or not dbfile.exists():
    raise SystemExit('ERROR: Komari theme / database missing. Nothing modified.')
db = sqlite3.connect(f'file:{dbfile}?mode=ro', uri=True)
actual = {row[0] for row in db.execute('SELECT id FROM ping_tasks')}
missing = [id for id in ids if id not in actual]
if missing:
    raise SystemExit(f'ERROR: Missing Ping tasks: {missing}. Nothing modified.')

# The original compiled parser follows Number.isSafeInteger(...) with .slice(0,3).
# Change ONLY that specific limit. Do not modify other slice(0,3) expressions.
to_write = {}
matched = []
already = []
for file in (dist / 'assets').rglob('*.js'):
    source = file.read_text(encoding='utf-8', errors='replace')
    if 'Number.isSafeInteger' not in source or 'new Set' not in source:
        continue
    edits = []
    for m in re.finditer(r'Number\.isSafeInteger\s*\(', source):
        before = source[max(0,m.start()-600):m.start()]
        after = source[m.end():min(len(source),m.end()+180)]
        if 'new Set' not in before or '.map(Number)' not in before:
            continue
        sm = re.search(r'\.slice\(0\s*,\s*(3|12)\)', after)
        if not sm:
            continue
        beg = m.end()+sm.start()
        end = m.end()+sm.end()
        if sm.group(1) == '12':
            already.append(file.name)
            continue
        edits.append((beg,end))
    if edits:
        for start,end in sorted(set(edits),reverse=True):
            source = source[:start]+'.slice(0,12)'+source[end:]
        to_write[file] = source
        matched.append((file.name, len(edits)))

if not matched and not already:
    print('ERROR: Native 3-task limit not located in this actual JS build.')
    print('Nothing modified. Matching source needs inspection before safely changing it.')
    for file in (dist/'assets').rglob('*.js'):
        t = file.read_text(errors='ignore')
        if 'Number.isSafeInteger' in t:
            m = re.search(r'Number\.isSafeInteger',t)
            print('CANDIDATE:',file.name,repr(t[max(0,m.start()-160):m.start()+200]))
    raise SystemExit(2)

old_index = idx.read_text(encoding='utf-8')
new_index = old_index
new_index = new_index.replace('<script src="/assets/fn-six-stage.js?v=1" defer></script>','')
new_index = re.sub(r'<script\s+src="/assets/fn-twelve-ping\.js\?v=\d+"\s+defer></script>','',new_index)
if not '<html' in new_index.lower():
    raise SystemExit('ERROR: invalid HTML, nothing modified.')

row = db.execute("SELECT data FROM theme_configurations WHERE short='Glassmorphism'").fetchone()
if not row:
    raise SystemExit('ERROR: Glassmorphism config missing, nothing modified.')
config = json.loads(row[0])
db.close()
if not isinstance(config,dict):
    raise SystemExit('ERROR: Glassmorphism config invalid, nothing modified.')

config.update({
    'threeNetPingEnabled': True,
    'threeNetPingSparkline': False,
    'threeNetPingTaskIds': json.dumps(ids),
    'threeNetPingNodeTaskBindings': '{}',
})

# All checks have passed: apply the native-component fix.
for file, content in to_write.items():
    file.write_text(content,encoding='utf-8')
if new_index != old_index:
    idx.write_text(new_index,encoding='utf-8')
wdb = sqlite3.connect(str(dbfile),timeout=15)
wdb.execute('BEGIN IMMEDIATE')
wdb.execute('UPDATE theme_configurations SET data=? WHERE short=?',
            (json.dumps(config,ensure_ascii=False),'Glassmorphism'))
wdb.execute('INSERT INTO configs (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            ('theme',json.dumps('Glassmorphism')))
wdb.commit()
wdb.close()

# Remove only our prior scripts, never the original theme bundles.
for f in ['fn-twelve-ping.js','fn-six-stage.js']:
    p=dist/'assets'/f
    if p.is_file(): p.unlink()
print('OK: Original Glassmorphism and native NodeThreeNetPing activated.')
print('Configured Ping tasks:', ids)
print('Patched native parser:', matched or 'already patched: '+', '.join(already))
print('Removed prior injected overlay: ', new_index!=old_index)
print('All native uptime, expiration, price, traffic and history calculations preserved.')
print('Reload the website in a NEW private/incognito browser tab.')
