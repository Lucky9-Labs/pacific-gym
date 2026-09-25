"""Build a compact local contact sheet from the verified Blender renders."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


root = Path(__file__).resolve().parent.parent
proof = root / "proof/slice-03"
views = ("negative-y", "positive-x", "three-quarter")
roles = (("visual", "STATIC VISUAL LOD0"), ("rig", "UNANIMATED 62-JOINT RIG"))
tile, label = 390, 38
sheet = Image.new("RGB", (tile * 3, (tile + label) * 2 + 90), "#20242b")
draw = ImageDraw.Draw(sheet)
font = ImageFont.load_default(size=20)
small = ImageFont.load_default(size=16)
draw.text((24, 17), "STROKAH / FLUX 3 GAIT PREFLIGHT", fill="white", font=font)
draw.text((24, 48), "Blender source renders only - no generated video", fill="#efbf74", font=small)
for row, (role, title) in enumerate(roles):
    for col, view in enumerate(views):
        image = Image.open(proof / "renders" / role / f"{view}.png").convert("RGB")
        image.thumbnail((tile, tile))
        x, y = col * tile, row * (tile + label) + 90
        sheet.paste(image, (x, y))
        draw.text((x + 10, y + tile + 7), f"{title} / {view}", fill="white", font=small)
sheet.save(proof / "source-contact-sheet.png", optimize=True)
print("FLUX3_PROOF_SHEET_PASS")
