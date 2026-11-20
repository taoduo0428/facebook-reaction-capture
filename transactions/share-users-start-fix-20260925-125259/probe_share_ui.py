from pathlib import Path
import json,sys,re
root=Path(sys.argv[1])
html=(root/'web_v2/index.html').read_text(encoding='utf-8-sig')
js=(root/'web_v2/app.js').read_text(encoding='utf-8-sig')
print(json.dumps({"input":"shares","has_share_control":'id="collectShares"' in html,"has_combined_control":'id="collectCombined"' in html,"submits_collect_shares":bool(re.search(r'requestBody\.collect\s*=\s*\["shares"\]',js)),"reads_share_selection":'$("collectShares").checked' in js},ensure_ascii=False,sort_keys=True))
