#!/usr/bin/env python3
"""Probe RemakeFace 1.7.7 (masyadi backend) protocol live."""
import base64, hashlib, io, json, sys, time, urllib.request, urllib.error
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

BASE = "https://app-remakeme.masyadi.com/"
PKG = "com.photoeditor.remakemefaceswapaigenerator"
KEY = b"vOVH6sdmpNWjRRIq"

def enc(obj):
    raw = json.dumps(obj, separators=(",", ":")).encode()
    ct = AES.new(KEY, AES.MODE_ECB).encrypt(pad(raw, 16))
    return base64.b64encode(ct).decode()

def dec(data):
    raw = base64.b64decode(data.replace(" ", ""))
    return unpad(AES.new(KEY, AES.MODE_ECB).decrypt(raw), 16).decode()

def req(method, url, obj=None, headers=None, raw_body=None, timeout=60):
    h = {"User-Agent": "okhttp/4.12.0"}
    if obj is not None:
        h["Content-Type"] = "application/json"
        body = json.dumps(obj, separators=(",", ":")).encode()
    elif raw_body is not None:
        body = raw_body
    else:
        body = None
    if headers: h.update(headers)
    r = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

def jget(resp):
    return json.loads(resp.decode() if isinstance(resp, bytes) else resp)

# 1. config
code, body = req("GET", BASE + "api/config/mobile/" + PKG)
print("config:", code)
cfg_raw = jget(body)
print("cfg keys:", list(cfg_raw.keys()))
inner = dec(cfg_raw["data"])
print("decrypted config:", inner[:300])
# tolerate shapes
try:
    cfg = json.loads(inner)
    c = cfg.get("config", cfg)
    c = c.get("data", c) if isinstance(c, dict) else c
except Exception:
    c = {}
url_task = c.get("urlTask"); url_presign = c.get("urlGeneratePresignedUrl")
print("urlTask:", url_task)
print("urlPresign:", url_presign)
models = c.get("aiModelImageGenerator") or []
print("models:", json.dumps(models, ensure_ascii=False)[:800])

# 2. task token
code, body = req("POST", url_task + "/token", None)
print("token:", code, body[:300])
tok = jget(body).get("token")
print("task-token:", tok)
