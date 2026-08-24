#!/usr/bin/env python3
"""Playwright visual verification: desktop + mobile, login flow, ratio adaptation."""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8611"
SHOT = "/root/haizhucodex/work/remakeface-webui/artifacts"

def login(page):
    page.goto(BASE + "/", wait_until="networkidle")
    assert "/login" in page.url, f"should redirect to login, got {page.url}"
    page.fill("#pw", "admin123")
    page.click("#f button[type=submit]")
    page.wait_for_url(BASE + "/", timeout=8000)
    page.wait_for_selector("#modelGrid .model-opt", timeout=8000)

with sync_playwright() as p:
    errors = []
    # ---------- desktop ----------
    browser = p.chromium.launch(args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    page = ctx.new_page()
    page.on("console", lambda m: errors.append(f"[{m.type}] {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))
    login(page)
    n_models = page.locator(".model-opt").count()
    n_ratios = page.locator(".ratio-btn").count()
    print(f"desktop: models={n_models} ratios={n_ratios}")
    assert n_models >= 9, "model count"
    assert n_ratios == 6, "ratio count"
    # click 9:16 ratio -> frame data-r should change
    page.click('.ratio-btn[data-r="9:16"]')
    frame_r = page.get_attribute("#frame", "data-r")
    assert frame_r == "9:16", frame_r
    print("ratio adaptation 9:16 -> frame data-r =", frame_r)
    # back to 1:1
    page.click('.ratio-btn[data-r="1:1"]')
    # type a prompt, check char count
    page.fill("#prompt", "cyberpunk city, neon rain, cinematic")
    cc = page.locator("#charCount").text_content()
    assert cc == "36", cc
    print("char count ok:", cc)
    page.screenshot(path=f"{SHOT}/desktop_main.png", full_page=False)
    page.click("#tabSwap")
    page.wait_for_selector("#swapPanel:not([hidden])")
    page.screenshot(path=f"{SHOT}/desktop_faceswap.png")
    page.click("#tabGen")
    # health pill
    pill = page.locator("#healthText").text_content()
    print("health:", pill)
    page.close()

    # ---------- mobile ----------
    ctx2 = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1,
                               is_mobile=True, has_touch=True)
    page2 = ctx2.new_page()
    page2.on("console", lambda m: errors.append(f"[m:{m.type}] {m.text}") if m.type == "error" else None)
    login(page2)
    page2.screenshot(path=f"{SHOT}/mobile_main.png")
    # rail should be collapsed (bottom drawer); open it
    rail_cls = page2.get_attribute("#rail", "class")
    print("mobile rail class:", rail_cls)
    page2.click("#railGrip")
    page2.wait_for_timeout(500)
    page2.screenshot(path=f"{SHOT}/mobile_rail_open.png")
    # select 3:4
    page2.click('.ratio-btn[data-r="3:4"]')
    frame_r2 = page2.get_attribute("#frame", "data-r")
    assert frame_r2 == "3:4", frame_r2
    print("mobile ratio ok:", frame_r2)
    page2.close()
    browser.close()

    print("\nconsole errors:", len(errors))
    for e in errors[:10]: print("  ", e)
    print("\nVISUAL CHECK DONE")
