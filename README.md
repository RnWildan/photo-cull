# Photo Cull

A local web app for deterministic photo culling. It scores focus, sharpness,
blur, and exposure, groups a shoot into bursts, and flags the keepers. Built
for RAW photography workflows (Sony ARW and 19 other RAW formats) but works on
JPEGs too.

Everything runs locally. No AI generation, no cloud, no photos leave your
machine.

## What it does

Point it at a folder of photos and it:

- Scores sharpness per image, using variance-of-Laplacian and Tenengrad,
  normalized by local contrast so the score is exposure-robust
- Detects blur type with an FFT high-frequency energy ratio and a directional
  gradient anisotropy measure (tells defocus from motion blur)
- Groups bursts with union-find over time gap and dHash perceptual distance
- Flags keepers by ranking within each burst, marking the winner and flagging
  soft duplicates and exposure problems
- Shows a contact sheet and a filterable results grid in the browser

## How it works

RAW files are not demosaiced here. The tool reads the largest embedded JPEG
preview straight out of the file, in pure Python, with no libraw or exiftool
dependency. That is enough for focus ranking. For final output you would use a
real demosaic like darktable.

The key point is that absolute sharpness is meaningless across scenes, so the
tool ranks within a burst. `norm_best` (variance-of-Laplacian normalized by
local luma std) decides the keepers, and `focus_ratio` is the image score
divided by the burst best.

## Run it

```bash
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000
```

Then open <http://127.0.0.1:8000>, enter a folder path, and hit Cull.

## Project layout

- `app.py` is the FastAPI backend (scan, cull, serve thumbnails and contact sheet)
- `cull.py` is the culling engine (metrics, burst clustering, verdicts)
- `static/` is the browser UI (single page, vanilla JS)

## API

- `POST /api/cull` takes `{path, recursive, gap, min_focus_ratio}` and returns stats, groups, thumb URLs, and a contact sheet
- `GET /api/thumb/{run_id}/{name}` serves a cached thumbnail or contact sheet
- `GET /api/health` is a health check

## Validation

The metrics were validated against known synthetic degradations. `norm_best`
and `hf_ratio` drop monotonically with defocus, and motion blur raises the
anisotropy metric while isotropic defocus does not. See the original
`validate.py` harness in the photo-culling skill.

## License

MIT
