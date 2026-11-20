import base64, hashlib, json, re, struct
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit
from mitmproxy import io, http

ROOT = Path(r"C:\Users\lenovo\Desktop\facebook\runs\20260901-102508")
INPUT = ROOT / "raw-facebook-http.mitm"
OUT = ROOT / "analysis" / "raw-facebook-http-full-analysis.json"

SENSITIVE = ("authorization","cookie","access_token","client_token","password","passwd","secret","session_token","auth_token")

def sensitive(k):
    x = str(k).lower().replace("-","_")
    return any(s in x for s in SENSITIVE)

def redact(v):
    s = "" if v is None else str(v)
    return {"_redacted": True, "length": len(s), "sha256_prefix": hashlib.sha256(s.encode("utf-8","ignore")).hexdigest()[:12]}

def safe_url(u):
    if not isinstance(u, str) or "?" not in u:
        return u
    try:
        sp=urlsplit(u)
        pairs=[]
        for k,v in parse_qsl(sp.query, keep_blank_values=True):
            pairs.append((k, "[REDACTED_SECRET]" if sensitive(k) else v))
        return urlunsplit((sp.scheme, sp.netloc, sp.path, urlencode(pairs), sp.fragment))
    except Exception:
        return u

def sanitize(x, key=None):
    if key is not None and sensitive(key):
        return redact(x)
    if isinstance(x, dict):
        return {k:sanitize(v,k) for k,v in x.items()}
    if isinstance(x, list):
        return [sanitize(v,key) for v in x]
    return x

def headers(h):
    out = {}
    for k,v in h.items(multi=True):
        val = redact(v) if sensitive(k) else v
        if k in out:
            if not isinstance(out[k], list): out[k] = [out[k]]
            out[k].append(val)
        else:
            out[k] = val
    return out

def maybe_json(s):
    if not isinstance(s,str): return None
    t = s.strip()
    if not t or t[0] not in "[{": return None
    try: return json.loads(t)
    except: return None

def nested_json(x, path="$", out=None):
    if out is None: out={}
    if isinstance(x,dict):
        for k,v in x.items(): nested_json(v, path+"."+str(k), out)
    elif isinstance(x,list):
        for i,v in enumerate(x): nested_json(v, path+"["+str(i)+"]", out)
    elif isinstance(x,str):
        p=maybe_json(x)
        if p is not None:
            p=sanitize(p)
            out[path]=p
            nested_json(p,path+"::<json>",out)
    return out

B64 = re.compile(r"^[A-Za-z0-9+/=_-]{12,}$")
def b64_decode(s):
    if not isinstance(s,str) or not B64.match(s) or len(s)>2048 or "http" in s.lower(): return None
    for t in (s, s.replace("-","+").replace("_","/")):
        try:
            t += "="*((4-len(t)%4)%4)
            b=base64.b64decode(t, validate=False)
            q=b.decode("utf-8")
            ratio=sum(c.isprintable() or c in "\r\n\t" for c in q)/max(1,len(q))
            if ratio>0.92 and any(c in q for c in ":_@"): return q
        except: pass
    return None

def b64_map(x,path="$",out=None):
    if out is None: out={}
    if isinstance(x,dict):
        for k,v in x.items(): b64_map(v,path+"."+str(k),out)
    elif isinstance(x,list):
        for i,v in enumerate(x): b64_map(v,path+"["+str(i)+"]",out)
    elif isinstance(x,str):
        d=b64_decode(x)
        if d is not None: out[path]=d
    return out

def parse_req(req):
    ct=req.headers.get("content-type","")
    rawb=req.raw_content or b""
    decodedb=req.content or b""
    raw=req.get_text(strict=False) if req.raw_content is not None else ""
    z={"bytes":len(rawb),"raw_bytes":len(rawb),"decoded_bytes":len(decodedb),"content_type":ct or None}
    if "application/x-www-form-urlencoded" in ct:
        q=parse_qs(raw,keep_blank_values=True)
        form={}
        for k,vals in q.items():
            vv=[]
            for v in vals:
                if sensitive(k): vv.append(redact(v))
                elif k in ("variables","fb_api_client_context"):
                    j=maybe_json(v)
                    vv.append(sanitize(j) if j is not None else v)
                else: vv.append(v)
            form[k]=vv[0] if len(vv)==1 else vv
        z["form"]=form
    elif "json" in ct.lower():
        try: z["json"]=sanitize(json.loads(raw))
        except: z["text"]=raw
    else:
        z["text"]=raw
    return z

def parse_resp(resp):
    ct=resp.headers.get("content-type","")
    rawb=resp.raw_content or b""
    decodedb=resp.content or b""
    txt=resp.get_text(strict=False) if resp.raw_content is not None else ""
    z={"bytes":len(rawb),"raw_bytes":len(rawb),"decoded_bytes":len(decodedb),"content_type":ct or None}
    if "json" in ct.lower() or "javascript" in ct.lower() or txt.lstrip().startswith(("{","[")):
        try:
            j=sanitize(json.loads(txt))
            z["json"]=j
            z["nested_json"]=nested_json(j)
            z["base64_decodings"]=b64_map(j)
        except Exception as e:
            z["text"]=txt
            z["json_parse_error"]=str(e)
    elif ct.lower().startswith("image/"):
        z["binary"]={
            "sha256":hashlib.sha256(decodedb).hexdigest(),
            "first_64_hex":decodedb[:64].hex(),
            "wire_first_64_hex":rawb[:64].hex()
        }
        if decodedb[:6] in (b"GIF87a",b"GIF89a") and len(decodedb)>=10:
            w,h=struct.unpack("<HH",decodedb[6:10])
            z["binary"].update({"format":decodedb[:6].decode(),"width":w,"height":h})
    else:
        z["text"]=txt
    return z

def tsi(t):
    if t is None:return None
    d=datetime.fromtimestamp(t,tz=timezone.utc)
    return {"epoch":t,"utc":d.isoformat(),"utc_plus_8":d.astimezone(timezone(timedelta(hours=8))).isoformat()}

def sem(flow, req_body, resp_body, friendly):
    out={}
    form=req_body.get("form",{}) if isinstance(req_body,dict) else {}
    if isinstance(form,dict) and isinstance(form.get("variables"),dict):
        out["request_variables"]=form["variables"]
    data=None
    if resp_body and isinstance(resp_body.get("json"),dict):
        data=resp_body["json"].get("data")
    if friendly=="FbStoriesLightBucketsQuery" and isinstance(data,dict):
        me=data.get("me") or {}
        ub=me.get("unified_stories_buckets") or {}
        out["viewer"]={k:me.get(k) for k in ("__typename","id","strong_id__","fb_stories_hot_start_ttl_sec","fb_stories_warm_start_ttl_sec","fb_stories_cold_start_ttl_sec","camera_post_tracking_key")}
        out["page_info"]=ub.get("page_info")
        out["client_request_id"]=ub.get("client_request_id")
        buckets=[]
        for e in ub.get("edges") or []:
            n=(e or {}).get("node") or {}
            owner=n.get("story_bucket_owner") or {}
            buckets.append({
              "bucket_id":n.get("id"),"typename":n.get("__typename"),"story_bucket_type":n.get("story_bucket_type"),
              "seen":n.get("is_bucket_seen_by_viewer"),"eligible_display":n.get("is_bucket_eligible_for_display"),
              "eligible_tray":n.get("is_bucket_eligible_for_display_in_tray"),"eligible_viewer":n.get("is_bucket_eligible_for_display_in_viewer"),
              "suggested":n.get("is_story_bucket_suggested"),"can_viewer_post":n.get("can_viewer_post"),
              "expiration_time":n.get("expiration_time"),"sort_key":n.get("sort_key"),"ranking_time":n.get("story_ranking_time"),
              "final_ranking_score":n.get("final_ranking_score"),"contact_importer_render_type":n.get("contact_importer_render_type"),
              "render_type":n.get("render_type"),"owner":owner,
              "thumbnail_story_to_show":n.get("thumbnail_story_to_show"),"first_story_to_show":n.get("first_story_to_show"),
              "first_story_feedback_summary":n.get("first_story_feedback_summary"),"ranking_tracking_string":n.get("ranking_tracking_string"),
              "is_bucket_muted_for_viewer":n.get("is_bucket_muted_for_viewer"),"tracking_key":n.get("tracking_key"),"is_bucket_live":n.get("is_bucket_live")
            })
        out["buckets"]=buckets
    elif friendly=="FBStoriesContactImporterUpsellImpression" and isinstance(data,dict):
        out["result"]=data.get("xfb_contact_importer_upsell_impression")
    elif friendly and friendly.startswith("FbBloksAsyncComponentsQuery") and isinstance(data,dict):
        q=data.get("fb_bloks_component_query") or {}
        comps=[]
        for r in q.get("component_query_responses") or []:
            m=r.get("metadata") or {}
            ir=r.get("initial_response") or {}
            p=(ir.get("payload") or {}).get("payload") if isinstance(ir.get("payload"),dict) else None
            comps.append({"metadata":m,"initial_payload_decoded":sanitize(maybe_json(p)) if p else None})
        out["components"]=comps
    elif friendly=="MediaFetchByIds" and isinstance(data,dict):
        out["nodes"]=data.get("nodes")
    return out

with INPUT.open("rb") as f:
    flows=list(io.FlowReader(f).stream())

recs=[]
for i,fl in enumerate(flows,1):
    r={"index":i,"flow_type":type(fl).__name__,"flow_id":getattr(fl,"id",None)}
    if isinstance(fl,http.HTTPFlow):
        qb=parse_req(fl.request)
        rb=parse_resp(fl.response) if fl.response else None
        form=qb.get("form",{}) if isinstance(qb,dict) else {}
        friendly=form.get("fb_api_req_friendly_name") if isinstance(form,dict) else None
        if not friendly: friendly=fl.request.headers.get("x-fb-friendly-name")
        r.update({
          "friendly_name":friendly,
          "request":{"method":fl.request.method,"scheme":fl.request.scheme,"host":fl.request.host,"port":fl.request.port,
                     "path":safe_url(fl.request.path),"pretty_url":safe_url(fl.request.pretty_url),"http_version":fl.request.http_version,
                     "headers":headers(fl.request.headers),"body":qb},
          "response":None if fl.response is None else {"status_code":fl.response.status_code,"reason":fl.response.reason,
                     "http_version":fl.response.http_version,"headers":headers(fl.response.headers),"body":rb},
          "error":None if fl.error is None else str(fl.error),
          "timestamps":{"request_start":tsi(fl.request.timestamp_start),"request_end":tsi(fl.request.timestamp_end),
                        "response_start":tsi(getattr(fl.response,"timestamp_start",None)),"response_end":tsi(getattr(fl.response,"timestamp_end",None))},
          "semantic":sem(fl,qb,rb,friendly)
        })
    recs.append(r)

result={"input_file":str(INPUT),"input_sha256":hashlib.sha256(INPUT.read_bytes()).hexdigest(),
        "flow_count":len(flows),"http_flow_count":sum(isinstance(x,http.HTTPFlow) for x in flows),
        "hosts":sorted({x.request.host for x in flows if isinstance(x,http.HTTPFlow)}),"records":recs}
OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"ok":True,"out":str(OUT),"flow_count":len(flows),
  "summary":[{"i":r["index"],"name":r.get("friendly_name"),"method":(r.get("request") or {}).get("method"),
              "path":(r.get("request") or {}).get("path"),"status":(r.get("response") or {}).get("status_code"),
              "req_bytes":(((r.get("request") or {}).get("body") or {}).get("bytes")),
              "resp_bytes":((((r.get("response") or {}).get("body") or {}).get("bytes")))} for r in recs]},ensure_ascii=False,indent=2))
