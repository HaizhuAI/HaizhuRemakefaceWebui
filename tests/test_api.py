#!/usr/bin/env python3
"""Smoke test: 启动服务 -> 登录 -> 模型/比例 -> OpenAI 网关鉴权。生图部分可开关。"""
import json, os, sys, time, urllib.request, urllib.error, hashlib, subprocess, signal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("TEST_PORT", "8611"))
BASE = f"http://127.0.0.1:{PORT}"
PW = "admin123"
TOKEN = hashlib.sha256(("gateway:" + PW).encode()).hexdigest()[:48]
GENERATE = os.environ.get("TEST_GENERATE", "0") == "1"

_COOKIE = {"cookie": None}

def req(method, path, obj=None, headers=None, timeout=60):
    h = {}
    if _COOKIE["cookie"]:
        h["Cookie"] = _COOKIE["cookie"]
    body = None
    if obj is not None:
        h["Content-Type"] = "application/json"
        body = json.dumps(obj).encode()
    if headers: h.update(headers)
    r = urllib.request.Request(BASE + path, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as x:
            sc = x.headers.get("Set-Cookie")
            if sc:
                _COOKIE["cookie"] = sc.split(";")[0]
            return x.status, json.loads(x.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode() or "{}")
        except Exception: return e.code, {}

def test():
    # 未登录访问 / 应重定向到 /login
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open(BASE + "/", timeout=10)
        print("FAIL: / 未登录应重定向"); sys.exit(1)
    except urllib.error.HTTPError as e:
        assert e.code in (307, 302), e.code
    print("PASS: 未登录重定向")
    # 登录
    code, j = req("POST", "/api/login", {"password": PW})
    assert code == 200, (code, j)
    print("PASS: 管理员登录")
    # 错误密码
    code, _ = req("POST", "/api/login", {"password": "wrong"})
    assert code == 403, code
    print("PASS: 错误密码拒绝")
    # 模型
    code, j = req("GET", "/api/models")
    assert code == 200 and len(j.get("models", [])) >= 9, j
    print(f"PASS: 模型清单 {len(j['models'])} 个:", [m["id"] for m in j["models"]][:5], "...")
    # 比例
    code, j = req("GET", "/api/ratios")
    assert code == 200 and "1:1" in j.get("ratios", []), j
    print("PASS: 比例:", j["ratios"])
    # 健康
    code, j = req("GET", "/api/health")
    assert code == 200 and j.get("ok"), j
    print("PASS: 健康检查 ok")
    # OpenAI 网关无 token -> 401
    code, _ = req("GET", "/v1/models")
    assert code == 401, code
    print("PASS: 网关无 token 拒绝")
    # 错误 token -> 401
    code, _ = req("GET", "/v1/models", headers={"Authorization": "Bearer wrong"})
    assert code == 401, code
    print("PASS: 网关错误 token 拒绝")
    # 正确 token -> 模型列表
    code, j = req("GET", "/v1/models", headers={"Authorization": "Bearer " + TOKEN})
    assert code == 200 and j.get("object") == "list", (code, j)
    print("PASS: OpenAI /v1/models 鉴权通过")
    if GENERATE:
        code, j = req("POST", "/v1/images/generations",
                      {"model": "seedream5.0_pro", "prompt": "a tiny red cube on white",
                       "size": "1024x1024", "n": 1},
                      headers={"Authorization": "Bearer " + TOKEN}, timeout=320)
        assert code == 200 and j.get("data"), (code, j)
        url = j["data"][0]["url"]
        print("PASS: OpenAI 生图 ->", url)
        # img2img：带参考图（b64）走 OpenAI 兼容网关
        import base64 as _b64
        # 生成一张真实可解析的 64x64 PNG
        _png = (ROOT / "data" / "_ref_probe.png")
        if not _png.exists():
            from PIL import Image
            im = Image.new("RGB", (64, 64), (120, 60, 200))
            im.save(_png, "PNG")
        png = _b64.b64encode(_png.read_bytes()).decode()
        code, j = req("POST", "/v1/images/generations",
                      {"model": "banana2", "prompt": "make it red",
                       "size": "512x512", "n": 1,
                       "image": [{"url": "data:image/png;base64," + png}]},
                      headers={"Authorization": "Bearer " + TOKEN}, timeout=320)
        assert code == 200 and j.get("data"), (code, j)
        print("PASS: OpenAI 图生图 ->", j["data"][0]["url"])
    print("\nALL TESTS PASSED")

if __name__ == "__main__":
    test()
