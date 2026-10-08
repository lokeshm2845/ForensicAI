"""Dual-branch hybrid CNN-Transformer audio deepfake detector.

Spatial branch : Mel-spectrogram -> EfficientNet-B0 (ImageNet pre-trained) -> 256-d
Temporal branch: raw waveform    -> 1D conv stem -> Transformer encoder (4 layers, 8 heads) -> 256-d
Fusion         : concatenation -> Dense -> Sigmoid  (probability that the clip is FAKE)
"""
import math

import torch
import torch.nn as nn
import torch.nn.functional as F
import torchaudio
from torchvision.models import efficientnet_b0, EfficientNet_B0_Weights

SR = 16000


class MelFrontEnd(nn.Module):
    def __init__(self, n_mels=128, n_fft=1024, hop=256, specaug=False,
                 time_mask=40, freq_mask=24, n_masks=2):
        super().__init__()
        self.mel = torchaudio.transforms.MelSpectrogram(
            SR, n_fft=n_fft, hop_length=hop, n_mels=n_mels, f_min=20, f_max=SR // 2, power=2.0)
        self.to_db = torchaudio.transforms.AmplitudeToDB(stype="power", top_db=80)
        self.specaug = specaug
        self.tmask = torchaudio.transforms.TimeMasking(time_mask)
        self.fmask = torchaudio.transforms.FrequencyMasking(freq_mask)
        self.n_masks = n_masks

    def forward(self, wave):  # (B, T)
        with torch.autocast(device_type=wave.device.type, enabled=False):
            m = self.to_db(self.mel(wave.float()))  # (B, n_mels, frames)
        m = (m - m.mean(dim=(1, 2), keepdim=True)) / (m.std(dim=(1, 2), keepdim=True) + 1e-5)
        if self.training and self.specaug:
            for _ in range(self.n_masks):
                m = self.fmask(m)
                m = self.tmask(m)
        return m


class SpatialBranch(nn.Module):
    def __init__(self, out_dim=256, pretrained=True, dropout=0.3):
        super().__init__()
        w = EfficientNet_B0_Weights.IMAGENET1K_V1 if pretrained else None
        net = efficientnet_b0(weights=w)
        self.features = net.features  # last conv block -> Grad-CAM target
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.proj = nn.Sequential(nn.Dropout(dropout), nn.Linear(1280, out_dim), nn.GELU())
        self.register_buffer("mean", torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1))
        self.register_buffer("std", torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1))

    def forward(self, spec, return_maps=False):  # spec: (B, n_mels, frames)
        x = spec.unsqueeze(1).repeat(1, 3, 1, 1)
        fmap = self.features(x)
        z = self.proj(self.pool(fmap).flatten(1))
        return (z, fmap) if return_maps else z


class PositionalEncoding(nn.Module):
    def __init__(self, d_model, max_len=2048):
        super().__init__()
        pe = torch.zeros(max_len, d_model)
        pos = torch.arange(max_len).unsqueeze(1)
        div = torch.exp(torch.arange(0, d_model, 2) * (-math.log(10000.0) / d_model))
        pe[:, 0::2], pe[:, 1::2] = torch.sin(pos * div), torch.cos(pos * div)
        self.register_buffer("pe", pe.unsqueeze(0))

    def forward(self, x):
        return x + self.pe[:, : x.size(1)]


class TemporalBranch(nn.Module):
    """Strided 1D-conv stem (64,000 samples -> 250 tokens) + Transformer encoder."""

    def __init__(self, out_dim=256, d_model=128, heads=8, layers=4, ff=256, dropout=0.3):
        super().__init__()
        self.stem = nn.Sequential(
            nn.Conv1d(1, 32, 64, stride=8, padding=28), nn.BatchNorm1d(32), nn.GELU(),
            nn.Conv1d(32, 64, 9, stride=4, padding=4), nn.BatchNorm1d(64), nn.GELU(),
            nn.Conv1d(64, d_model, 5, stride=4, padding=2), nn.BatchNorm1d(d_model), nn.GELU(),
            nn.Conv1d(d_model, d_model, 3, stride=2, padding=1), nn.BatchNorm1d(d_model), nn.GELU(),
        )
        self.pos = PositionalEncoding(d_model)
        enc = nn.TransformerEncoderLayer(d_model, heads, ff, dropout / 3, batch_first=True, norm_first=True)
        self.encoder = nn.TransformerEncoder(enc, layers)
        self.norm = nn.LayerNorm(d_model)
        self.proj = nn.Sequential(nn.Dropout(dropout), nn.Linear(2 * d_model, out_dim), nn.GELU())

    def forward(self, wave):  # (B, T)
        x = wave / (wave.abs().amax(dim=1, keepdim=True) + 1e-6)
        x = self.stem(x.unsqueeze(1)).transpose(1, 2)  # (B, tokens, d)
        x = self.norm(self.encoder(self.pos(x)))
        return self.proj(torch.cat([x.mean(1), x.amax(1)], dim=1))


class DualBranchDetector(nn.Module):
    def __init__(self, mode="dual", specaug=False, pretrained=True, dropout=0.3):
        super().__init__()
        assert mode in ("dual", "cnn", "transformer")
        self.mode = mode
        self.front = MelFrontEnd(specaug=specaug)
        self.spatial = SpatialBranch(pretrained=pretrained, dropout=dropout) if mode != "transformer" else None
        self.temporal = TemporalBranch(dropout=dropout) if mode != "cnn" else None
        fused = 256 * ((self.spatial is not None) + (self.temporal is not None))
        self.head = nn.Sequential(nn.Dropout(dropout), nn.Linear(fused, 128), nn.GELU(),
                                  nn.Dropout(dropout), nn.Linear(128, 1))

    def forward(self, wave):
        feats = []
        if self.spatial is not None:
            feats.append(self.spatial(self.front(wave)))
        if self.temporal is not None:
            feats.append(self.temporal(wave))
        return self.head(torch.cat(feats, dim=1)).squeeze(1)  # logit; sigmoid -> P(fake)

    def n_params(self):
        return sum(p.numel() for p in self.parameters())
