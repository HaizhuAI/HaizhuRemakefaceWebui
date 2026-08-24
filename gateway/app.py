#!/usr/bin/env python3
"""
RemakeFace Pro 生图工作站 — OpenAI 兼容 API 网关 + WebUI 后端

  WebUI   : GET /                 (管理员密码登录)
  Gateway : GET /v1/models        (Authorization: Bearer <GATEWAY_TOKEN>)
            POST /v1/images/generations
  Internal: /api/generate, /api/jobs/{id}, /api/image/{name}, /api/history
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from client import MAX_COUNT, MAX_PROMPT, RATIOS, RemakeClient, RemakeError

ROOT = Path(__file__).resolve().parents[1]
WEBUI_DIR = ROOT / "webui"
STATE_PATH = ROOT / "state" / "client_state.json"
DATA_DIR = ROOT / "data"
GEN_DIR = DATA_DIR / "generated"
JOBS_PATH = DATA_DIR / "jobs.json"
GEN_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- config env
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")   # 改这个！WebUI 管理员密码
GATEWAY_TOKEN = os.environ.get("GATEWAY_TOKEN", "")             # OpenAI 网关 Bearer token；空则 = sha256(ADMIN_PASSWORD)
SESSION_SECRET = os.environ.get("SESSION_SECRET", "remakeface-pro-secret-change-me")
PORT = int(os.environ.get("PORT", "8611"))
if not GATEWAY_TOKEN:
    GATEWAY_TOKEN = hashlib.sha256(("gateway:" + ADMIN_PASSWORD).encode()).hexdigest()[:48]

app = FastAPI(title="RemakeFace Pro Gateway", version="2.0.0")

_client_lock = threading.Lock()
_client: RemakeClient | None = None

_jobs_lock = threading.Lock()
_jobs: dict = {}
_pool = ThreadPoolExecutor(max_workers=3)

MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif"}

SIZE_RATIO = {
    "1024x1024": "1:1", "1024x1792": "9:16", "1792x1024": "16:9",
    "768x1024": "3:4", "1024x768": "4:3", "1536x1024": "3:2",
    "512x512": "1:1", "256x256": "1:1",
}


def get_client() -> RemakeClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = RemakeClient(state_path=str(STATE_PATH))
        return _client


def _load_jobs():
    global _jobs
    try:
        if JOBS_PATH.exists():
            _jobs = json.loads(JOBS_PATH.read_text())
    except Exception:
        _jobs = {}


def _save_jobs():
    try:
        JOBS_PATH.write_text(json.dumps(_jobs, ensure_ascii=False, indent=1))
    except Exception:
        pass


_load_jobs()


# ---------------------------------------------------------------- auth utils
def _sess_sign(payload: str) -> str:
    return hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _make_cookie() -> str:
    val = f"{int(time.time())}.{uuid.uuid4().hex}"
    return f"{val}.{_sess_sign(val)}"


def _check_cookie(cookie: str | None) -> bool:
    if not cookie:
        return False
    parts = cookie.split(".")
    if len(parts) != 3:
        return False
    val = ".".join(parts[:2])
    if not hmac.compare_digest(_sess_sign(val), parts[2]):
        return False
    try:
        ts = int(parts[0])
    except ValueError:
        return False
    return time.time() - ts < 7 * 86400


def _require_admin(request: Request):
    if not _check_cookie(request.cookies.get("rf_admin")):
        raise HTTPException(status_code=401, detail="管理员未登录")


def _auth_gateway(authorization: str | None):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Bearer token required")
    if not hmac.compare_digest(token.strip(), GATEWAY_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid token")


# ---------------------------------------------------------------- webui
@app.get("/")
def index(request: Request):
    if not _check_cookie(request.cookies.get("rf_admin")):
        return RedirectResponse("/login")
    return FileResponse(WEBUI_DIR / "index.html")


@app.get("/login")
def login_page():
    return FileResponse(WEBUI_DIR / "login.html")


@app.post("/api/login")
async def api_login(request: Request):
    body = await request.json()
    pw = body.get("password", "")
    if not hmac.compare_digest(pw, ADMIN_PASSWORD):
        raise HTTPException(status_code=403, detail="密码错误")
    resp = JSONResponse({"ok": True})
    resp.set_cookie("rf_admin", _make_cookie(), max_age=7 * 86400,
                    httponly=True, samesite="lax", path="/")
    return resp


@app.post("/api/logout")
def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("rf_admin", path="/")
    return resp


# ---------------------------------------------------------------- internal api
@app.get("/api/health")
def health(request: Request):
    _require_admin(request)
    try:
        c = get_client()
        cfg = c.config()
        sess = {"config": bool(cfg), "models": len(c.models()), "tasks": c.tasks_base()}
    except Exception as e:
        return {"ok": False, "error": str(e), "session": None}
    return {"ok": True, "session": sess}


@app.get("/api/models")
def api_models(request: Request):
    _require_admin(request)
    try:
        models = get_client().models()
    except Exception:
        from client import FALLBACK_MODELS
        models = [{"id": m["id"], "name": m["name"], "description": m["desc"],
                   "group": m["group"], "priceCredit": 0} for m in FALLBACK_MODELS]
    return {"models": models}


@app.get("/api/ratios")
def api_ratios(request: Request):
    _require_admin(request)
    return {"ratios": RATIOS}


@app.post("/api/generate")
async def api_generate(request: Request,
                       prompt: str = Form(...),
                       model: str = Form("seedream5.0_pro"),
                       ratio: str = Form("Original"),
                       count: int = Form(1),
                       file: UploadFile | None = File(None),
                       file2: UploadFile | None = File(None)):
    _require_admin(request)
    prompt = prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt 不能为空")
    if len(prompt) > MAX_PROMPT:
        raise HTTPException(status_code=400, detail=f"prompt 超过 {MAX_PROMPT} 字符")
    refs = []
    for f in (file, file2):
        if f and f.filename:
            data = await f.read()
            if not data:
                continue
            try:
                key = get_client().upload_image("ai-generate", data,
                                                f.content_type or "image/jpeg")
                refs.append(key)
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"参考图上传失败: {e}")
    job_id = uuid.uuid4().hex[:12]
    rec = {"id": job_id, "kind": "aiImageGen", "prompt": prompt, "model": model,
           "ratio": ratio, "count": max(1, min(count, MAX_COUNT)),
           "status": "pending", "created": time.strftime("%Y-%m-%d %H:%M:%S"),
           "refs": len(refs), "imageUrls": [], "error": None}
    with _jobs_lock:
        _jobs[job_id] = rec
        _save_jobs()
    _pool.submit(_run_gen, job_id, prompt, model, ratio, count, refs)
    return {"jobId": job_id}


def _run_gen(job_id: str, prompt: str, model: str, ratio: str, count: int, refs: list):
    rec = _jobs.get(job_id)
    try:
        with _jobs_lock:
            rec["status"] = "running"
            _save_jobs()
        res = get_client().generate_images(prompt, model, ratio, count, refs)
        urls = []
        for u in res.get("resultUrls", []):
            local = _cache_image(u, job_id, urls.__len__())
            urls.append(local)
        with _jobs_lock:
            rec["status"] = "completed"
            rec["imageUrls"] = urls
            _save_jobs()
    except Exception as e:
        with _jobs_lock:
            rec["status"] = "failed"
            rec["error"] = str(e)
            _save_jobs()


def _sign_url(name: str) -> str:
    """生成带 HMAC 签名+时间戳的取图 URL（24h 有效，免 cookie/token 可直接下载）。"""
    t = int(time.time())
    s = hmac.new(SESSION_SECRET.encode(), f"{name}.{t}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"/api/image/{name}?t={t}&s={s}"


def _check_sig(name: str, t: str | None, s: str | None) -> bool:
    if not t or not s:
        return False
    try:
        ts = int(t)
    except ValueError:
        return False
    if abs(time.time() - ts) > 86400:   # 24h
        return False
    expect = hmac.new(SESSION_SECRET.encode(), f"{name}.{ts}".encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(expect, s)


def _cache_image(remote_url: str, job_id: str, idx: int) -> str:
    import urllib.request
    name = f"{job_id}_{idx}.webp"
    dest = GEN_DIR / name
    if not dest.exists():
        req = urllib.request.Request(remote_url, headers={"User-Agent": "okhttp/4.12.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
        # try to normalize to a browser-friendly format
        try:
            im = Image.open(io.BytesIO(raw))
            im.load()
            if im.format == "WEBP":
                dest.write_bytes(raw)
            else:
                out = io.BytesIO()
                im.convert("RGB").save(out, "WEBP", quality=93)
                dest.write_bytes(out.getvalue())
        except Exception:
            dest.write_bytes(raw)
    return f"/api/image/{name}"


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, request: Request):
    _require_admin(request)
    j = _jobs.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    return j


@app.get("/api/image/{name}")
def image_file(name: str, request: Request,
               authorization: str | None = Header(None)):
    # 管理员 cookie / 网关 Bearer token / 签名URL 三选一（API 调用方没有 cookie）
    if not _check_cookie(request.cookies.get("rf_admin")):
        try:
            _auth_gateway(authorization)
        except HTTPException:
            if not _check_sig(name, request.query_params.get("t"), request.query_params.get("s")):
                raise HTTPException(status_code=401, detail="需要管理员登录、Bearer token 或有效签名 URL")
    if not re.fullmatch(r"[A-Za-z0-9_\-]+\.(webp|png|jpg|jpeg|gif)", name):
        raise HTTPException(status_code=400, detail="bad name")
    p = GEN_DIR / name
    if not p.exists():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(p, media_type=MIME.get(p.suffix, "application/octet-stream"))


@app.get("/api/history")
def history(limit: int = 20, request: Request = None):
    _require_admin(request)
    items = sorted(_jobs.values(), key=lambda x: x.get("created", ""), reverse=True)
    return {"items": items[: max(1, min(limit, 100))]}


# ------------------------------------------------------- faceswap (bonus)
@app.post("/api/faceswap")
async def api_faceswap(request: Request,
                       source: UploadFile = File(...),
                       target: UploadFile = File(...),
                       swap_type: str = Form("standard"),
                       enhance: bool = Form(False)):
    _require_admin(request)
    src = await source.read()
    tgt = await target.read()
    if not src or not tgt:
        raise HTTPException(status_code=400, detail="需要源图与目标图")
    try:
        c = get_client()
        sk = c.upload_image("uploads", src, source.content_type or "image/jpeg")
        tk = c.upload_image("uploads", tgt, target.content_type or "image/jpeg")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"上传失败: {e}")
    job_id = uuid.uuid4().hex[:12]
    rec = {"id": job_id, "kind": "faceswap", "prompt": "face swap",
           "model": "faceswap", "ratio": "-", "count": 1,
           "status": "pending", "created": time.strftime("%Y-%m-%d %H:%M:%S"),
           "refs": 2, "imageUrls": [], "error": None}
    with _jobs_lock:
        _jobs[job_id] = rec
        _save_jobs()
    _pool.submit(_run_swap, job_id, sk, tk, swap_type, enhance)
    return {"jobId": job_id}


def _run_swap(job_id: str, sk: str, tk: str, swap_type: str, enhance: bool):
    rec = _jobs.get(job_id)
    try:
        with _jobs_lock:
            rec["status"] = "running"
            _save_jobs()
        res = get_client().face_swap(sk, tk, swap_type, enhance)
        urls = []
        for u in res.get("resultUrls", []):
            urls.append(_cache_image(u, job_id, urls.__len__()))
        with _jobs_lock:
            rec["status"] = "completed"
            rec["imageUrls"] = urls
            _save_jobs()
    except Exception as e:
        with _jobs_lock:
            rec["status"] = "failed"
            rec["error"] = str(e)
            _save_jobs()


# ------------------------------------------------------- OpenAI-compatible
@app.get("/v1/models")
def v1_models(authorization: str | None = Header(None)):
    _auth_gateway(authorization)
    try:
        models = get_client().models()
    except Exception:
        from client import FALLBACK_MODELS
        models = [{"id": m["id"], "name": m["name"], "description": m["desc"],
                   "group": m["group"], "priceCredit": 0} for m in FALLBACK_MODELS]
    return {"object": "list", "data": [
        {"id": m["id"], "object": "model", "created": 1700000000,
         "owned_by": "remakeface-pro", "name": m.get("name", m["id"])}
        for m in models
    ]}


@app.post("/v1/images/edits")
async def v1_edits(request: Request, authorization: str | None = Header(None)):
    """OpenAI /v1/images/edits 兼容：multipart/form-data。
    image=参考图（必填），prompt/model/n/size/response_format 同官方语义。
    mask 作为第二张参考图一并上传（模型侧按图生图处理）。
    """
    _auth_gateway(authorization)
    try:
        form = await request.form()
    except Exception:
        raise HTTPException(status_code=400, detail="expected multipart/form-data")
    prompt = str(form.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt 不能为空")
    model = str(form.get("model") or "banana2")
    try:
        n = int(form.get("n") or 1)
    except Exception:
        n = 1
    size = str(form.get("size") or "1024x1024")
    ratio = SIZE_RATIO.get(size, "Original")
    ref_keys = []
    for k in ("image", "mask"):
        f = form.get(k)
        if f is None or not hasattr(f, "read"):
            continue
        data = await f.read()
        if not data:
            continue
        try:
            ref_keys.append(get_client().upload_image("ai-generate", data,
                                                      getattr(f, "content_type", None) or "image/png"))
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"参考图上传失败: {e}")
    if not ref_keys:
        raise HTTPException(status_code=400, detail="image 字段必填（图生图需要参考图）")
    job_id = uuid.uuid4().hex[:12]
    rec = {"id": job_id, "kind": "aiImageGen", "prompt": prompt, "model": model,
           "ratio": ratio, "count": max(1, min(n, MAX_COUNT)),
           "status": "pending", "created": time.strftime("%Y-%m-%d %H:%M:%S"),
           "refs": len(ref_keys), "imageUrls": [], "error": None,
           "api": "openai-edits"}
    with _jobs_lock:
        _jobs[job_id] = rec
        _save_jobs()
    _pool.submit(_run_gen, job_id, prompt, model, ratio, rec["count"], ref_keys)
    deadline = time.time() + 300
    while time.time() < deadline:
        time.sleep(2)
        with _jobs_lock:
            st = dict(_jobs.get(job_id, {}))
        if st.get("status") == "completed":
            fmt = str(form.get("response_format") or "url")
            data_out = []
            for u in st.get("imageUrls", []):
                if fmt == "b64_json":
                    p = GEN_DIR / Path(u).name
                    b64 = base64.b64encode(p.read_bytes()).decode() if p.exists() else ""
                    data_out.append({"b64_json": b64})
                else:
                    name = Path(u).name
                    abs_u = u if u.startswith("http") else f"{str(request.base_url).rstrip('/')}{_sign_url(name)}"
                    data_out.append({"url": abs_u, "revised_prompt": prompt})
            return {"created": int(time.time()), "data": data_out,
                    "job_id": job_id, "model": model}
        if st.get("status") == "failed":
            raise HTTPException(status_code=502, detail=st.get("error") or "生成失败")
    raise HTTPException(status_code=504, detail="生成超时（>300s）")


@app.post("/v1/images/generations")
async def v1_generations(request: Request, authorization: str | None = Header(None)):
    _auth_gateway(authorization)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    model = str(body.get("model") or "seedream5.0_pro")
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt 不能为空")
    n = int(body.get("n") or 1)
    size = str(body.get("size") or "1024x1024")
    ratio = SIZE_RATIO.get(size, "Original")
    ref_urls = body.get("image") or body.get("reference_images") or []
    # OpenAI 兼容：image 可为 string URL 或 [{url, ...}]
    if isinstance(ref_urls, str):
        ref_urls = [ref_urls]
    ref_keys = []
    for ru in ref_urls:
        if isinstance(ru, dict):
            ru = ru.get("url") or ru.get("b64_json") or ""
        if not ru:
            continue
        try:
            if ru.startswith("data:") or ru.startswith("http"):
                import urllib.request
                if ru.startswith("data:"):
                    _, b64 = ru.split(",", 1)
                    data = base64.b64decode(b64)
                    ct = "image/png"
                else:
                    req = urllib.request.Request(ru, headers={"User-Agent": "okhttp/4.12.0"})
                    with urllib.request.urlopen(req, timeout=60) as r:
                        data = r.read()
                    ct = r.headers.get("Content-Type") or "image/jpeg"
                ref_keys.append(get_client().upload_image("ai-generate", data, ct))
            else:
                ref_keys.append(ru)  # already a storage key
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"参考图处理失败: {e}")

    job_id = uuid.uuid4().hex[:12]
    rec = {"id": job_id, "kind": "aiImageGen", "prompt": prompt, "model": model,
           "ratio": ratio, "count": max(1, min(n, MAX_COUNT)),
           "status": "pending", "created": time.strftime("%Y-%m-%d %H:%M:%S"),
           "refs": len(ref_keys), "imageUrls": [], "error": None,
           "api": "openai"}
    with _jobs_lock:
        _jobs[job_id] = rec
        _save_jobs()
    _pool.submit(_run_gen, job_id, prompt, model, ratio, rec["count"], ref_keys)

    # 同步等待（OpenAI Images API 语义）
    deadline = time.time() + 300
    while time.time() < deadline:
        time.sleep(2)
        with _jobs_lock:
            st = dict(_jobs.get(job_id, {}))
        if st.get("status") == "completed":
            fmt = str(body.get("response_format") or "url")
            data_out = []
            for u in st.get("imageUrls", []):
                if fmt == "b64_json":
                    p = GEN_DIR / Path(u).name
                    b64 = base64.b64encode(p.read_bytes()).decode() if p.exists() else ""
                    data_out.append({"b64_json": b64})
                else:
                    name = Path(u).name
                    abs_u = u if u.startswith("http") else f"{str(request.base_url).rstrip('/')}{_sign_url(name)}"
                    data_out.append({"url": abs_u, "revised_prompt": prompt})
            return {"created": int(time.time()), "data": data_out,
                    "job_id": job_id, "model": model}
        if st.get("status") == "failed":
            raise HTTPException(status_code=502, detail=st.get("error") or "生成失败")
    raise HTTPException(status_code=504, detail="生成超时（>300s）")


app.mount("/static", StaticFiles(directory=str(WEBUI_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
