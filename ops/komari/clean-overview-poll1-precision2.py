#!/usr/bin/env python3
"""Glassmorphism: hide redundant overview cards, poll at 1s, show 2-decimal units."""
import json, re, sqlite3
from pathlib import Path

base=Path('/var/lib/komari')
root=base/'data/theme/Glassmorphism/dist'
idx=root/'index.html'
dbfile=base/'komari.db'
if not idx.is_file() or not dbfile.is_file():
    raise SystemExit('ERROR: Existing Glassmorphism/index.html or komari.db not found; no changes.')

# Precisely target the original app byte-decimal constants (not Ping renderers or pricing).
pattern = re.compile(
    r'(?<![A-Za-z0-9_])(?:"B"|B)\s*:\s*0\s*,\s*(?:"KB"|KB)\s*:\s*(?:0|2)'
    r'\s*,\s*(?:"MB"|MB)\s*:\s*(?:1|2)\s*,\s*(?:"GB"|GB)\s*:\s*(?:1|2)'
    r'\s*,\s*(?:"TB"|TB)\s*:\s*2(?![0-9])'
)
replacements={}
matched=[]
for f in (root/'assets').rglob('*.js'):
    code=f.read_text(encoding='utf8',errors='replace')
    found=list(pattern.finditer(code))
    if found:
        updated=pattern.sub('B:0,KB:2,MB:2,GB:2,TB:2',code)
        if updated!=code: replacements[f]=updated
        matched.append((f.name,len(found)))

if not matched:
    print('ERROR: Cannot locate known byte-format setting in compiled JS. No files changed.')
    print('Candidates (first 5):')
    count=0
    for f in (root/'assets').rglob('*.js'):
        s=f.read_text(errors='ignore')
        for m in re.finditer(r'GB\s*:\s*1',s):
            print(f.name,repr(s[max(0,m.start()-90):m.end()+90]))
            count+=1
            if count>=5: break
        if count>=5: break
    raise SystemExit(2)

css = '''<style id="fn-hide-duplicate-overview">
/* Keep the globe and original node cards, hide just the six duplicated summary tiles. */
[data-general-card-key]{display:none!important;}
div:has(> [data-general-card-key]){display:none!important;}
div:has(> div > [data-general-card-key]){height:auto!important;min-height:0!important;grid-template-rows:auto!important;}
div:has(> div > [data-general-card-key]) > :first-child{grid-column:1/-1!important;justify-self:center!important;max-width:650px;width:100%;}
</style>'''
html=idx.read_text(encoding='utf8')
html=re.sub(r'<style\s+id=["\']fn-hide-duplicate-overview["\'][^>]*>.*?</style>','',html,flags=re.S)
if '</head>' not in html: raise SystemExit('ERROR: Invalid theme HTML; no changes.')
html=html.replace('</head>',css+'</head>',1)

db=sqlite3.connect(f'file:{dbfile}?mode=ro',uri=True)
row=db.execute('SELECT data FROM theme_configurations WHERE short=?',('Glassmorphism',)).fetchone()
db.close()
if row is None: raise SystemExit('ERROR: Glassmorphism configuration missing; no changes.')
settings=json.loads(row[0])
if not isinstance(settings,dict):raise SystemExit('ERROR: Invalid config; no changes.')
settings['dataUpdateInterval']=1
# Keep Globe visible: hideGeneralCard=true would remove globe AND overview.
settings['hideGeneralCard']=False

for file,content in replacements.items():file.write_text(content,encoding='utf8')
idx.write_text(html,encoding='utf8')
conn=sqlite3.connect(str(dbfile),timeout=12)
cur=conn.execute('UPDATE theme_configurations SET data=? WHERE short=?',
                 (json.dumps(settings,ensure_ascii=False),'Glassmorphism'))
conn.commit()
conn.close()
print('OK: Removed six duplicated overview cards; globe and original node card remain.')
print('OK: Native polling interval is 1 second while page timers are running.')
print('OK: KB/MB/GB values display two decimal places; raw server counters unchanged.')
print('Updated format chunks:',matched,'files written:',len(replacements))
print('Note: browser closed = zero webpage polling; hidden tabs may throttle polling.')
print('Note: probe report frequency can be slower than one second.')
print('Please RELOAD the monitor site in a NEW private browser tab.')
