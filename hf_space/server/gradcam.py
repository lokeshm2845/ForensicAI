"""Grad-CAM on the EfficientNet-B0 spatial branch: which time/frequency region pushed the verdict toward FAKE."""
import numpy as np
import torch
import torch.nn.functional as F


class GradCAM:
    def __init__(self, model):
        assert model.spatial is not None, "Grad-CAM needs the spatial (EfficientNet) branch"
        self.model = model

    def __call__(self, wave):
        """wave: (1, T) float tensor on the model's device -> (prob_fake, spec[F,T], raw cam[F,T] >= 0)."""
        m = self.model
        m.eval()
        wave = wave.clone()
        spec = m.front(wave)
        z, fmap = m.spatial(spec, return_maps=True)
        fmap.retain_grad()
        feats = [z]
        if m.temporal is not None:
            feats.append(m.temporal(wave))
        logit = m.head(torch.cat(feats, dim=1)).squeeze(1)
        m.zero_grad(set_to_none=True)
        logit.sum().backward()
        weights = fmap.grad.mean(dim=(2, 3), keepdim=True)  # global-average-pooled gradients
        cam = F.relu((weights * fmap).sum(dim=1, keepdim=True))  # evidence FOR "fake"
        cam = F.interpolate(cam, size=spec.shape[-2:], mode="bilinear", align_corners=False)[0, 0]
        return float(torch.sigmoid(logit).item()), spec[0].detach().cpu().numpy(), cam.detach().cpu().numpy()  # raw CAM


def hot_region(cam, sr=16000, hop=256, n_mels=128, fmax=8000, thresh=0.6):
    """Summarise the CAM: dominant frequency band (Hz) and time span (s) above `thresh`."""
    mask = cam >= thresh
    if not mask.any():
        mask = cam >= cam.max() * 0.9
    fs, ts = np.where(mask)
    hz_to_mel = lambda f: 2595 * np.log10(1 + f / 700)  # HTK scale = torchaudio default
    m_lo, m_hi = hz_to_mel(20), hz_to_mel(fmax)
    mel_to_hz = lambda i: 700 * (10 ** ((m_lo + (m_hi - m_lo) * (i + 1) / (n_mels + 1)) / 2595) - 1)
    f_lo, f_hi = mel_to_hz(fs.min() - 1), mel_to_hz(fs.max())
    return {"freq_hz": (float(f_lo), float(f_hi)), "time_s": (float(ts.min() * hop / sr), float((ts.max() + 1) * hop / sr)),
            "coverage": float(mask.mean())}


def scale_cam(cam, ref):
    """Show CAM on a FIXED scale (ref = typical peak of a correctly detected fake) so weak evidence looks weak."""
    return np.clip(cam / ref, 0.0, 1.0)
