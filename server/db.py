"""Persistence + realtime for analysis records. Only metadata is stored - never the audio itself.

* SQLite (always on)       - works offline, zero setup.
* Supabase (optional)      - if SUPABASE_URL and SUPABASE_SERVICE_KEY are set, every record is also written there and
                             history/stats are read from there, so data survives restarts of an ephemeral host and the
                             browser can subscribe to Supabase Realtime directly.
* Hub                      - in-process pub/sub that feeds the Server-Sent-Events stream (/api/stream).
"""
import asyncio
import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone

import httpx

DB_PATH = os.environ.get("FORENSIC_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "analyses.db"))
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
FIELDS = ["id", "created_at", "filename", "source", "client_id", "duration_s", "verdict", "prob_fake", "max_prob",
          "n_windows", "n_flagged", "latency_ms"]


def new_record(res, source="upload", client_id=""):
    return {
        "id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "filename": ((res.get("filename") or "audio")[:80] if os.environ.get("STORE_FILENAMES", "1") != "0" else "(hidden)"),
        "source": source if source in ("upload", "sample", "record") else "upload",
        "client_id": (client_id or "")[:64],
        "duration_s": round(float(res["duration_s"]), 2),
        "verdict": res["verdict"],
        "prob_fake": round(float(res["prob_fake"]), 4),
        "max_prob": round(float(res["max_prob"]), 4),
        "n_windows": int(res["n_windows"]),
        "n_flagged": int(res["n_flagged"]),
        "latency_ms": round(float(res["latency_ms"]), 1),
    }


class Hub:
    """Fan-out of new records to all connected SSE clients (thread-safe: analyses run in worker threads)."""

    def __init__(self):
        self.loop = None
        self.clients = set()

    def bind(self, loop):
        self.loop = loop

    def subscribe(self):
        q = asyncio.Queue(maxsize=100)
        self.clients.add(q)
        return q

    def unsubscribe(self, q):
        self.clients.discard(q)

    def publish(self, event: dict):
        if not self.loop:
            return
        for q in list(self.clients):
            self.loop.call_soon_threadsafe(lambda q=q: q.put_nowait(event) if not q.full() else None)


class Store:
    def __init__(self, path=DB_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self._local = threading.local()
        self.supabase = bool(SUPABASE_URL and SUPABASE_KEY)
        self.supabase_error = None
        self.hub = Hub()
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS analyses (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, filename TEXT, source TEXT, client_id TEXT,
                duration_s REAL, verdict TEXT, prob_fake REAL, max_prob REAL, n_windows INTEGER, n_flagged INTEGER,
                latency_ms REAL)""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_created ON analyses(created_at DESC)")

    def _conn(self):
        if not hasattr(self._local, "c"):
            self._local.c = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
            self._local.c.row_factory = sqlite3.Row
        return self._local.c

    # ------------------------------------------------------------------ write
    def add(self, rec: dict):
        with self._conn() as c:
            c.execute(f"INSERT INTO analyses ({','.join(FIELDS)}) VALUES ({','.join('?' * len(FIELDS))})",
                      [rec[f] for f in FIELDS])
        self.hub.publish({"type": "analysis", "record": rec})
        if self.supabase:
            threading.Thread(target=self._push_supabase, args=(rec,), daemon=True).start()

    def _sb_headers(self):
        return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}

    def _push_supabase(self, rec):
        try:
            r = httpx.post(f"{SUPABASE_URL}/rest/v1/analyses", headers={**self._sb_headers(), "Prefer": "return=minimal"},
                           content=json.dumps(rec), timeout=8)
            r.raise_for_status()
            self.supabase_error = None
        except Exception as e:  # never let the database break an analysis
            self.supabase_error = f"{type(e).__name__}: {e}"[:200]
            print("[supabase] insert failed:", self.supabase_error, flush=True)

    # ------------------------------------------------------------------ read
    def recent(self, limit=25, client_id=None):
        limit = max(1, min(int(limit), 200))
        if self.supabase:
            try:
                params = {"select": ",".join(FIELDS), "order": "created_at.desc", "limit": str(limit)}
                if client_id:
                    params["client_id"] = f"eq.{client_id}"
                r = httpx.get(f"{SUPABASE_URL}/rest/v1/analyses", headers=self._sb_headers(), params=params, timeout=8)
                r.raise_for_status()
                self.supabase_error = None
                return r.json()
            except Exception as e:
                self.supabase_error = f"{type(e).__name__}: {e}"[:200]
        q, args = "SELECT * FROM analyses", []
        if client_id:
            q += " WHERE client_id = ?"
            args.append(client_id)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self._conn().execute(q, args).fetchall()]

    def stats(self):
        rows = self.recent(1000)
        n = len(rows)
        fake = sum(1 for r in rows if r["verdict"] == "FAKE")
        lat = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]
        cutoff = time.time() - 86400
        day = 0
        for r in rows:
            try:
                if datetime.fromisoformat(r["created_at"]).timestamp() >= cutoff:
                    day += 1
            except Exception:
                pass
        return {"total": n, "fake": fake, "real": n - fake, "fake_share": (fake / n) if n else 0.0,
                "avg_latency_ms": (sum(lat) / len(lat)) if lat else 0.0, "last_24h": day,
                "latency_series": [r["latency_ms"] for r in rows[:40]][::-1], "window": "last 1000 analyses"}

    def describe(self):
        return {"backend": "sqlite+supabase" if self.supabase else "sqlite", "supabase_error": self.supabase_error}
