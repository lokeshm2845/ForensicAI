"""Browser test through tests/fake_ngrok.py (ngrok free-plan warning-page behaviour). Needs: API on :8002, fake_ngrok on :8001."""
import functools, http.server, os, shutil, tempfile, threading
from playwright.sync_api import sync_playwright
ROOT = os.getcwd(); ui = os.path.join(tempfile.mkdtemp(), "web"); shutil.copytree(os.path.join(ROOT, "web"), ui)
open(os.path.join(ui, "config.js"), "w").write('window.FORENSIC_CONFIG = {"apiBase": "http://ngrok.localhost:8001", "supabaseUrl": "", "supabaseAnonKey": ""};')
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 5176), functools.partial(http.server.SimpleHTTPRequestHandler, directory=ui)); threading.Thread(target=srv.serve_forever, daemon=True).start()
errs = []
with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True); pg = b.new_page(viewport={"width": 1300, "height": 900}, color_scheme="dark")
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None); pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://127.0.0.1:5176/#dash")
    pg.wait_for_selector("#apiPill.ok", timeout=20000); print("PASS pill:", pg.inner_text("#apiPill"))
    pg.wait_for_selector("#conn.ok", timeout=20000); print("PASS dashboard stream connected through the ngrok-style proxy:", pg.inner_text("#conn"))
    pg.click('[data-tab="analyze"]'); pg.wait_for_selector(".sample"); pg.locator(".sample.fake").first.click()
    pg.wait_for_function("document.querySelector('#verdictWord').textContent==='FAKE'", timeout=40000); print("PASS analysis works")
    pg.click('[data-tab="dash"]'); pg.wait_for_selector("#histTable tbody tr.new", timeout=15000); print("PASS live row arrived over the stream")
    pg.click('[data-tab="about"]'); pg.fill("#apiInput", "http://ngrok.localhost:8001"); pg.click("#apiTest")
    pg.wait_for_function("document.querySelector('#apiMsg').textContent.startsWith('OK')", timeout=15000); print("PASS Connection > Test:", pg.inner_text("#apiMsg"))
    b.close()
print("console errors:", errs or "none")
