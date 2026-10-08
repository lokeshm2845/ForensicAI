"""End-to-end browser test (Playwright + installed Edge/Chrome). Server must be running on http://127.0.0.1:8000
   py tests/e2e.py            -> asserts + screenshots in tests/shots/
"""
import io
import os
import sys
import time

import numpy as np
import soundfile as sf
from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE", "http://127.0.0.1:8000")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(ROOT, "tests", "shots")
os.makedirs(SHOTS, exist_ok=True)
SAMPLES = os.path.join(ROOT, "samples")
errors, checks = [], []


def check(name, cond, extra=""):
    checks.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)


def long_wav(path):
    parts = [sf.read(os.path.join(SAMPLES, f))[0] for f in sorted(os.listdir(SAMPLES))[:5]]
    sf.write(path, np.concatenate(parts).astype("float32"), 16000)


def watch(page):
    page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
    page.on("console", lambda m: errors.append("console.error: " + m.text) if m.type == "error" and "favicon" not in m.text and "400 (Bad Request)" not in m.text else None)  # 400 = deliberate bad-file test


with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True,
                          args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"])
    ctx = b.new_context(viewport={"width": 1440, "height": 1000}, permissions=["microphone"], color_scheme="dark")
    page = ctx.new_page(); watch(page)
    page.goto(BASE); page.wait_for_selector("#apiPill.ok", timeout=20000); page.add_style_tag(content=".topbar{position:static!important}")
    check("API pill shows online", "online" in page.inner_text("#apiPill"), page.inner_text("#apiPill"))
    page.wait_for_selector(".sample", timeout=10000)
    check("7 sample clips listed", page.locator(".sample").count() == 7)
    check("hero stats filled", page.locator("#heroStats li").count() == 4, page.inner_text("#heroStats").replace("\n", " "))
    page.screenshot(path=f"{SHOTS}/01_home.png", full_page=True)

    # ---- fake sample -> FAKE
    page.locator(".sample.fake").first.click()
    page.wait_for_selector("#results:not(.hidden)", timeout=30000); page.wait_for_function("document.querySelector('#verdictWord').textContent !== '-'")
    time.sleep(1.4)
    check("fake sample verdict FAKE", page.inner_text("#verdictWord") == "FAKE", page.inner_text("#probText"))
    check("report rendered", "FORENSIC" in page.inner_text("#reportBody").upper() or "Verdict" in page.inner_text("#reportBody"))
    check("spectrogram canvas drawn", page.evaluate("(() => { const c = document.querySelector('#specCanvas'); const d = c.getContext('2d').getImageData(c.width/2|0, c.height/2|0, 1, 1).data; return d[3] > 0 })()"))
    check("band bars rendered", page.locator("#bandBars .band-row").count() == 3)
    page.mouse.move(700, 620)
    box = page.locator("#specCanvas").bounding_box(); page.mouse.move(box["x"] + box["width"] * .45, box["y"] + box["height"] * .5)
    time.sleep(.3); check("spectrogram tooltip appears", page.locator("#specTip").is_visible(), page.inner_text("#specTip").replace("\n", " "))
    page.screenshot(path=f"{SHOTS}/02_fake_result.png", full_page=True)
    page.uncheck("#camToggle"); time.sleep(.2); page.screenshot(path=f"{SHOTS}/03_no_cam.png", clip={**page.locator('.card.evidence').bounding_box()}); page.check("#camToggle")

    # ---- playback does not crash
    page.click("#playBtn"); time.sleep(.6); page.click("#playBtn")

    # ---- real sample -> REAL
    page.locator(".sample.real").first.click()
    page.wait_for_function("document.querySelector('#verdictWord').textContent === 'REAL'", timeout=30000); time.sleep(1.4)
    check("real sample verdict REAL", page.inner_text("#verdictWord") == "REAL", page.inner_text("#probText"))
    page.screenshot(path=f"{SHOTS}/04_real_result.png", full_page=True)

    # ---- long upload with many windows + re-explain a window
    lp = os.path.join(ROOT, "tests", "shots", "long.wav"); long_wav(lp)
    page.set_input_files("#fileInput", lp)
    page.wait_for_function("document.querySelector('#facts').textContent.includes('Windows flagged')", timeout=40000); time.sleep(1)
    import re
    nwin = int(re.search(r"Windows flagged(\d+) / (\d+)", page.text_content("#facts")).group(2))
    check("long upload analysed in several windows", nwin >= 4, f"{nwin} windows")
    page.screenshot(path=f"{SHOTS}/05_long_upload.png", full_page=True)
    tl = page.locator("#tlCanvas").bounding_box(); before = page.inner_text("#evWindowNote")
    page.mouse.click(tl["x"] + 44 + (tl["width"] - 56) * (3.5 / nwin), tl["y"] + tl["height"] * .6)   # bar #4
    time.sleep(2.5); after = page.inner_text("#evWindowNote")
    check("clicking a timeline bar re-explains that window", before != after, f"{before[:40]} -> {after[:40]}")

    # ---- error handling
    bad = os.path.join(ROOT, "tests", "shots", "bad.wav"); open(bad, "wb").write(b"definitely not audio" * 50)
    page.set_input_files("#fileInput", bad); page.wait_for_selector("#errorBox:not(.hidden)", timeout=15000)
    check("bad file shows a friendly error", "decode" in page.inner_text("#errorBox").lower() or "audio" in page.inner_text("#errorBox").lower(), page.inner_text("#errorBox"))

    # ---- recording with the fake microphone
    page.click("#recBtn"); page.wait_for_selector("#recPanel:not([hidden])", timeout=5000); time.sleep(3.2)
    check("record timer runs", page.inner_text("#recTime") != "0:00", page.inner_text("#recTime"))
    page.click("#recStop"); page.wait_for_function("document.querySelector('#reportBody').textContent.includes('microphone-recording')", timeout=40000)
    check("recording analysed", True)

    # ---- live monitor with the fake microphone
    page.click('[data-tab="live"]'); page.click("#liveBtn"); time.sleep(6.5)
    check("live monitor produced updates", int(page.inner_text("#liveCount").split()[0]) >= 2, page.inner_text("#liveCount") + " / " + page.inner_text("#liveVerdict") + " " + page.inner_text("#liveProb"))
    page.screenshot(path=f"{SHOTS}/06_live.png", full_page=True); page.click("#liveBtn"); time.sleep(.4)
    check("live monitor stops", page.inner_text("#liveVerdict") == "IDLE")

    # ---- realtime dashboard: a second browser window must receive new analyses without reloading
    page2 = ctx.new_page(); watch(page2); page2.goto(BASE + "/#dash"); page2.wait_for_selector("#conn.ok", timeout=15000)
    check("dashboard connected to the live stream", "LIVE" in page2.inner_text("#conn"), page2.inner_text("#conn"))
    n0 = page2.locator("#histTable tbody tr").count(); stats0 = page2.inner_text("#stTotal")
    page.click('[data-tab="analyze"]'); page.locator(".sample.fake").nth(1).click(); page.wait_for_selector("#results:not(.hidden)")
    page2.wait_for_selector("#histTable tbody tr.new", timeout=15000)
    check("new analysis appears on the other window in realtime", page2.locator("#histTable tbody tr.new").count() >= 1)
    time.sleep(1.2); check("stats counter updated live", page2.inner_text("#stTotal") != stats0, f"{stats0} -> {page2.inner_text('#stTotal')}")
    page2.screenshot(path=f"{SHOTS}/07_dashboard.png", full_page=True)
    page2.click('[data-tab="about"]'); time.sleep(.8); page2.screenshot(path=f"{SHOTS}/08_about.png", full_page=True)
    check("about tables loaded", page2.locator("#aboutResults table").count() == 1 and page2.locator("#aboutRobust table").count() == 1)

    # ---- light theme + phone viewport
    page.click('[data-tab="analyze"]'); page.click("#themeBtn"); time.sleep(.6); page.screenshot(path=f"{SHOTS}/09_light.png", full_page=True)
    m = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True); pm = m.new_page(); watch(pm)
    pm.goto(BASE); pm.wait_for_selector(".sample"); pm.locator(".sample.fake").first.click(); pm.wait_for_selector("#results:not(.hidden)", timeout=30000); time.sleep(1.4)
    check("no horizontal overflow on phone", pm.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), str(pm.evaluate("document.documentElement.scrollWidth")))
    pm.screenshot(path=f"{SHOTS}/10_mobile.png", full_page=True)
    b.close()

print("\nconsole/page errors:", errors if errors else "none")
bad = [n for n, ok in checks if not ok]
print(f"{len(checks) - len(bad)}/{len(checks)} checks passed" + (f"; FAILED: {bad}" if bad else ""))
sys.exit(1 if bad or errors else 0)
