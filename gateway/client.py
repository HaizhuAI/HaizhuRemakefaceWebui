#!/usr/bin/env python3
"""
RemakeFace Ai 1.7.7 (PRO) backend client — clean-room implementation.
Protocol recovered from `RemakeFace Ai.apk` (Pairip/R8 smali), versionName 1.7.7:

  Base:        https://app-remakeme.masyadi.com/
  Presign:     https://temp-file.masyadi.com/api/presigned   (from config)
  Tasks:       {urlTask}/api/tasks                          (from config + observed)
  Crypto:      AES-128-ECB/PKCS5, key=vOVH6sdmpNWjRRIq
               payload = base64( AES( json ) ), request body = {"data": "<b64>"}

  Flow:
    1. GET  api/config/mobile/{packageName}                    -> encrypted config JSON
    2. POST {tasks}/token                                      -> encrypted {"token": ...}
    3. POST {presign}  body={"data": b64({"objectName": <key>,
                                          "contentType": ..., "contentLength": ...})}
                                                               -> {"url": <PUT url>, "objectName": <key>}
    4. PUT  <url>  (binary)
    5. POST {tasks}/create  header task-token:<token>
         body={"data": b64({"jobType": ..., "jobData": {...}})} -> plain JSON:
              {urlStatusTask, urlCancelTask, pollAfterMs, waitingCount, ...}
    6. GET  urlStatusTask  -> {"status": in_progress|completed|failed, "result": [...]}

  Job types: aiImageGen | faceswap | multifaceswap | getManyFaces | checkNsfw | remover
  PRO models (from live config, all priceCredit=0):
    seedream5.0_pro, seedream5.0, seedream4.5, seedream4,
    banana_pro, banana2, banana, banana2_lite, gpt_image_2
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Optional

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

DEFAULT_BASE = "https://app-remakeme.masyadi.com"
DEFAULT_PKG = "com.photoeditor.remakemefaceswapaigenerator"
AES_KEY = b"vOVH6sdmpNWjRRIq"          # recovered from ju.smali
TASK_API = "/api/tasks"

# Fallback model catalog if the live config endpoint is unreachable.
FALLBACK_MODELS = [
    {"id": "seedream5.0_pro", "name": "Seedream 5 Pro",   "desc": "Premium generation with exceptional detail and visual quality.", "group": "旗舰"},
    {"id": "seedream5.0",     "name": "Seedream 5.0",     "desc": "Smarter generation with refined visual quality.",                 "group": "旗舰"},
    {"id": "seedream4.5",     "name": "Seedream 4.5",     "desc": "Sharp, detailed, and consistent image generation.",               "group": "主流"},
    {"id": "seedream4",       "name": "Seedream 4.0",     "desc": "Flexible model for generation and editing.",                       "group": "主流"},
    {"id": "banana_pro",      "name": "Nano Banana Pro",  "desc": "Best for premium, realistic, and text-heavy visuals.",             "group": "编辑"},
    {"id": "banana2",         "name": "Nano Banana 2",    "desc": "Fast, high-quality generation and editing.",                       "group": "编辑"},
    {"id": "banana",          "name": "Nano Banana",      "desc": "Quick edits and creative image transformations.",                  "group": "编辑"},
    {"id": "banana2_lite",    "name": "Banana 2 Lite",    "desc": "Lightweight and fast generation for everyday image tasks.",       "group": "轻量"},
    {"id": "gpt_image_2",     "name": "GPT Image 2",      "desc": "Accurate prompts, clean edits, detailed results.",                 "group": "编辑"},
]

RATIOS = ["Original", "1:1", "4:3", "3:4", "9:16", "16:9"]
MAX_PROMPT = 20000
MAX_COUNT = 4
UA = "okhttp/4.12.0"


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.replace(" ", ""))


def encrypt(obj: Any) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    ct = AES.new(AES_KEY, AES.MODE_ECB).encrypt(pad(raw, AES.block_size))
    return _b64e(ct)


def decrypt(data: str) -> str:
    raw = _b64d(data)
    return unpad(AES.new(AES_KEY, AES.MODE_ECB).decrypt(raw), AES.block_size).decode()


class RemakeError(RuntimeError):
    pass


class RemakeClient:
    """Thread-safe client for the RemakeFace masyadi backend."""

    def __init__(self, base: str = DEFAULT_BASE, package: str = DEFAULT_PKG,
                 state_path: str | os.PathLike | None = None):
        self.base = base.rstrip("/")
        self.package = package
        self.state_path = Path(state_path) if state_path else None
        self._lock = threading.RLock()
        self._create_lock = threading.Lock()   # 单次有效 task-token：create 必须串行
        self._config: dict = {}
        self._config_at: float = 0
        self._task_token: str | None = None
        self._token_at: float = 0
        self._load_state()

    # ------------------------------------------------------------------ state
    def _load_state(self):
        if self.state_path and self.state_path.exists():
            try:
                st = json.loads(self.state_path.read_text())
                self._config = st.get("config", {})
                self._config_at = st.get("config_at", 0)
                self._task_token = st.get("task_token")
                self._token_at = st.get("token_at", 0)
            except Exception:
                pass

    def _save_state(self):
        if not self.state_path:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "config": self._config, "config_at": self._config_at,
                "task_token": self._task_token, "token_at": self._token_at,
            }, ensure_ascii=False, indent=1))
            tmp.replace(self.state_path)
        except Exception:
            pass

    # ------------------------------------------------------------------ http
    def _request(self, method: str, url: str, body: bytes | None = None,
                 headers: dict | None = None, timeout: float = 90) -> tuple[int, bytes]:
        h = {"User-Agent": UA}
        if body is not None:
            h.setdefault("Content-Type", "application/json")
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, data=body, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def _enc_post(self, url: str, payload: dict, headers: dict | None = None,
                  timeout: float = 60) -> dict:
        code, body = self._request("POST", url, json.dumps({"data": encrypt(payload)},
                                                           separators=(",", ":")).encode(),
                                   headers=headers, timeout=timeout)
        if code not in (200, 201, 202):
            raise RemakeError(f"POST {url} -> {code}: {body[:300]!r}")
        try:
            return json.loads(body.decode())
        except Exception:
            return {"data": body.decode()}

    # ---------------------------------------------------------------- config
    def fetch_config(self, force: bool = False) -> dict:
        with self._lock:
            if self._config and not force and time.time() - self._config_at < 3600:
                return self._config
            code, body = self._request("GET", f"{self.base}/api/config/mobile/{self.package}")
            if code != 200:
                raise RemakeError(f"config -> {code}: {body[:300]!r}")
            raw = json.loads(body.decode())
            if not isinstance(raw, dict) or "data" not in raw:
                raise RemakeError(f"config unexpected shape: {raw!r}")
            cfg = json.loads(decrypt(raw["data"]))
            # tolerate nested shapes: {"config":{"data":{...}}} -> flat
            if isinstance(cfg, dict):
                c2 = cfg.get("config")
                if isinstance(c2, dict):
                    cfg = c2.get("data", c2) if isinstance(c2.get("data"), dict) else c2
            self._config = cfg
            self._config_at = time.time()
            self._save_state()
            return cfg

    def config(self) -> dict:
        return self._config or self.fetch_config()

    def tasks_base(self) -> str:
        # observed: {urlTask}/api/tasks  (urlTask from config, e.g. https://app-remakeme.masyadi.com)
        url_task = (self.config().get("urlTask") or self.base).rstrip("/")
        if url_task.endswith("/api/tasks"):
            return url_task
        return url_task + TASK_API

    def presign_base(self) -> str:
        return self.config().get("urlGeneratePresignedUrl") or "https://temp-file.masyadi.com/api/presigned"

    def models(self) -> list[dict]:
        try:
            cfg = self.config()
        except Exception:
            cfg = {}
        items = cfg.get("aiModelImageGenerator") or []
        out = []
        for it in items:
            mid = it.get("model") or it.get("id")
            if not mid:
                continue
            out.append({
                "id": mid,
                "name": it.get("nameModel") or mid,
                "description": it.get("description") or "",
                "group": it.get("group") or "",
                "priceCredit": it.get("priceCredit", 0),
            })
        if out:
            return out
        return [{"id": m["id"], "name": m["name"], "description": m["desc"],
                 "group": m["group"], "priceCredit": 0} for m in FALLBACK_MODELS]

    # --------------------------------------------------------------- token
    def get_task_token(self, force: bool = False) -> str:
        with self._lock:
            if self._task_token and not force and time.time() - self._token_at < 5400:
                return self._task_token
            url = self.tasks_base() + "/token"
            code, body = self._request("POST", url, timeout=30)
            if code not in (200, 201):
                raise RemakeError(f"task token -> {code}: {body[:300]!r}")
            try:
                data = json.loads(decrypt(json.loads(body.decode())["data"]))
            except Exception:
                data = json.loads(body.decode())
            tok = data.get("token")
            if not tok:
                raise RemakeError(f"task token missing: {data!r}")
            self._task_token = tok
            self._token_at = time.time()
            self._save_state()
            return tok

    # ------------------------------------------------------------ upload
    def upload_image(self, object_name: str, data: bytes,
                     content_type: str | None = None) -> str:
        """Presign + PUT. Returns the storage key (use in jobData).

        Recovered from bb4.h()/n9.smali: the app sends a RELATIVE object
        name  ``{purpose}/{uuid}{ext}``  (purpose = ai-generate | uploads | ...),
        never a full URL. The presign response echoes the same relative key,
        and that exact key is what the backend later re-reads from storage
        (image_uri_list / sourceImage / targetImage ...).
        """
        if not content_type:
            content_type = "image/jpeg"
        ct = content_type.lower()
        if "png" in ct:
            ext = ".png"
        elif "webp" in ct:
            ext = ".webp"
        elif "heic" in ct:
            ext = ".heic"
        elif "heif" in ct:
            ext = ".heif"
        elif "gif" in ct:
            ext = ".gif"
        else:
            ext = ".jpg"
        obj_path = f"{object_name.strip('/')}/{uuid.uuid4()}{ext}"
        resp = self._enc_post(self.presign_base(), {
            "objectName": obj_path,
            "contentType": content_type,
            "contentLength": len(data),
        })
        inner = self._unwrap_data(resp)
        url = inner.get("url") or inner.get("uploadUrl")
        key = inner.get("objectName") or inner.get("key") or obj_path
        if not url:
            raise RemakeError(f"presign missing url: {inner!r}")
        code, body = self._request("PUT", url, body=data,
                                   headers={"Content-Type": content_type,
                                            "Content-Length": str(len(data))},
                                   timeout=120)
        if code not in (200, 201, 204):
            raise RemakeError(f"upload -> {code}: {body[:300]!r}")
        return key

    @staticmethod
    def _unwrap_data(resp: dict) -> dict:
        if isinstance(resp.get("data"), dict):
            return resp["data"]
        if isinstance(resp.get("data"), str):
            d = resp["data"]
            try:
                return json.loads(d)
            except Exception:
                pass
            try:
                return json.loads(decrypt(d))
            except Exception:
                return {"data": d}
        return resp

    # ------------------------------------------------------------- tasks
    def create_task(self, job_type: str, job_data: dict,
                    retry_auth: bool = True) -> dict:
        """POST {tasks}/create with task-token header. Returns plain JSON.

        task-token 单次有效：整个 create（取 token + POST + 鉴权重试）用
        _create_lock 串行化，避免并发请求互相消耗 token 导致
        后端任务报 not authenticated。
        """
        with self._create_lock:
            url = self.tasks_base() + "/create"
            attempts = 2 if retry_auth else 1
            for _ in range(attempts):
                # 后端 task-token 有效期极短（实测 <75s 即失效），且过期 token
                # create 可能返回 200 假成功（任务执行时才报 not authenticated）。
                # 因此每次 create 都强制取全新 token，绝不使用缓存。
                token = self.get_task_token(force=True)
                code, body = self._request(
                    "POST", url,
                    json.dumps({"data": encrypt({"jobType": job_type, "jobData": job_data})},
                               separators=(",", ":")).encode(),
                    headers={"task-token": token}, timeout=60)
                if code in (401, 403) and attempts > 1:
                    time.sleep(1.5)          # 后端限流避让
                    self.get_task_token(force=True)
                    continue
                if code not in (200, 201, 202):
                    raise RemakeError(f"create task -> {code}: {body[:300]!r}")
                try:
                    return json.loads(body.decode())
                except Exception:
                    return self._unwrap_data({"data": body.decode()})
            raise RemakeError(f"create task auth -> {code}: {body[:300]!r}")

    def poll_status(self, status_url: str, poll_after_ms: int = 3000,
                    timeout_s: int = 300) -> dict:
        """Poll until terminal. Returns the final status dict."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            code, body = self._request("GET", status_url, timeout=60)
            if code == 200:
                try:
                    j = json.loads(body.decode())
                except Exception:
                    j = {}
                st = str(j.get("status") or j.get("state") or "").lower()
                if st in ("completed", "succeeded", "success", "done"):
                    return j
                if st in ("failed", "error", "cancelled", "canceled"):
                    raise RemakeError(f"task failed: {j!r}")
                wait = j.get("pollAfterMs") or poll_after_ms
                time.sleep(max(1.0, wait / 1000.0))
                continue
            if code in (401, 403):
                # status endpoints are token-less on this backend; just retry
                time.sleep(2)
                continue
            time.sleep(poll_after_ms / 1000.0)
        raise TimeoutError(f"task timeout: {status_url}")

    # ------------------------------------------------------- high-level ops
    def generate_images(self, prompt: str, model: str = "seedream5.0_pro",
                        ratio: str = "Original", count: int = 1,
                        ref_image_keys: list[str] | None = None,
                        peoples: list | None = None,
                        wait: bool = True) -> dict:
        """aiImageGen. Returns {task, resultUrls} when wait=True."""
        ratio = (ratio or "Original").strip()
        if ratio.lower() == "original":
            ratio = "Original"
        job_data = {
            "image_uri_list": ref_image_keys or [],
            "prompt": prompt,
            "image_ratio": ratio,
            "model": model,
            "generate_count": max(1, min(int(count or 1), MAX_COUNT)),
        }
        if peoples:
            job_data["peoples"] = peoples
        task = self.create_task("aiImageGen", job_data)
        status_url = task.get("urlStatusTask")
        if not status_url:
            task_id = task.get("taskId") or task.get("jobId") or task.get("id")
            if task_id:
                status_url = self.tasks_base() + "/status/" + task_id
        if not status_url:
            raise RemakeError(f"create task no status url: {task!r}")
        task["jobId"] = task.get("jobId") or task.get("taskId") or task.get("id")
        if not wait:
            return {"task": task}
        for attempt in range(2):
            try:
                final = self.poll_status(status_url,
                                         poll_after_ms=int(task.get("pollAfterMs") or 3000))
                urls = final.get("result") or (final.get("data") or {}).get("result") or []
                if isinstance(urls, str):
                    urls = [urls]
                return {"task": task, "final": final, "resultUrls": urls}
            except RemakeError as e:
                msg = str(e)
                # 后端任务认证失败 / 任务超时：强制换 token 整体重试一次
                if attempt == 0 and ("not authenticated" in msg or "timeout after" in msg):
                    time.sleep(3)            # 后端排队/限流避让
                    self.get_task_token(force=True)
                    task = self.create_task("aiImageGen", job_data, retry_auth=True)
                    status_url = task.get("urlStatusTask")
                    if not status_url:
                        task_id = task.get("taskId") or task.get("jobId") or task.get("id")
                        if task_id:
                            status_url = self.tasks_base() + "/status/" + task_id
                    task["jobId"] = task.get("jobId") or task.get("taskId") or task.get("id")
                    continue
                raise

    def face_swap(self, source_key: str, target_key: str,
                  swap_type: str = "standard", enhance: bool = False,
                  wait: bool = True) -> dict:
        task = self.create_task("faceswap", {
            "sourceImage": source_key, "targetImage": target_key,
            "type": swap_type, "versi": "v3", "isEnhance": bool(enhance),
        })
        return self._finish_task(task, wait)

    def multi_face_swap(self, resource_keys: list[str], target_key: str,
                        faces_to_swap: list, enhance: bool = False,
                        wait: bool = True) -> dict:
        task = self.create_task("multifaceswap", {
            "imageResourcePaths": resource_keys, "targetImagePath": target_key,
            "facesToSwap": faces_to_swap, "queueType": "standard",
            "versi": "v3", "isEnhance": bool(enhance),
        })
        return self._finish_task(task, wait)

    def detect_faces(self, image_key: str, wait: bool = True) -> dict:
        task = self.create_task("getManyFaces", {
            "typeImge": "path", "path_image": image_key, "versi": "v3",
        })
        return self._finish_task(task, wait)

    def check_nsfw(self, image_key: str, wait: bool = True) -> dict:
        task = self.create_task("checkNsfw", {
            "typeImge": "path", "path_image": image_key, "versi": "v3",
        })
        return self._finish_task(task, wait)

    def _finish_task(self, task: dict, wait: bool) -> dict:
        status_url = task.get("urlStatusTask")
        if not status_url:
            task_id = task.get("taskId") or task.get("jobId") or task.get("id")
            if task_id:
                status_url = self.tasks_base() + "/status/" + task_id
        task["jobId"] = task.get("jobId") or task.get("taskId") or task.get("id")
        if not wait or not status_url:
            return {"task": task}
        final = self.poll_status(status_url,
                                 poll_after_ms=int(task.get("pollAfterMs") or 3000))
        # faceswap/multifaceswap 的 result 是单个 URL 字符串，aiImageGen 是数组
        result = final.get("result")
        if isinstance(result, str):
            result = [result]
        return {"task": task, "final": final, "resultUrls": result or []}


if __name__ == "__main__":
    import sys
    c = RemakeClient(state_path=sys.argv[1] if len(sys.argv) > 1 else None)
    cfg = c.fetch_config()
    print("config ok:", cfg.get("title"), "| models:", len(c.models()))
    print("token:", c.get_task_token()[:24], "...")
    print("tasks base:", c.tasks_base())
    print("presign base:", c.presign_base())
