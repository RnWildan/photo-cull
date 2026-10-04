# Photo Cull

A local web app for **deterministic photo culling** — focus, sharpness, blur,
and exposure analysis that ranks a shoot and flags keepers. Built for RAW
photography workflows (Sony ARW and 19 other RAW formats) but works on JPEGs
too.

Everything runs **locally** — no AI generation, no cloud, no photos leave your
machine.

## What it does

Point it at a folder of photos and it:

- **Scores sharpness** per image — variance-of-Laplacian + Tenengrad, normalized
  by local contrast so it's exposure-robust
- **Detects blur type** — FFT high-frequency energy ratio + directional
  gradient anisotropy (tells defocus from motion blur)
- **Groups bursts** — union-find over time-gap AND dHash perceptual distance
- **Flags keepers** — ranks within each burst, marks the winner, flags soft
  duplicates and exposure problems
- **Shows a contact sheet** + a filterable results grid in the browser

## How it works

RAW files have no demosaic step here — it reads the **largest embedded JPEG
preview** straight out of the file (pure Python, no libraw/exiftool needed).
That's plenty for focus ranking. For final output you'd use a real demosaic
(darktable, etc.).

The key insight: **absolute sharpness is meaningless across scenes**, so it
ranks *within* a burst. `norm_best` (variance-of-Laplacian normalized by local
luma std) is what decides keepers; `focus_ratio` = image / burst-best.

## Run it

```bash
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000
```

Then open <http://127.0.0.1:8000>, enter a folder path, and hit **Cull**.

## Project layout

- `app.py` — FastAPI backend (scan, cull, serve thumbnails + contact sheet)
- `cull.py` — the culling engine (metrics, burst clustering, verdicts)
- `static/` — browser UI (single-page, vanilla JS)

## API

- `POST /api/cull` — `{path, recursive, gap, min_focus_ratio}` → stats, groups, thumb URLs, contact sheet
- `GET /api/thumb/{run_id}/{name}` — cached thumbnail / contact sheet
- `GET /api/health` — health check

## Validation

The metrics were validated against known synthetic degradations — `norm_best`
and `hf_ratio` drop monotonically with defocus, and motion blur raises the
anisotropy metric while isotropic defocus does not. See the original
`validate.py` harness in the photo-culling skill.

## License

MIT
