"""API tests. Run from the project root:  py -m pytest -q tests"""
import base64
import io
import os
import sys
import tempfile

import numpy as np
import soundfile as sf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["FORENSIC_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.setdefault("DEVICE", "cpu")

from fastapi.testclient import TestClient  # noqa: E402

from server.main import app  # noqa: E402

SAMPLES = os.path.join(ROOT, "samples")


def sample(prefix):
    return next(f for f in sorted(os.listdir(SAMPLES)) if f.startswith(prefix) and "MISSED" not in f)


def post(c, name, **params):
    with open(os.path.join(SAMPLES, name), "rb") as f:
        return c.post("/api/analyze", params=params, files={"file": (name, f, "audio/flac")})


def test_health_samples_and_analysis():
    with TestClient(app) as c:
        h = c.get("/api/health").json()
        assert h["status"] == "ok" and h["params_m"] > 5 and h["database"]["backend"] == "sqlite"
        sm = c.get("/api/samples").json()
        assert len(sm) == 7 and sm[0]["label"] == "real"

        fake = post(c, sample("fake_A07"), source="sample", client_id="t1")
        assert fake.status_code == 200, fake.text
        j = fake.json()
        assert j["verdict"] == "FAKE" and j["prob_fake"] > 0.9
        assert j["spec"]["w"] == 251 and j["spec"]["h"] == 128
        assert len(base64.b64decode(j["spec"]["data"])) == 251 * 128
        assert len(base64.b64decode(j["cam"]["data"])) == 251 * 128
        assert len(j["waveform"]["min"]) == 900 and "report" in j and j["report"]["text"].startswith("FORENSIC")
        assert j["timings_ms"]["total"] > 0

        real = post(c, sample("real_01"), source="sample", client_id="t1").json()
        assert real["verdict"] == "REAL" and real["prob_fake"] < 0.1

        # history / stats reflect both analyses, newest first
        items = c.get("/api/history", params={"limit": 5, "client_id": "t1"}).json()["items"]
        assert [i["verdict"] for i in items] == ["REAL", "FAKE"]
        st = c.get("/api/stats").json()
        assert st["total"] == 2 and st["fake"] == 1 and st["real"] == 1

        # log=0 and live=1 must not write a record
        post(c, sample("real_02"), log=0, cam=0)
        post(c, sample("real_02"), log=0, cam=0, live=1)
        assert c.get("/api/stats").json()["total"] == 2

        # explaining a different window of a long clip
        x = np.concatenate([sf.read(os.path.join(SAMPLES, f))[0] for f in sorted(os.listdir(SAMPLES))[:4]]).astype("float32")
        buf = io.BytesIO(); sf.write(buf, x, 16000, format="WAV")
        r = c.post("/api/analyze", params={"log": 0, "window": 1},
                   files={"file": ("long.wav", buf.getvalue(), "audio/wav")}).json()
        assert r["n_windows"] > 3 and r["evidence_window"]["index"] == 1


def test_errors():
    with TestClient(app) as c:
        r = c.post("/api/analyze", files={"file": ("x.wav", b"not audio at all", "audio/wav")})
        assert r.status_code == 400 and "decode" in r.json()["detail"].lower()
        buf = io.BytesIO(); sf.write(buf, np.zeros(1000, "float32"), 16000, format="WAV")
        r = c.post("/api/analyze", files={"file": ("short.wav", buf.getvalue(), "audio/wav")})
        assert r.status_code == 400 and "short" in r.json()["detail"].lower()
        assert c.get("/api/model-info").json()["results"]["dual_full"]["n_seeds"] == 2
