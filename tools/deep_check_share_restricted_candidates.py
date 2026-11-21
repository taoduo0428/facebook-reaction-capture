from __future__ import annotations
import json,re,subprocess,time
from pathlib import Path
from datetime import datetime
BASE=Path(__file__).resolve().parents[1]; ADB=BASE/'platform-tools'/'adb.exe'; SERIAL='emulator-5554'; REMOTE='/sdcard/share-deep.xml'
PATS=[re.compile(x,re.I) for x in [r'加为好友可查看',r'加為好友可查看',r'添加好友可查看',r'加好友可查看',r'把.+加为好友可查看',r'把.+加為好友可查看',r'Add friend to see more',r'Add as a friend to see more',r'Only friends can see']]

def adb(*a,timeout=20,binary=False):
 r=subprocess.run([str(ADB),'-s',SERIAL,*a],capture_output=True,timeout=timeout)
 if r.returncode!=0: raise RuntimeError(r.stderr.decode('utf-8','ignore').strip())
 return r.stdout if binary else r.stdout.decode('utf-8','ignore').strip()

def dump(p):
 adb('shell','uiautomator','dump',REMOTE,timeout=20); adb('pull',REMOTE,str(p),timeout=20); return p.read_text(encoding='utf-8',errors='ignore')
def save(p,o):p.write_text(json.dumps(o,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def main():
 src=sorted((BASE/'runs').glob('share-restricted-prefilter-*'))[-1]
 rows=json.loads((src/'progress.json').read_text(encoding='utf-8'))
 candidates=[r for r in rows if r.get('prefilter') and not r.get('explicit_restricted') and r.get('user_id')]
 out=BASE/'runs'/('share-restricted-deep-'+datetime.now().strftime('%Y%m%d-%H%M%S')); out.mkdir(parents=True,exist_ok=True)
 results=[]; hit=None
 for row in candidates:
  uid=str(row['user_id']); ud=out/uid; ud.mkdir(exist_ok=True)
  rr={**row,'deep_checked':False,'explicit_restricted_deep':False}
  try:
   adb('shell','am','start','-a','android.intent.action.VIEW','-d',f'fb://profile/{uid}','-p','com.facebook.katana'); time.sleep(3)
   texts=[dump(ud/'top.xml')]
   for i in range(1,5):
    adb('shell','input','swipe','540','1650','540','550','500'); time.sleep(1.3)
    texts.append(dump(ud/f'scroll-{i}.xml'))
    joined='\n'.join(texts)
    if any(p.search(joined) for p in PATS):
     rr['deep_checked']=True; rr['explicit_restricted_deep']=True; rr['matched_scroll']=i
     (ud/'restricted.png').write_bytes(adb('exec-out','screencap','-p',binary=True)); rr['screenshot']=str((ud/'restricted.png').relative_to(out)).replace('\\','/'); hit=rr; break
   if not rr['deep_checked']: rr['deep_checked']=True
   results.append(rr); save(out/'progress.json',results)
   print('TEST',uid,row.get('user_name',''),'restricted=',rr['explicit_restricted_deep'],flush=True)
   if hit: break
  except Exception as e:
   rr['error']=str(e); results.append(rr); save(out/'progress.json',results); print('ERROR',uid,str(e),flush=True)
 proxy=adb('shell','settings','get','global','http_proxy'); rev=adb('reverse','--list')
 summary={'output_dir':str(out),'source_prefilter_dir':str(src),'candidate_count':len(candidates),'tested':len(results),'hit':hit,'transport':{'adb_state':adb('get-state'),'proxy':proxy,'reverse_7897':'tcp:7897 tcp:7897' in rev,'reverse_8081':'tcp:8081 tcp:8081' in rev,'capture_marker':(BASE/'.facebook-capture-active.json').exists()}}
 save(out/'summary.json',summary); print(json.dumps(summary,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
