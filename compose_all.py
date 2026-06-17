# -*- coding: utf-8 -*-
"""Assemble a single 4x16 sheet from A/B/C/D.
- Rows: 4 per sheet, split at the 3 widest vertical gaps (reliable with narrow slits).
- Columns: even division by the KNOWN number of frames per row.
- Background (magenta + dark border + grid) -> transparent; the cat and props (hearts,
  yarn ball, mouse, Z, dust) are kept. Each frame is normalized by height.
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
MAGENTA = (237, 48, 238)
TARGET_H = 200          # normalized frame height (props included)
GAP = 46
MARGIN = 26

# state, sheet, row, ncols(known), [column indices], target_h override
PLAN = [
    ("SIT",       "A", 0, 8, [0, 2, 4, 6], 200),
    ("WALK",      "A", 1, 4, [0, 1, 2, 3], 200),
    ("RUN",       "A", 2, 4, [0, 1, 2, 3], 200),
    ("FALL",      "A", 3, 4, [0, 1, 2, 3], 200),
    ("HELD",      "B", 0, 5, [0, 1, 2, 4], 220),
    ("LAND",      "B", 1, 5, [0, 1, 2, 3], 200),
    ("SLEEP",     "B", 2, 5, [0, 1, 2, 3], 170),
    ("STRETCH",   "B", 3, 5, [0, 1, 2, 3], 200),
    ("GROOM",     "C", 0, 7, [0, 2, 4, 6], 200),
    ("PETTED",    "C", 1, 7, [0, 2, 4, 6], 200),
    ("LOVE",      "C", 2, 7, [0, 2, 4, 6], 235),
    ("SULK",      "C", 3, 7, [0, 2, 4, 6], 200),
    ("SURPRISED", "D", 0, 4, [0, 1, 2, 3], 240),
    # NOTE: D.png PLAY frames 1 & 3 are doubled (the generator drew TWO cats),
    # so we use only the clean single-cat frames 0 & 2 (alternated for a 4-frame loop).
    ("PLAY",      "D", 1, 4, [0, 2, 0, 2], 200),
    ("HUNT",      "D", 2, 4, [0, 1, 2, 3], 200),
    ("MEOW",      "D", 3, 4, [0, 1, 2, 3], 200),
]


def is_bg(r, g, b):
    if r < 55 and g < 55 and b < 75:          # dark border/slit
        return True
    if r > 90 and b > 90 and g < min(r, b) * 0.6:   # magenta/grid
        return True
    return False


def warm(p):
    r, g, b = p[0], p[1], p[2]
    return r > 60 and b < r - 25


def row_edges(im):
    """4 rows: the 3 widest INNER gaps based on the profile of 'warm' rows."""
    W, H = im.size
    px = im.load()
    prof = [sum(1 for x in range(0, W, 3) if warm(px[x, y])) for y in range(H)]
    mx = max(prof) or 1
    thr = mx * 0.05
    gaps, s = [], -1
    for y in range(H):
        low = prof[y] <= thr
        if low and s < 0:
            s = y
        elif not low and s >= 0:
            gaps.append((s, y-1)); s = -1
    if s >= 0:
        gaps.append((s, H-1))
    inner = [g for g in gaps if g[0] > 0 and g[1] < H-1]
    inner.sort(key=lambda g: g[1]-g[0], reverse=True)
    seps = sorted(inner[:3])
    edges = [0] + [(a+b)//2 for a, b in seps] + [H]
    return edges


def extract_frame(im, x0, y0, x1, y1):
    """Crop the cell -> RGBA (transparent background) -> trim to bbox."""
    cell = im.crop((x0, y0, x1, y1)).convert("RGBA")
    px = cell.load()
    w, h = cell.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a <= 16 or is_bg(r, g, b):
                px[x, y] = (0, 0, 0, 0)
    bb = cell.getbbox()
    return cell.crop(bb) if bb else cell


# --- Extract all frames according to the plan ---
sheets = {t: Image.open(os.path.join(ASSETS, t + ".png")).convert("RGBA")
          for t in ["A", "B", "C", "D"]}
edges = {t: row_edges(sheets[t]) for t in sheets}
print("row edges:", {t: edges[t] for t in sheets})

rows_frames = []   # list of rows; each is a list of 4 RGBA frames (scaled)
INSET = 0.06       # side padding within the cell (fraction), to avoid catching a neighbor
for state, tag, r, ncols, picks, th in PLAN:
    im = sheets[tag]
    W, H = im.size
    y0, y1 = edges[tag][r], edges[tag][r+1]
    frames = []
    for c in picks:
        cx0 = round((c + INSET) * W / ncols)
        cx1 = round((c + 1 - INSET) * W / ncols)
        fr = extract_frame(im, cx0, y0, cx1, y1)
        if fr.height > 1:
            k = th / fr.height
            fr = fr.resize((max(1, round(fr.width*k)), max(1, round(fr.height*k))),
                           Image.LANCZOS)
        frames.append(fr)
    rows_frames.append((state, frames))
    print(f"  {state}: {tag} r{r} y={y0}..{y1} -> {len(frames)} frames, h~{th}")

# --- Assemble the 4 x 16 sheet on magenta ---
cellW = max(fr.width for _, fs in rows_frames for fr in fs) + 28
rowH = max(fr.height for _, fs in rows_frames for fr in fs) + 16
COLS = 4
Wf = cellW * COLS
Hf = MARGIN*2 + rowH*len(rows_frames) + GAP*(len(rows_frames)-1)
canvas = Image.new("RGBA", (Wf, Hf), MAGENTA + (255,))
y = MARGIN
for state, fs in rows_frames:
    for c in range(COLS):
        fr = fs[c % len(fs)]
        cx = c*cellW + (cellW - fr.width)//2
        cy = y + (rowH - fr.height)            # bottom alignment (feet)
        canvas.alpha_composite(fr, (cx, cy))
    y += rowH + GAP

OUT = os.path.join(ASSETS, "cat_sheet.png")
canvas.convert("RGB").save(OUT)
print(f"\nSaved {OUT}  size={Wf}x{Hf}  rows={len(rows_frames)}")
print("Order:", [s for s, _ in rows_frames])
