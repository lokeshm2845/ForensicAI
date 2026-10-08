"""Simulates the production layout: static UI on one origin (like Vercel), API on another (like a Hugging Face Space)."""
import functools, http.server, os, shutil, sys, tempfile, threading
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.environ.get("API", "http://127.0.0.1:8000")
tmp = tempfile.mkdtemp(); ui = os.path.join(tmp, "web"); shutil.copytree(os.path.join(ROOT, "web"), ui)
open(os.path.join(ui, "config.js"), "w").write('window.FORENSIC_CONFIG = {"apiBase": "%s", "supabaseUrl": "", "supabaseAnonKey": ""};' % API)
h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=ui)
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 5173), h); threading.Thread(target=srv.serve_forever, daemon=True).start()
errs, ok = [], []
with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True); c = b.new_context(viewport={"width": 1300, "height": 900}, color_scheme="dark")
    pg = c.new_page(); pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None); pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://127.0.0.1:5173/"); pg.wait_for_selector("#apiPill.ok", timeout=15000); ok.append("UI(5173) reaches API(8000) cross-origin")
    pg.wait_for_selector(".sample"); ok.append(f"{pg.locator('.sample').count()} samples loaded cross-origin")
    pg2 = c.new_page(); pg2.goto("http://127.0.0.1:5173/#dash"); pg2.wait_for_selector("#conn.ok", timeout=15000); ok.append("SSE live stream works cross-origin: " + pg2.inner_text("#conn"))
    pg.locator(".sample.fake").first.click(); pg.wait_for_function("document.querySelector('#verdictWord').textContent === 'FAKE'", timeout=30000); ok.append("analysis works cross-origin")
    pg2.wait_for_selector("#histTable tbody tr.new", timeout=15000); ok.append("realtime row arrived on dashboard")
    b.close()
print("\n".join("PASS " + o for o in ok)); print("errors:", errs or "none"); sys.exit(1 if errs or len(ok) < 5 else 0)
