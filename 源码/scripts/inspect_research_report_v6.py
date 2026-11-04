"""Create QA contact sheets from the already rendered report pages."""
from pathlib import Path
import json
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[1] / ".tmp" / "qa" / "research-report-v6"
counts = {item['type']: item['pages'] for item in json.loads((root / 'results.json').read_text(encoding='utf-8'))}
for kind in ("paper", "project", "guideline"):
    pages = [root / kind / f'page-{number:02d}.png' for number in range(1, counts[kind] + 1)]
    for start in range(0, len(pages), 12):
        sheet = Image.new("RGB", (1040, 1120), "#dde2e8")
        draw = ImageDraw.Draw(sheet)
        for offset, path in enumerate(pages[start:start+12]):
            image = Image.open(path).convert("RGB")
            image.thumbnail((245, 345))
            x, y = (offset % 4) * 260 + 8, (offset // 4) * 373 + 20
            sheet.paste(image, (x, y))
            draw.text((x, y - 16), f"{kind} / page {start+offset+1}", fill="black")
        sheet.save(root / f"{kind}-sheet-{start//12+1}.png")
