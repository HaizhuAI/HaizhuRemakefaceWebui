import json, urllib.request, urllib.error, time
def req(m,u,timeout=60):
    h={"User-Agent":"okhttp/4.12.0"}
    r=urllib.request.Request(u,data=None,headers=h,method=m)
    try:
        with urllib.request.urlopen(r,timeout=timeout) as x: return x.status,x.read()
    except urllib.error.HTTPError as e: return e.code,e.read()
url="https://job.masyadi.com/api/jobs-v4?id=UDBw74HALpxk7-PgZp14I"
for i in range(30):
    code,body=req("GET",url)
    txt=body.decode() if isinstance(body,bytes) else str(body)
    print(f"[{i}] {code} {txt[:500]}")
    try:
        j=json.loads(txt)
        st=(j.get("status") or j.get("state") or "").lower()
        if st in ("completed","failed","error","succeeded"):
            print("FINAL:",json.dumps(j,ensure_ascii=False)[:1500]); break
    except: pass
    time.sleep(3)
