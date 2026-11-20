from __future__ import annotations
import csv,json,re,subprocess,time
from pathlib import Path
from datetime import datetime
BASE=Path(__file__).resolve().parents[1]; ADB=BASE/'platform-tools'/'adb.exe'; SERIAL='emulator-5554'; REMOTE='/sdcard/share-fast.xml'
PATS=[re.compile(x,re.I) for x in [r'加为好友可查看',r'加為好友可查看',r'添加好友可查看',r'加好友可查看',r'Add friend to see more',r'Add as a friend to see more',r'Only friends can see']]

def adb(*a,timeout=20,binary=False):
 r=subprocess.run([str(ADB),'-s',SERIAL,*a],capture_output=True,timeout=timeout)
 if r.returncode!=0: raise RuntimeError(r.stderr.decode('utf-8','ignore'))
 return r.stdout if binary else r.stdout.decode('utf-8','ignore').strip()

def tested_ids():
 out=set()
 for d in (BASE/'runs').glob('profile-visibility-audit-*/profiles/*'): out.add(d.name)
 for d in (BASE/'runs').glob('share-restricted-audit-*/*'):
  if d.is_dir() and d.name.isdigit(): out.add(d.name)
 for d in (BASE/'runs').glob('share-restricted-fast-*/*'):
  if d.is_dir() and d.name.isdigit(): out.add(d.name)
 return out

def cand(limit=16):
 ex=tested_ids(); seen=set(); out=[]
 for run in sorted((BASE/'runs').glob('20*'),key=lambda p:p.name,reverse=True):
  f=run/'analysis'/'facebook-share-users.csv'
  if not f.exists(): continue
  try:
   for r in csv.DictReader(f.open(encoding='utf-8-sig')):
    uid=str(r.get('actor_id') or '').strip()
    if not uid.isdigit() or uid in ex or uid in seen: continue
    seen.add(uid); out.append({'user_id':uid,'user_name':str(r.get('actor_name') or ''),'source_post_id':str(r.get('source_post_id') or ''),'source_run':run.name})
    if len(out)>=limit: return out
  except Exception: pass
 return out

def dump(p):
 adb('shell','uiautomator','dump',REMOTE,timeout=20); adb('pull',REMOTE,str(p),timeout=20); return p.read_text(encoding='utf-8',errors='ignore')
def save_json(p,obj): p.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def main():
 out=BASE/'runs'/('share-restricted-fast-'+datetime.now().strftime('%Y%m%d-%H%M%S')); out.mkdir(parents=True,exist_ok=True)
 results=[]; hit=None
 for row in cand(16):
  uid=row['user_id']; ud=out/uid; ud.mkdir(exist_ok=True)
  try:
   adb('shell','am','start','-a','android.intent.action.VIEW','-d',f'fb://profile/{uid}','-p','com.facebook.katana'); time.sleep(4)
   texts=[]
   for i in range(3):
    texts.append(dump(ud/f'{i}.xml'))
    if i<2: adb('shell','input','swipe','540','1600','540','700','450'); time.sleep(1.5)
   joined='\n'.join(texts); explicit=any(p.search(joined) for p in PATS)
   rr={**row,'explicit_restricted':explicit}; results.append(rr)
   print('TEST',uid,row['user_name'],'restricted=',explicit,flush=True)
   if explicit:
    (ud/'restricted.png').write_bytes(adb('exec-out','screencap','-p',binary=True)); rr['screenshot']=str((ud/'restricted.png').relative_to(out)).replace('\\','/'); hit=rr; save_json(out/'progress.json',results); break
  except Exception as e:
   results.append({**row,'explicit_restricted':False,'error':str(e)}); print('ERROR',uid,str(e),flush=True)
  save_json(out/'progress.json',results)
 proxy=adb('shell','settings','get','global','http_proxy'); rev=adb('reverse','--list')
 summary={'output_dir':str(out),'tested':len(results),'hit':hit,'transport':{'adb_state':adb('get-state'),'proxy':proxy,'reverse_7897':'tcp:7897 tcp:7897' in rev,'reverse_8081':'tcp:8081 tcp:8081' in rev,'capture_marker':(BASE/'.facebook-capture-active.json').exists()}}
 save_json(out/'summary.json',summary); print(json.dumps(summary,ensure_ascii=False),flush=True)
if __name__=='__main__': main()
