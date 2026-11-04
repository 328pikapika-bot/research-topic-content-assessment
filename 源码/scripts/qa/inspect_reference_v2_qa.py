"""Read-only PDF checks and contact sheets of the isolated synthetic sample."""
from pathlib import Path
import re

import fitz
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[2] / ".tmp/qa/report-reference-v2"
compact = lambda value: re.sub(r"\s+", "", value)
with fitz.open(root / "report.pdf") as pdf:
    for _, title, page in pdf.get_toc():
        assert 1 <= page <= len(pdf), (title, page)
        assert compact(title) in compact(pdf[page - 1].get_text()), (title, page)
    for start in range(0, len(pdf), 6):
        sheet = Image.new("RGB", (1800, 2610), "#dce2eb")
        for offset, page_index in enumerate(range(start, min(start + 6, len(pdf)))):
            with Image.open(root / f"page-{page_index+1:02d}.png") as source:
                tile = source.copy()
            tile.thumbnail((880, 1240))
            x, y = (offset % 2) * 900, (offset // 2) * 870
            # Two columns of three pages; fit each page without clipping.
            tile.thumbnail((590, 835))
            sheet.paste(tile, (x + 155, y + 25))
            ImageDraw.Draw(sheet).text((x + 20, y + 5), f"PAGE {page_index+1}", fill="#1e3a8a")
        sheet.save(root / f"contact-{start//6+1:02d}.png")
    print(f"Verified {len(pdf.get_toc())} bookmark destinations and {len(pdf)} rendered pages.")
