import base64, json, urllib.request, urllib.error, uuid, time
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad, pad
KEY=b"vOVH6sdmpNWjRRIq"
def enc(o):
    raw=json.dumps(o,separators=(",",":")).encode()
    return base64.b64encode(AES.new(KEY,AES.MODE_ECB).encrypt(pad(raw,16))).decode()
def dec(d):
    raw=base64.b64decode(d.replace(" ",""))
    return unpad(AES.new(KEY,AES.MODE_ECB).decrypt(raw),16).decode()
def req(m,u,obj=None,hdr=None,raw=None,ct=None,timeout=90):
    h={"User-Agent":"okhttp/4.12.0"}
    body=None
    if obj is not None:
        h["Content-Type"]="application/json"; body=json.dumps(obj,separators=(",",":")).encode()
    if raw is not None:
        body=raw
        if ct: h["Content-Type"]=ct
    if hdr: h.update(hdr)
    r=urllib.request.Request(u,data=body,headers=h,method=m)
    try:
        with urllib.request.urlopen(r,timeout=timeout) as x: return x.status,x.read()
    except urllib.error.HTTPError as e: return e.code,e.read()

TASKS="https://app-remakeme.masyadi.com/api/tasks"
PRESIGN="https://temp-file.masyadi.com/api/presigned"

# token
code,body=req("POST",TASKS+"/token")
print("token:",code)
tok=dec(json.loads(body)["data"])
print("token data:",tok[:200])
tokv=json.loads(tok)["token"]
print("task-token:",tokv[:30],"...")

# presign for a tiny test image (1x1 png)
png=base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
ext="png"
obj_name=f"https://temp-file.masyadi.com/api/presigned/ai-generate/{uuid.uuid4()}.{ext}"
code,body=req("POST",PRESIGN,{"data":enc({"objectName":obj_name,"contentType":"image/png","contentLength":len(png)})})
print("presign:",code)
pdata=json.loads(dec(json.loads(body)["data"]))
print("presign data:",json.dumps(pdata)[:400])
url=pdata["url"]; key=pdata.get("objectName") or pdata.get("key")
# upload
code,body=req("PUT",url,raw=png,ct="image/png")
print("upload:",code,body[:100])

# create aiImageGen
job={"jobType":"aiImageGen","jobData":{"image_uri_list":[],"prompt":"a minimalist logo of a seagull, flat design, clean white background","image_ratio":"1:1","model":"seedream5.0_pro","generate_count":1}}
code,body=req("POST",TASKS+"/create",{"data":enc(job)},hdr={"task-token":tokv})
print("create:",code)
try:
    cr=json.loads(dec(json.loads(body)["data"]))
    print("create resp:",json.dumps(cr,ensure_ascii=False)[:500])
except Exception as e:
    print("create resp raw:",body[:400],e)
