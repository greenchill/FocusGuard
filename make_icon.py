# -*- coding: utf-8 -*-
"""Generate app_icon.ico from the cat's SIT frame (chroma-keyed, squared, multi-size)."""
import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SHEET = os.path.join(HERE, "assets", "cat_sheet.png")
MAGENTA = (237, 48, 238)

im = Image.open(SHEET).convert("RGBA")
W, H = im.size
# SIT is row 0, col 0 of the 4x16 grid. Crop that cell with a little inset.
cell_w, row0_h = W // 4, 334            # first row band ends ~334 (engine auto-rows)
cell = im.crop((6, 6, cell_w - 6, row0_h - 6))

# Chroma-key the magenta background -> transparent.
px = cell.load()
w, h = cell.size
for y in range(h):
    for x in range(w):
        r, g, b, a = px[x, y]
        dr, dg, db = r - MAGENTA[0], g - MAGENTA[1], b - MAGENTA[2]
        if dr*dr + dg*dg + db*db < 90*90:
            px[x, y] = (0, 0, 0, 0)

bb = cell.getbbox()
if bb:
    cell = cell.crop(bb)

# Square it on a transparent canvas, then render to 256 and save a multi-size .ico.
s = max(cell.size) + 16
canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))
canvas.paste(cell, ((s - cell.width) // 2, (s - cell.height) // 2), cell)
canvas = canvas.resize((256, 256), Image.LANCZOS)
out = os.path.join(HERE, "app_icon.ico")
canvas.save(out, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("saved", out, "from cat SIT frame", cell.size)
