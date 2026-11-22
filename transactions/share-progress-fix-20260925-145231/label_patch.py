from pathlib import Path
p=Path(r"C:\Users\lenovo\Desktop\facebook\web_v2\app.js")
s=p.read_text(encoding="utf-8")
old='  $("telUsersLabel").textContent = shares ? "可见分享用户（按 ID 去重）" : unknown ? "采集类型待确认" : "点赞用户人数";'
new='  $("telUsersLabel").textContent = shares ? summary ? "可见分享用户（按 ID 去重）" : "可见分享记录（实时，未去重）" : unknown ? "采集类型待确认" : "点赞用户人数";'
if old not in s: raise SystemExit("label marker missing")
p.write_text(s.replace(old,new,1),encoding="utf-8")
