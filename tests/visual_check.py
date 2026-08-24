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
    page.wait_for_function("document.querySelectorAll('#modelSelect option').length >= 9", timeout=8000)

with sync_playwright() as p:
    errors = []
    # ---------- desktop ----------
    browser = p.chromium.launch(args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    page = ctx.new_page()
    page.on("console", lambda m: errors.append(f"[{m.type}] {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))
    login(page)
    n_models = page.locator("#modelSelect option").count()
    n_ratios = page.locator("#ratioSelect option").count()
    print(f"desktop: models={n_models} ratios={n_ratios}")
    assert n_models >= 9, "model count"
    assert n_ratios == 6, "ratio count"
    # select 9:16 ratio
    page.select_option("#ratioSelect", "9:16")
    ratio_val = page.input_value("#ratioSelect")
    assert ratio_val == "9:16", ratio_val
    print("ratio selection ok:", ratio_val)
    # back to 1:1
    page.select_option("#ratioSelect", "1:1")
    # type a prompt
    page.fill("#prompt", "cyberpunk city, neon rain, cinematic")
    pv = page.input_value("#prompt")
    assert pv == "cyberpunk city, neon rain, cinematic", pv
    print("prompt ok:", pv)
    page.screenshot(path=f"{SHOT}/desktop_main.png", full_page=False)
    page.click("#modeSeg button[data-mode=swap]")
    page.wait_for_selector("#paramsSwap:not([hidden])")
    page.screenshot(path=f"{SHOT}/desktop_faceswap.png")
    page.click("#modeSeg button[data-mode=gen]")
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
    # select 3:4
    page2.select_option("#ratioSelect", "3:4")
    ratio_val2 = page2.input_value("#ratioSelect")
    assert ratio_val2 == "3:4", ratio_val2
    print("mobile ratio ok:", ratio_val2)
    page2.close()
    browser.close()

    print("\nconsole errors:", len(errors))
    for e in errors[:10]: print("  ", e)
    print("\nVISUAL CHECK DONE")
