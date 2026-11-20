from __future__ import annotations
import csv,json,re,subprocess,time
from pathlib import Path
from datetime import datetime
BASE=Path(__file__).resolve().parents[1]; ADB=BASE/'platform-tools'/'adb.exe'; SERIAL='emulator-5554'; REMOTE='/sdcard/share-prefilter.xml'
ADD_RE=re.compile(r'(?i)(加为好友|加為好友|添加好友|加好友|add\s+friend)')
POST_RE=re.compile(r'(?i)([0-9][0-9,.]*|[0-9.]+\s*[KkMm]|[0-9.]+\s*[万萬])\s*(帖子|貼文|posts?)')
RESTRICT=[re.compile(x,re.I) for x in [r'加为好友可查看',r'加為好友可查看',r'添加好友可查看',r'加好友可查看',r'Add friend to see more',r'Add as a friend to see more',r'Only friends can see']]

def adb(*a,timeout=18,binary=False):
 r=subprocess.run([str(ADB),'-s',SERIAL,*a],capture_output=True,timeout=timeout)
 if r.returncode!=0: raise RuntimeError(r.stderr.decode('utf-8','ignore').strip())
 return r.stdout if binary else r.stdout.decode('utf-8','ignore').strip()

def parse_num(s):
 s=s.replace(',','').replace(' ',''); mult=1
 if s[-1:] in ('K','k'): mult=1000; s=s[:-1]
 elif s[-1:] in ('M','m'): mult=1000000; s=s[:-1]
 elif s[-1:] in ('万','萬'): mult=10000; s=s[:-1]
 try:return int(float(s)*mult)
 except:return None

def tested():
 out=set()
 for pat in ['profile-visibility-audit-*/profiles/*','share-restricted-audit-*/*','share-restricted-fast-*/*','share-restricted-prefilter-*/*']:
  for p in (BASE/'runs').glob(pat):
   name=p.stem if p.is_file() else p.name
   if name.isdigit(): out.add(name)
 for p in (BASE/'runs').glob('share-profile-visibility-probe-*/*.xml'):
  if p.stem.isdigit(): out.add(p.stem)
 return out

def candidates(limit=60):
 ex=tested(); seen=set(); out=[]
 for run in sorted((BASE/'runs').glob('20*'),key=lambda p:p.name,reverse=True):
  f=run/'analysis'/'facebook-share-users.csv'
  if not f.exists(): continue
  try:
   for r in csv.DictReader(f.open(encoding='utf-8-sig')):
    uid=str(r.get('actor_id') or '').strip()
    if not uid.isdigit() or uid in ex or uid in seen: continue
    seen.add(uid); out.append({'user_id':uid,'user_name':str(r.get('actor_name') or ''),'source_post_id':str(r.get('source_post_id') or ''),'source_run':run.name})
    if len(out)>=limit:return out
  except Exception:pass
 return out

def dump(p):
 adb('shell','uiautomator','dump',REMOTE,timeout=18); adb('pull',REMOTE,str(p),timeout=18); return p.read_text(encoding='utf-8',errors='ignore')
def save(p,o): p.write_text(json.dumps(o,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def main():
 out=BASE/'runs'/('share-restricted-prefilter-'+datetime.now().strftime('%Y%m%d-%H%M%S')); out.mkdir(parents=True,exist_ok=True)
 rows=[]; hit=None
 for row in candidates(60):
  uid=row['user_id']; ud=out/uid; ud.mkdir(exist_ok=True)
  rr={**row,'add_friend':False,'reported_post_count':None,'prefilter':False,'explicit_restricted':False}
  try:
   adb('shell','am','start','-a','android.intent.action.VIEW','-d',f'fb://profile/{uid}','-p','com.facebook.katana'); time.sleep(3)
   top=dump(ud/'top.xml')
   rr['add_friend']=bool(ADD_RE.search(top))
   m=POST_RE.search(top); rr['reported_post_count']=parse_num(m.group(1)) if m else None
   rr['prefilter']=bool(rr['add_friend'] and (rr['reported_post_count'] is None or rr['reported_post_count']<=5))
   if rr['prefilter']:
    adb('shell','input','swipe','540','1550','540','720','450'); time.sleep(1.2)
    lower=dump(ud/'lower.xml')
    text=top+'\n'+lower
    rr['explicit_restricted']=any(p.search(text) for p in RESTRICT)
    if rr['explicit_restricted']:
     (ud/'restricted.png').write_bytes(adb('exec-out','screencap','-p',binary=True)); rr['screenshot']=str((ud/'restricted.png').relative_to(out)).replace('\\','/'); hit=rr
   rows.append(rr); save(out/'progress.json',rows)
   print('TEST',uid,row['user_name'],'add=',rr['add_friend'],'posts=',rr['reported_post_count'],'prefilter=',rr['prefilter'],'restricted=',rr['explicit_restricted'],flush=True)
   if hit: break
  except Exception as e:
   rr['error']=str(e); rows.append(rr); save(out/'progress.json',rows); print('ERROR',uid,str(e),flush=True)
 proxy=adb('shell','settings','get','global','http_proxy'); rev=adb('reverse','--list')
 summary={'output_dir':str(out),'tested':len(rows),'hit':hit,'prefiltered':sum(1 for r in rows if r.get('prefilter')),'transport':{'adb_state':adb('get-state'),'proxy':proxy,'reverse_7897':'tcp:7897 tcp:7897' in rev,'reverse_8081':'tcp:8081 tcp:8081' in rev,'capture_marker':(BASE/'.facebook-capture-active.json').exists()}}
 save(out/'summary.json',summary); print(json.dumps(summary,ensure_ascii=False),flush=True)
if __name__=='__main__': main()
