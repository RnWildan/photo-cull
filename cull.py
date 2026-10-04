#!/usr/bin/env python3
"""cull.py — deterministic photo culling (focus / sharpness / blur / exposure).

No AI generation. Pure signal processing on the camera's embedded preview
(ARW/CR2/NEF/DNG) or the JPEG itself:
  * variance of Laplacian (VoL) + Tenengrad, per 3x3 grid cell -> subject-region focus
  * FFT high-frequency energy ratio
  * directional gradient ratio (motion-blur anisotropy)
  * exposure stats (mean luma, clipped highlights / crushed shadows)
  * dHash perceptual hash -> burst clustering
Relative ranking INSIDE a burst is what decides keepers (absolute VoL is
scene-dependent and meaningless across different subjects).

Usage:
  python3 cull.py DIR [DIR...] [--out report.json] [--csv report.csv]
                       [--sheet sheet.jpg] [--gap 3.0] [--workers 4]
                       [--recursive] [--min-focus-ratio 0.35] [--full]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, asdict, field

import numpy as np
import cv2
from PIL import Image

RAW_EXT = {".arw", ".cr2", ".cr3", ".nef", ".dng", ".raf", ".orf", ".rw2", ".pef", ".srw"}
JPG_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


# ---------------------------------------------------------------- preview IO
def _largest_embedded_jpeg(data: bytes) -> bytes | None:
    best = None
    i = 0
    while True:
        s = data.find(b"\xff\xd8\xff", i)
        if s < 0:
            break
        e = data.find(b"\xff\xd9", s + 3)
        if e < 0:
            break
        if best is None or (e - s) > len(best):
            best = data[s:e + 2]
        i = e + 2
    return best


def load_preview(path: str, read_bytes: int = 8_000_000):
    """Return (rgb uint8 array, source_tag). Uses embedded preview for RAW."""
    ext = os.path.splitext(path)[1].lower()
    if ext in RAW_EXT:
        with open(path, "rb") as f:
            data = f.read(read_bytes)
        jb = _largest_embedded_jpeg(data)
        if jb:
            im = Image.open(io.BytesIO(jb)).convert("RGB")
            return np.asarray(im), "raw-preview"
        return None, "no-preview"
    im = Image.open(path).convert("RGB")
    return np.asarray(im), "file"


# ---------------------------------------------------------------- metrics
def _vol(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_32F, ksize=3).var())


def _tenengrad(gray: np.ndarray) -> float:
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    return float(np.mean(gx * gx + gy * gy))


def _hf_ratio(gray: np.ndarray) -> float:
    """Fraction of spectral energy in the upper frequency band (blur-sensitive)."""
    g = gray - gray.mean()
    h, w = g.shape
    win = np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)
    F = np.abs(np.fft.rfft2(g * win)) ** 2
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.rfftfreq(w)[None, :]
    r = np.sqrt(fy ** 2 + fx ** 2)
    tot = F.sum() + 1e-12
    return float(F[r > 0.18].sum() / tot)


def analyze(img: np.ndarray, grid: int = 3) -> dict:
    h, w = img.shape[:2]
    scale = 1024 / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

    # contrast map so blur score is normalised against local contrast
    cells = []
    ch, cw = h // grid, w // grid
    for gy in range(grid):
        for gx in range(grid):
            c = gray[gy * ch:(gy + 1) * ch, gx * cw:(gx + 1) * cw]
            if c.size == 0:
                continue
            vol = _vol(c)
            lstd = float(c.std())
            cells.append({
                "row": gy, "col": gx,
                "vol": round(vol, 1),
                "tenengrad": round(_tenengrad(c), 1),
                # normalised: sharpness per unit of local contrast (exposure-robust)
                "norm": round(vol / (lstd + 4.0), 2),
                "luma_std": round(lstd, 1),
            })
    by_norm = sorted(cells, key=lambda c: c["norm"], reverse=True)

    gx_s = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy_s = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    ex = float(np.mean(gx_s ** 2)) + 1e-9
    ey = float(np.mean(gy_s ** 2)) + 1e-9
    aniso = round(max(ex, ey) / min(ex, ey), 2)   # ~1 = isotropic; high = directional smear

    luma = gray.astype(np.float32)
    return {
        "w": w, "h": h,
        "vol_full": round(_vol(gray), 1),
        "vol_best": by_norm[0]["vol"] if by_norm else 0.0,
        "norm_best": by_norm[0]["norm"] if by_norm else 0.0,
        "best_cell": f"r{by_norm[0]['row']}c{by_norm[0]['col']}" if by_norm else None,
        "hf_ratio": round(_hf_ratio(gray), 4),
        "aniso": aniso,
        "mean_luma": round(float(luma.mean()), 1),
        "clip_hi_pct": round(float((luma > 250).mean() * 100), 3),
        "clip_lo_pct": round(float((luma < 5).mean() * 100), 3),
    }


def dhash(img: np.ndarray, n: int = 8) -> str:
    g = cv2.cvtColor(cv2.resize(img, (n + 1, n)), cv2.COLOR_RGB2GRAY)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return "".join(str(int(b)) for b in bits)


def file_metrics(path: str) -> dict:
    rec = {"path": path, "name": os.path.basename(path)}
    try:
        st = os.stat(path)
        rec["mtime"] = st.st_mtime
        rec["size"] = st.st_size
        img, tag = load_preview(path)
        if img is None:
            rec["error"] = "no embedded preview"
            return rec
        rec["source"] = tag
        rec.update(analyze(img))
        rec["dhash"] = dhash(img)
        small = cv2.resize(img, (320, int(320 * img.shape[0] / img.shape[1])))
        rec["_thumb"] = small
    except Exception as e:  # keep the run alive; report the real error
        rec["error"] = f"{type(e).__name__}: {e}"
    return rec


# ---------------------------------------------------------------- bursts
def hamming(a: str, b: str) -> int:
    return sum(x != y for x, y in zip(a, b))


class DSU:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def cluster(recs: list[dict], gap: float = 3.0, phash_max: int = 14) -> list[dict]:
    """Union-find over (time gap <= gap) OR (perceptual hash close enough)."""
    recs = [r for r in recs if "error" not in r]
    recs.sort(key=lambda r: r["mtime"])
    n = len(recs)
    d = DSU(n)
    for i in range(1, n):
        if recs[i]["mtime"] - recs[i - 1]["mtime"] <= gap:
            d.union(i - 1, i)
    for i in range(n):
        for j in range(i + 1, min(i + 8, n)):
            if recs[j]["mtime"] - recs[i]["mtime"] > 20:
                break
            if hamming(recs[i]["dhash"], recs[j]["dhash"]) <= phash_max:
                d.union(i, j)
    groups: dict[int, list[dict]] = {}
    for i, r in enumerate(recs):
        groups.setdefault(d.find(i), []).append(r)
    out = []
    for members in groups.values():
        out.append(sorted(members, key=lambda r: r["mtime"]))
    out.sort(key=lambda g: g[0]["mtime"])
    return out


def flag(groups: list[list[dict]], min_ratio: float) -> dict:
    stats = {"total": sum(len(g) for g in groups), "bursts": 0, "keepers": 0,
             "soft_dupes": 0, "exposure_flagged": 0, "unreadable": 0}
    for g in groups:
        if len(g) > 1:
            stats["bursts"] += 1
        best = max(g, key=lambda r: r["norm_best"])
        for r in g:
            r["group_size"] = len(g)
            r["is_winner"] = r is best
            r["focus_ratio"] = round(r["norm_best"] / (best["norm_best"] + 1e-9), 3)
            r["verdict"] = "keep"
            if r is best:
                stats["keepers"] += 1
                if len(g) > 1:
                    r["verdict"] = "burst-winner"
            elif r["focus_ratio"] < min_ratio:
                r["verdict"] = "soft"
                stats["soft_dupes"] += 1
            else:
                r["verdict"] = "alt"
            if r["clip_hi_pct"] > 2.0 or r["mean_luma"] < 60 or r["mean_luma"] > 200:
                r["exposure"] = "flagged"
                stats["exposure_flagged"] += 1
            else:
                r["exposure"] = "ok"
            if r["aniso"] > 2.5 and r["norm_best"] < 0.6 * best["norm_best"]:
                r["blur_type"] = "directional/motion"
            elif r["verdict"] == "soft":
                r["blur_type"] = "defocus"
            else:
                r["blur_type"] = ""
    return stats


def contact_sheet(groups: list[list[dict]], out_path: str, cell: int = 260,
                  thumbs_per_burst: int = 6) -> str:
    rows = []
    for g in groups:
        g = sorted(g, key=lambda r: r["norm_best"], reverse=True)[:thumbs_per_burst]
        if not g:
            continue
        rows.append(g)
    if not rows:
        return ""
    cols = max(len(r) for r in rows)
    H = len(rows) * (cell + 22)
    W = cols * cell
    sheet = np.full((H, W, 3), 24, np.uint8)
    for ri, row in enumerate(rows):
        for ci, r in enumerate(row):
            t = r.get("_thumb")
            if t is None:
                continue
            th = cv2.resize(t, (cell - 8, int((cell - 8) * t.shape[0] / t.shape[1])))
            y = ri * (cell + 22) + 18
            sheet[y:y + th.shape[0], ci * cell + 4:ci * cell + 4 + th.shape[1]] = th
            label = f"{r['norm_best']:.0f} {'KEEP' if r['is_winner'] else r['verdict'][:4]}"
            col = (90, 255, 90) if r["is_winner"] else (200, 200, 200)
            cv2.putText(sheet, label, (ci * cell + 6, y - 5), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, col, 1, cv2.LINE_AA)
    cv2.imwrite(out_path, sheet, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    return out_path


# ---------------------------------------------------------------- main
def collect(paths: list[str], recursive: bool) -> list[str]:
    files = []
    for p in paths:
        if os.path.isfile(p):
            files.append(p)
            continue
        exts = RAW_EXT | JPG_EXT
        if recursive:
            for root, _, names in os.walk(p):
                for n in names:
                    if os.path.splitext(n)[1].lower() in exts:
                        files.append(os.path.join(root, n))
        else:
            for n in sorted(os.listdir(p)):
                fp = os.path.join(p, n)
                if os.path.isfile(fp) and os.path.splitext(n)[1].lower() in exts:
                    files.append(fp)
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--out", default=None)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--gap", type=float, default=3.0)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--recursive", action="store_true")
    ap.add_argument("--min-focus-ratio", type=float, default=0.35)
    args = ap.parse_args()

    files = collect(args.paths, args.recursive)
    print(f"[cull] {len(files)} files", file=sys.stderr)
    recs = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for i, r in enumerate(ex.map(file_metrics, files, chunksize=4), 1):
            recs.append(r)
            if i % 25 == 0:
                print(f"[cull] {i}/{len(files)}", file=sys.stderr)

    bad = [r for r in recs if "error" in r]
    groups = cluster(recs, gap=args.gap)
    stats = flag(groups, args.min_focus_ratio)
    stats["unreadable"] = len(bad)

    if args.out:
        payload = {
            "stats": stats,
            "groups": [[{k: v for k, v in r.items() if not k.startswith("_")} for r in g]
                       for g in groups],
            "unreadable": bad,
        }
        with open(args.out, "w") as f:
            json.dump(payload, f, indent=1)
        print(f"[cull] wrote {args.out}", file=sys.stderr)
    if args.csv:
        import csv
        cols = ["name", "path", "verdict", "focus_ratio", "norm_best", "vol_best",
                "hf_ratio", "aniso", "blur_type", "exposure", "mean_luma",
                "clip_hi_pct", "clip_lo_pct", "group_size", "best_cell"]
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for g in groups:
                for r in g:
                    w.writerow(r)
        print(f"[cull] wrote {args.csv}", file=sys.stderr)
    if args.sheet:
        contact_sheet(groups, args.sheet)

    print(json.dumps(stats, indent=1))


if __name__ == "__main__":
    main()
