"""Model engine: decode audio, score 4 s windows, explain with Grad-CAM, return UI-ready (quantised) arrays."""
import base64
import io
import json
import os
import threading
import time

import numpy as np
import soundfile as sf
import torch
import torchaudio

from gradcam import GradCAM, hot_region, scale_cam
from model import DualBranchDetector
from report import build_report

SR = 16000
N = 4 * SR
MAX_SECONDS = 60
SPEC_LO, SPEC_HI = -2.0, 4.0  # quantisation range of the standardised log-mel (z-score)


class AudioError(ValueError):
    pass


def _b64(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a, dtype=np.uint8).tobytes()).decode("ascii")


class Engine:
    def __init__(self, ckpt, cam_ref_path, device=None):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        if self.device == "cpu":
            torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
        self.model = DualBranchDetector("dual", pretrained=False).to(self.device)
        self.model.load_state_dict(torch.load(ckpt, map_location=self.device))
        self.model.eval()
        self.cam = GradCAM(self.model)
        self.cam_ref = float(json.load(open(cam_ref_path))["ref"])
        self.lock = threading.Lock()  # one forward/backward at a time (Grad-CAM mutates .grad)
        self.params_m = sum(p.numel() for p in self.model.parameters()) / 1e6
        self.warm_up()

    # ------------------------------------------------------------------ audio
    def decode(self, raw: bytes):
        """bytes (wav/flac/ogg/mp3) -> (16 kHz mono float32, info dict). Raises AudioError."""
        try:
            data, sr = sf.read(io.BytesIO(raw), dtype="float32", always_2d=True)
        except Exception as e:  # unsupported / corrupt
            raise AudioError("Could not decode this file. Use WAV, FLAC, OGG or MP3.") from e
        if data.shape[0] == 0:
            raise AudioError("The file contains no audio.")
        x = torch.from_numpy(data.mean(axis=1))
        if sr != SR:
            x = torchaudio.functional.resample(x, sr, SR)
        x = x.numpy().astype(np.float32)
        info = {"orig_sr": int(sr), "truncated": False}
        if len(x) > MAX_SECONDS * SR:
            x, info["truncated"] = x[: MAX_SECONDS * SR], True
        if len(x) < SR // 2:
            raise AudioError("Audio is too short - need at least 0.5 seconds.")
        return x, info

    @staticmethod
    def make_windows(x, hop_s=2.0):
        """4 s windows with 50 % overlap; clips shorter than 4 s are repeat-padded (as in training)."""
        if len(x) <= N:
            return [np.tile(x, int(np.ceil(N / len(x))))[:N]], [0.0]
        hop = int(hop_s * SR)
        starts = list(range(0, len(x) - N + 1, hop))
        if starts[-1] != len(x) - N:
            starts.append(len(x) - N)
        return [x[s:s + N] for s in starts], [s / SR for s in starts]

    # ------------------------------------------------------------------ model
    def _probs(self, wins):
        out = []
        with torch.inference_mode():
            for i in range(0, len(wins), 16):
                t = torch.from_numpy(np.stack(wins[i:i + 16])).to(self.device)
                with torch.autocast(self.device, dtype=torch.bfloat16, enabled=self.device == "cuda"):
                    out.append(torch.sigmoid(self.model(t).float()).cpu())
        return torch.cat(out).numpy()

    def warm_up(self):
        with self.lock:
            self._probs([np.zeros(N, np.float32)])

    def analyse(self, x, info, filename="audio", want_cam=True, window=None):
        t_all = time.perf_counter()
        wins, starts = self.make_windows(x)
        with self.lock:
            t0 = time.perf_counter()
            probs = self._probs(wins)
            t_probs = time.perf_counter() - t0
            k = int(np.argmax(probs)) if window is None else int(np.clip(window, 0, len(wins) - 1))
            spec = cam = None
            t_cam = 0.0
            if want_cam:
                t0 = time.perf_counter()
                with torch.enable_grad():
                    _, spec, cam = self.cam(torch.from_numpy(wins[k]).unsqueeze(0).to(self.device))
                cam = scale_cam(cam, self.cam_ref)
                t_cam = time.perf_counter() - t0
        clip_p = float(np.mean(probs))
        verdict = "FAKE" if clip_p > 0.5 else "REAL"

        warnings = []
        if info.get("truncated"):
            warnings.append(f"Only the first {MAX_SECONDS} s were analysed.")
        if len(x) < N:
            warnings.append("Clip is shorter than 4 s and was repeat-padded; short clips are less reliable.")
        if info.get("orig_sr", SR) < SR:
            warnings.append(f"Source sampling rate is {info['orig_sr'] / 1000:.1f} kHz (band-limited); reliability is reduced.")
        rms = float(np.sqrt(np.mean(x ** 2)))
        if rms < 1e-3:
            warnings.append("Audio is almost silent; the verdict is not meaningful.")
        if float(np.max(np.abs(x))) >= 0.999:
            warnings.append("Audio is clipped (saturated), which can distort the evidence.")

        res = {
            "filename": filename, "duration_s": len(x) / SR, "verdict": verdict, "prob_fake": clip_p,
            "max_prob": float(probs.max()), "n_windows": len(wins), "n_flagged": int((probs > 0.5).sum()),
            "window_probs": [float(v) for v in probs], "window_starts_s": [float(s) for s in starts],
            "worst_window": k, "warnings": warnings, "orig_sr": info.get("orig_sr", SR),
        }
        # waveform envelope of the whole clip (min/max per bin) for plotting
        bins = 900
        edges = np.linspace(0, len(x), bins + 1).astype(int)
        res["waveform"] = {"min": [round(float(x[a:b].min()), 3) for a, b in zip(edges[:-1], edges[1:])],
                           "max": [round(float(x[a:b].max()), 3) for a, b in zip(edges[:-1], edges[1:])]}
        res["region"] = {"freq_hz": [0, 8000], "time_s": [0.0, 4.0], "coverage": 0.0}
        if want_cam:
            h, w = spec.shape
            q = np.clip((spec - SPEC_LO) / (SPEC_HI - SPEC_LO), 0, 1) * 255
            res["spec"] = {"w": w, "h": h, "data": _b64(q), "lo": SPEC_LO, "hi": SPEC_HI}
            res["cam"] = {"w": w, "h": h, "data": _b64(np.clip(cam, 0, 1) * 255), "peak": float(cam.max())}
            res["region"] = hot_region(cam)
            low, mid, high = self._band_shares(cam)
            res["bands"] = {"low": low, "mid": mid, "high": high, "mean_activation": float(cam.mean())}
            res["evidence_window"] = {"index": k, "start_s": starts[k], "end_s": starts[k] + 4.0}
        rep = build_report(res, filename)
        res["report"] = rep
        res["confidence"] = rep["confidence"]
        res["timings_ms"] = {"inference": round(t_probs * 1000, 1), "gradcam": round(t_cam * 1000, 1),
                             "total": round((time.perf_counter() - t_all) * 1000, 1)}
        res["latency_ms"] = res["timings_ms"]["total"]
        return res

    @staticmethod
    def _band_shares(cam, n_mels=128, fmax=8000):
        hz_to_mel = lambda f: 2595 * np.log10(1 + f / 700)
        m_lo, m_hi = hz_to_mel(20), hz_to_mel(fmax)
        idx = np.arange(n_mels)
        hz = 700 * (10 ** ((m_lo + (m_hi - m_lo) * (idx + 1) / (n_mels + 1)) / 2595) - 1)
        tot = float(cam.sum())
        if tot <= 1e-8:
            return 0.0, 0.0, 0.0
        row = cam.sum(axis=1)
        return (float(row[hz < 2700].sum() / tot), float(row[(hz >= 2700) & (hz < 5500)].sum() / tot),
                float(row[hz >= 5500].sum() / tot))
