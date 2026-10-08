"""Forensic AI web API (FastAPI).  Run:  py -m uvicorn server.main:app --port 8000   (from the project root)"""
import asyncio
import collections
import json
import os
import sys
import time
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # sibling modules (model.py, engine.py, ...)

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from db import Store, new_record
from engine import MAX_SECONDS, AudioError, Engine

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(ROOT, "model")
SAMPLES_DIR = os.path.join(ROOT, "samples")
WEB_DIR = os.path.join(ROOT, "web")
MAX_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "15")) * 1024 * 1024
VERSION = "1.0.0"
STARTED = time.time()

state = {}


@asynccontextmanager
async def lifespan(app):
    state["engine"] = Engine(os.path.join(MODEL_DIR, "final.pt"), os.path.join(MODEL_DIR, "cam_ref.json"),
                             device=os.environ.get("DEVICE") or None)
    state["store"] = Store()
    state["store"].hub.bind(asyncio.get_running_loop())
    print(f"[forensic-ai] model on {state['engine'].device}; db={state['store'].describe()}", flush=True)
    yield


app = FastAPI(title="Forensic AI - Audio Deepfake Detector", version=VERSION, lifespan=lifespan)
origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["*"],
                   allow_credentials=False, max_age=600)

# --------------------------------------------------------------------------- tiny in-memory rate limiter
_hits = collections.defaultdict(collections.deque)


def rate_limit(request: Request, kind: str, per_minute: int):
    fwd = request.headers.get("x-forwarded-for", "")
    ip = (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?"))
    q = _hits[(ip, kind)]
    now = time.time()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= per_minute:
        raise HTTPException(429, f"Too many requests - limit is {per_minute} per minute. Please slow down.")
    q.append(now)


# --------------------------------------------------------------------------- API
@app.get("/api/health")
def health():
    eng, store = state["engine"], state["store"]
    return {"status": "ok", "version": VERSION, "device": eng.device, "params_m": round(eng.params_m, 2),
            "uptime_s": round(time.time() - STARTED), "database": store.describe(),
            "limits": {"max_seconds": MAX_SECONDS, "max_upload_mb": MAX_BYTES // (1024 * 1024)}}


@app.get("/api/model-info")
def model_info():
    def load(name):
        p = os.path.join(MODEL_DIR, name)
        return json.load(open(p)) if os.path.exists(p) else None
    return {"results": load("results_table.json"), "latency": load("latency.json"), "robustness": load("robustness.json"),
            "gradcam": load("gradcam_stats.json"),
            "dataset": {"name": "ASVspoof 2019 LA (subset)", "train": 7740, "dev": 4000, "eval": 14000,
                        "unseen_attacks": 11, "note": "min t-DCF computed on our subset; not comparable with the public leaderboard"}}


@app.get("/api/samples")
def samples():
    out = []
    for name in sorted(os.listdir(SAMPLES_DIR)):
        if not name.endswith((".flac", ".wav", ".mp3", ".ogg")):
            continue
        stem = name.rsplit(".", 1)[0]
        label = "real" if stem.startswith("real") else "fake"
        attack = stem.split("__")[0].replace("fake_", "").replace("_MISSED_by_model", "") if label == "fake" else ""
        missed = "MISSED" in stem
        title = ("Genuine speech" if label == "real" else f"Synthetic - attack {attack}") + (" (known miss)" if missed else "")
        out.append({"name": name, "label": label, "attack": attack, "known_miss": missed, "title": title,
                    "url": f"/samples/{name}"})
    out.sort(key=lambda s: (s["label"] != "real", s["known_miss"], s["name"]))
    return out


def _read_upload(file: UploadFile) -> bytes:
    raw = file.file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, f"File too large - limit is {MAX_BYTES // (1024 * 1024)} MB.")
    if not raw:
        raise HTTPException(400, "Empty upload.")
    return raw


@app.post("/api/analyze")
def analyze(request: Request, file: UploadFile = File(...), source: str = "upload", client_id: str = "",
            cam: int = Query(1), log: int = Query(1), window: int | None = Query(None), live: int = Query(0)):
    rate_limit(request, "live" if live else "analyze", 150 if live else 40)
    raw = _read_upload(file)
    eng, store = state["engine"], state["store"]
    try:
        x, info = eng.decode(raw)
    except AudioError as e:
        raise HTTPException(400, str(e))
    res = eng.analyse(x, info, filename=os.path.basename(file.filename or "audio"), want_cam=bool(cam), window=window)
    if log:
        rec = new_record(res, source, client_id)
        store.add(rec)
        res["record"] = rec
    return JSONResponse(res)


@app.get("/api/history")
def history(limit: int = 25, client_id: str = ""):
    return {"items": state["store"].recent(limit, client_id or None), "database": state["store"].describe()}


@app.get("/api/stats")
def stats():
    return state["store"].stats()


@app.get("/api/stream")
async def stream(request: Request):
    hub = state["store"].hub
    q = hub.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            yield f"data: {json.dumps({'type': 'hello', 'database': state['store'].describe()})}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), 15)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                if await request.is_disconnected():
                    break
        finally:
            hub.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


# --------------------------------------------------------------------------- static (local convenience; Vercel serves web/ itself)
app.mount("/samples", StaticFiles(directory=SAMPLES_DIR), name="samples")
if os.path.isdir(WEB_DIR):
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
else:
    @app.get("/")
    def root():
        return {"service": "Forensic AI API", "status": "ok", "health": "/api/health", "docs": "/docs"}
