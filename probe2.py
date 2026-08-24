import base64, json, urllib.request, urllib.error
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
BASE="https://app-remakeme.masyadi.com/"; PKG="com.photoeditor.remakemefaceswapaigenerator"
KEY=b"vOVH6sdmpNWjRRIq"
def dec(data):
    raw=base64.b64decode(data.replace(" ",""))
    return unpad(AES.new(KEY,AES.MODE_ECB).decrypt(raw),16).decode()
def req(m,u,obj=None,hdr=None):
    h={"User-Agent":"okhttp/4.12.0"}
    body=None
    if obj is not None:
        h["Content-Type"]="application/json"; body=json.dumps(obj,separators=(",",":")).encode()
    if hdr: h.update(hdr)
    r=urllib.request.Request(u,data=body,headers=h,method=m)
    try:
        with urllib.request.urlopen(r,timeout=40) as x: return x.status,x.read()
    except urllib.error.HTTPError as e: return e.code,e.read()
code,body=req("GET",BASE+"api/config/mobile/"+PKG)
cfg=json.loads(dec(json.loads(body)["data"]))
print(json.dumps(cfg,ensure_ascii=False,indent=1)[:4000])
open("config_dump.json","w").write(json.dumps(cfg,ensure_ascii=False,indent=1))
