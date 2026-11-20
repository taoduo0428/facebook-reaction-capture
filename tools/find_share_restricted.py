from __future__ import annotations
import csv, json, re, subprocess, time
from datetime import datetime
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
ADB=BASE/'platform-tools'/'adb.exe'
SERIAL='emulator-5554'
EXCLUDE={'100002479694500','100002704356402','100001389831058','100060409312511','100000461496497','100010428648566','1337174756'}
for d in (BASE/'runs').glob('profile-visibility-audit-*/profiles/*'):
    EXCLUDE.add(d.name)
PATS=[re.compile(p,re.I) for p in [r'加为好友可查看',r'加為好友可查看',r'添加好友可查看',r'加好友可查看',r'Add friend to see more',r'Add as a friend to see more',r'Only friends can see']]
REMOTE='/sdcard/share-restricted-probe.xml'

def adb(*args,timeout=20,binary=False):
    r=subprocess.run([str(ADB),'-s',SERIAL,*args],capture_output=True,timeout=timeout)
    if r.returncode!=0:
        raise RuntimeError(r.stderr.decode('utf-8','ignore'))
    return r.stdout if binary else r.stdout.decode('utf-8','ignore').strip()

def candidates(limit=20):
    seen=set(); out=[]
    for run in sorted((BASE/'runs').glob('20*'),key=lambda p:p.name,reverse=True):
        f=run/'analysis'/'facebook-share-users.csv'
        if not f.exists(): continue
        try:
            for r in csv.DictReader(f.open(encoding='utf-8-sig')):
                uid=str(r.get('actor_id') or '').strip()
                if not uid.isdigit() or uid in EXCLUDE or uid in seen: continue
                seen.add(uid); out.append({'user_id':uid,'user_name':str(r.get('actor_name') or ''),'source_post_id':str(r.get('source_post_id') or ''),'source_run':run.name})
                if len(out)>=limit: return out
        except Exception: pass
    return out

def dump(path):
    adb('shell','uiautomator','dump',REMOTE)
    adb('pull',REMOTE,str(path))
    return path.read_text(encoding='utf-8',errors='ignore')

def shot(path): path.write_bytes(adb('exec-out','screencap','-p',binary=True))

def main():
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
    out=BASE/'runs'/f'share-restricted-audit-{stamp}'
    out.mkdir(parents=True,exist_ok=True)
    results=[]; hits=[]
    for row in candidates(20):
        uid=row['user_id']; ud=out/uid; ud.mkdir(exist_ok=True)
        try:
            adb('shell','am','start','-a','android.intent.action.VIEW','-d',f'https://www.facebook.com/{uid}','-p','com.facebook.katana')
            time.sleep(5)
            texts=[]
            for i in range(3):
                p=ud/f'{i}.xml'; texts.append(dump(p))
                if i<2:
                    adb('shell','input','swipe','540','1600','540','700','500'); time.sleep(2)
            joined='\n'.join(texts)
            explicit=any(p.search(joined) for p in PATS)
            result={**row,'explicit_restricted':explicit}
            if explicit:
                sp=ud/'restricted.png'; shot(sp); result['screenshot']=str(sp.relative_to(out)).replace('\\','/'); hits.append(result)
            results.append(result)
            if len(hits)>=2: break
        except Exception as e:
            results.append({**row,'explicit_restricted':False,'error':str(e)})
    (out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    proxy=adb('shell','settings','get','global','http_proxy')
    reverse=adb('reverse','--list')
    summary={'output_dir':str(out),'tested':len(results),'hits':hits,'transport':{'adb_state':adb('get-state'),'proxy':proxy,'reverse_7897':'tcp:7897 tcp:7897' in reverse,'reverse_8081':'tcp:8081 tcp:8081' in reverse,'capture_marker':(BASE/'.facebook-capture-active.json').exists()}}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))
if __name__=='__main__': main()
