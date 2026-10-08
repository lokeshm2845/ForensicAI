"""Plain-English forensic report built from measured model outputs (rule-based, deterministic)."""
from datetime import datetime, timezone


def confidence_band(p):
    d = abs(p - 0.5) * 2  # 0 = coin flip, 1 = certain
    return "very high" if d > 0.9 else "high" if d > 0.7 else "moderate" if d > 0.4 else "low"


def build_report(res, filename):
    """res needs: prob_fake, verdict, region, worst_window, window_probs, window_starts_s, n_windows, duration_s, warnings."""
    p, verdict, reg = res["prob_fake"], res["verdict"], res["region"]
    wp = res["window_probs"]
    k = res["worst_window"]
    f_lo, f_hi = reg["freq_hz"]
    t_lo, t_hi = reg["time_s"]
    t0 = res["window_starts_s"][k]
    n_flag = sum(1 for v in wp if v > 0.5)

    sections = []
    if verdict == "FAKE":
        sections.append(("Summary", "The recording shows characteristics typical of synthetic or converted speech."))
        sections.append(("Evidence",
                         f"The spectrogram expert concentrated its attention on {f_lo:,.0f}–{f_hi:,.0f} Hz between "
                         f"{t0 + t_lo:.2f} s and {t0 + t_hi:.2f} s (window {k + 1}, P[fake] = {wp[k]:.1%}). "
                         "This is the region that pushed the decision toward FAKE (see the Grad-CAM view)."))
    else:
        sections.append(("Summary", "No dominant synthetic-speech artefacts were found; the clip is consistent with genuine speech."))
        sections.append(("Evidence",
                         f"The strongest (still sub-threshold) attention was around {f_lo:,.0f}–{f_hi:,.0f} Hz at "
                         f"{t0 + t_lo:.2f}–{t0 + t_hi:.2f} s, with a maximum window score of {max(wp):.1%}."))
    if res["n_windows"] > 1:
        spread = max(wp) - min(wp)
        extra = ("Scores are stable across the clip." if spread < 0.3 else
                 "Scores vary strongly between windows: possible partial manipulation – review the flagged windows.")
        sections.append(("Consistency", f"{n_flag} of {res['n_windows']} analysis windows were flagged as fake "
                                         f"(window scores {min(wp):.1%}–{max(wp):.1%}). {extra}"))
    if res.get("warnings"):
        sections.append(("Input warnings", " ".join(res["warnings"])))
    sections.append(("Limitations",
                     "Screening aid, not conclusive proof. Accuracy drops on voice generators, microphones and codecs not "
                     "represented in training (some voice-conversion attacks were missed with high confidence in our tests). "
                     "The Grad-CAM map is coarse (about 2 kHz × 0.5 s cells). Confirm high-stakes findings with a human "
                     "forensic examiner."))

    head = (f"FORENSIC AUDIO AUTHENTICITY REPORT\nFile: {filename}\n"
            f"Generated: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC | Length: {res['duration_s']:.1f} s | "
            f"{res['n_windows']} analysis window{'s' if res['n_windows'] != 1 else ''} of 4 s\n\n"
            f"VERDICT: {verdict} (P[fake] = {p:.1%}, decision confidence: {confidence_band(p)})\n")
    text = head + "\n" + "\n\n".join(f"{t}: {b}" for t, b in sections)
    return {"text": text, "sections": [{"title": t, "body": b} for t, b in sections],
            "confidence": confidence_band(p)}
