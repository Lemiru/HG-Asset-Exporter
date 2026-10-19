"""Tile PNG images into a grid: python tools/tile.py OUT.png COLUMNS IMG1.png IMG2.png ..."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hgexport.util.png import decode_png, encode_png  # noqa: E402

out, cols, files = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
images = [decode_png(Path(f).read_bytes()) for f in files]
w = max(i[0] for i in images)
h = max(i[1] for i in images)
rows = (len(images) + cols - 1) // cols
canvas = bytearray(w * cols * h * rows * 4)
for k, (iw, ih, px) in enumerate(images):
    ox, oy = (k % cols) * w, (k // cols) * h
    for y in range(ih):
        start = ((oy + y) * w * cols + ox) * 4
        canvas[start:start + iw * 4] = px[y * iw * 4:(y + 1) * iw * 4]
Path(out).write_bytes(encode_png(w * cols, h * rows, bytes(canvas)))
