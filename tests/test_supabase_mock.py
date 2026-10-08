"""Checks the Supabase mirror against a local mock of the Supabase REST API (no account needed)."""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
import db  # noqa: E402

CAPTURED = []
CANNED = [{"id": "00000000-0000-0000-0000-000000000001", "created_at": "2026-10-08T10:00:00+00:00", "filename": "x.wav", "source": "upload",
           "client_id": "c", "duration_s": 3.0, "verdict": "FAKE", "prob_fake": 0.9, "max_prob": 0.9, "n_windows": 1, "n_flagged": 1, "latency_ms": 50.0}]


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        CAPTURED.append(("POST", self.path, dict(self.headers), json.loads(body)))
        self.send_response(201); self.end_headers()

    def do_GET(self):
        CAPTURED.append(("GET", self.path, dict(self.headers), None))
        out = json.dumps(CANNED).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)


def test_supabase_mirror_and_reads():
    srv = HTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    db.SUPABASE_URL, db.SUPABASE_KEY = f"http://127.0.0.1:{srv.server_port}", "service-key-123"
    store = db.Store(os.path.join(tempfile.mkdtemp(), "t.db"))
    assert store.describe()["backend"] == "sqlite+supabase"

    res = {"filename": "a.wav", "duration_s": 3.0, "verdict": "REAL", "prob_fake": 0.1, "max_prob": 0.2, "n_windows": 1, "n_flagged": 0, "latency_ms": 12.3}
    rec = db.new_record(res, "upload", "client-1")
    store.add(rec)
    for _ in range(50):
        if any(c[0] == "POST" for c in CAPTURED):
            break
        time.sleep(0.05)
    method, path, headers, body = next(c for c in CAPTURED if c[0] == "POST")
    assert path == "/rest/v1/analyses"
    assert headers["apikey"] == "service-key-123" and headers["Authorization"] == "Bearer service-key-123"
    assert headers["Prefer"] == "return=minimal"
    assert body["id"] == rec["id"] and body["verdict"] == "REAL" and set(body) == set(db.FIELDS)

    items = store.recent(5, client_id="client-1")           # read path goes to Supabase when configured
    assert items == CANNED
    get = [c for c in CAPTURED if c[0] == "GET"][-1]
    assert "order=created_at.desc" in get[1] and "client_id=eq.client-1" in get[1] and "limit=5" in get[1]
    assert store.stats()["total"] == 1 and store.stats()["fake"] == 1
    srv.shutdown()


def test_supabase_failure_never_breaks_analysis():
    db.SUPABASE_URL, db.SUPABASE_KEY = "http://127.0.0.1:9", "k"   # nothing listens on port 9
    store = db.Store(os.path.join(tempfile.mkdtemp(), "t2.db"))
    res = {"filename": "a.wav", "duration_s": 3.0, "verdict": "REAL", "prob_fake": 0.1, "max_prob": 0.2, "n_windows": 1, "n_flagged": 0, "latency_ms": 1.0}
    store.add(db.new_record(res))                                # must not raise
    assert len(store.recent(3)) == 1                              # falls back to the local SQLite copy
    assert store.describe()["supabase_error"]


def test_filename_privacy_switch():
    os.environ["STORE_FILENAMES"] = "0"
    try:
        rec = db.new_record({"filename": "secret-call.wav", "duration_s": 1.0, "verdict": "REAL", "prob_fake": .1, "max_prob": .1,
                             "n_windows": 1, "n_flagged": 0, "latency_ms": 1.0})
        assert rec["filename"] == "(hidden)"
    finally:
        os.environ.pop("STORE_FILENAMES")
