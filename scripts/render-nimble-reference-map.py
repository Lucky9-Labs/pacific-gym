#!/usr/bin/env python3
"""Render a compact, inspectable PNG source map from a Nimble receipt."""

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


GROUPS = [
    ("FLUX - visual direction", "FLUX prompt", "#89edcc",
     "Artist and animation leads shape visual language; physics stays out of FLUX."),
    ("BLENDER - rig authoring", "Blender authoring", "#ffc879",
     "Rigify and IK references guide setup only; inspect the actual rig separately."),
    ("ISAAC SIM - physics handoff", "Isaac Sim handoff", "#93c5fd",
     "USD and robotics sources guide authoring; runtime proof is still required."),
    ("INFERRED LEADS - excluded", "Excluded from prompt", "#f3a2a7",
     "Nimble inferred a named-character resemblance from generic geometry; excluded to preserve the supplied design."),
]


def wrap(draw, text, font, width):
    lines, current = [], ""
    for word in str(text).split():
        candidate = (current + " " + word).strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= width:
            current = candidate
        elif current:
            lines.append(current)
            current = word
        else:
            lines.append(word)
    if current:
        lines.append(current)
    return lines


def write_wrapped(draw, x, y, text, font, color, width, leading=5):
    for line in wrap(draw, text, font, width):
        draw.text((x, y), line, font=font, fill=color)
        y += font.size + leading
    return y


def render(receipt_path: Path) -> tuple[Path, Path]:
    data = json.loads(receipt_path.read_text())
    refs = data["tagged_references"]
    output_dir = receipt_path.parent
    reference = Path(data["input"]["path"])
    if not reference.is_absolute():
        reference = Path.cwd() / reference
    if not reference.is_file():
        raise ValueError(f"Reference image not found: {reference}")

    width, height = 1480, 2800
    image = Image.new("RGB", (width, height), "#0b1016")
    draw = ImageDraw.Draw(image)
    font_path = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
    if font_path.is_file():
        font = lambda size: ImageFont.truetype(str(font_path), size)
    else:
        font = ImageFont.load_default
    f12, f14, f16, f22, f38 = (font(size) for size in (12, 14, 16, 22, 38))
    ink, muted, line, panel, mint = "#edf4f4", "#9dafb7", "#243441", "#111a23", "#89edcc"

    draw.text((58, 42), "PACIFIC GYM  /  NIMBLE REFERENCE MAP", font=f14, fill=mint)
    draw.text((58, 77), "From one image to usable guidance.", font=f38, fill=ink)
    write_wrapped(draw, 58, 132,
                  f"{len(refs)} cited leads tagged by destination: FLUX visual language, Blender rig authoring, and later Isaac Sim work.",
                  f16, muted, 1320, 7)

    draw.rounded_rectangle((58, 190, 1422, 500), 18, fill=panel, outline=line, width=2)
    source = Image.open(reference).convert("RGB")
    thumb = ImageOps.contain(source, (320, 270))
    image.paste(thumb, (80 + (320 - thumb.width) // 2, 210 + (270 - thumb.height) // 2))
    draw.rounded_rectangle((78, 208, 402, 482), 12, outline=line, width=2)
    draw.text((438, 215), "LOCAL IMAGE DESCRIPTION - IMAGE BYTES STAY LOCAL", font=f14, fill=mint)
    write_wrapped(draw, 438, 248, data["visual_description"], f16, ink, 935, 7)
    draw.text((438, 456), "Reference SHA-256  " + data["input"]["sha256"], font=f12, fill=muted)

    flow = [("01  REFERENCE", "PNG"), ("02  OLLAMA", "Local visual caption"),
            ("03  NIMBLE", "Cited text research"), ("04  TAG + ROUTE", "Use-specific map")]
    y, card_width = 520, 325
    for index, (label, detail) in enumerate(flow):
        x = 58 + index * 341
        draw.rounded_rectangle((x, y, x + card_width, y + 104), 14, fill=panel, outline=line, width=2)
        color = [mint, mint, "#ffc879", "#93c5fd"][index]
        draw.text((x + 15, y + 14), label, font=f14, fill=color)
        draw.text((x + 15, y + 52), detail, font=f16, fill=ink)
        if index < 3:
            draw.text((x + card_width + 2, y + 38), ">", font=f22, fill=muted)

    counts = {use: sum(item["used_by"] == use for item in refs) for _, use, _, _ in GROUPS}
    y = 646
    draw.text((58, y), "ROUTING", font=f14, fill=muted)
    x = 172
    for heading, use, color, _ in GROUPS:
        label = f"{heading.split(' - ')[0]}  {counts[use]}"
        draw.rounded_rectangle((x, y - 4, x + 230, y + 30), 12, fill=panel, outline=line, width=1)
        draw.text((x + 12, y + 5), label, font=f12, fill=color)
        x += 244

    y = 700
    for heading, use, color, note in GROUPS:
        group = [item for item in refs if item["used_by"] == use]
        draw.rounded_rectangle((58, y, 1422, y + 47), 13, fill="#18232e", outline=line, width=1)
        draw.text((77, y + 13), f"{heading}  /  {len(group)} SOURCE TAGS", font=f16, fill=color)
        y += 58
        for item in group:
            title_lines = wrap(draw, f"[{item['id']:02d}] {item['title']}", f14, 1100)
            row_height = max(50, len(title_lines) * 19 + 28)
            draw.rounded_rectangle((70, y, 1410, y + row_height), 9, fill=panel, outline=line, width=1)
            title_y = y + 7
            for title_line in title_lines:
                draw.text((84, title_y), title_line, font=f14, fill=ink)
                title_y += 18
            draw.text((1250, y + 8), f"NIMBLE {item['confidence'].upper()}", font=f12, fill=color)
            draw.text((84, y + row_height - 19), "  ·  ".join(item["tags"]), font=f12, fill=color)
            y += row_height + 4
        y = write_wrapped(draw, 78, y + 1, note, f12, muted, 1310, 3) + 12

    write_wrapped(draw, 58, y + 4,
                  "Nimble confidence and source-type labels are provider metadata. Open the companion HTML for direct source links and category filters. A still image does not establish articulation, gait, balance, or physical validity.",
                  f12, muted, 1340, 5)
    image = image.crop((0, 0, width, min(height, y + 90)))
    png_path = output_dir / "reference-map.png"
    image.save(png_path, optimize=True)

    manifest = {
        "schema_version": 1,
        "project": "pacific-gym",
        "artifact_type": "local static source-map visualization",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "runtime_identity": "Pillow raster render from saved Nimble research receipt; browser UI preview blocked by URL policy",
        "commit": subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip(),
        "research_run_id": data.get("nimble_request_id"),
        "reference_sha256": data["input"]["sha256"],
        "artifact_path": str(png_path.resolve()),
        "artifact_sha256": hashlib.sha256(png_path.read_bytes()).hexdigest(),
        "verification_signal": "All cited leads categorized into FLUX, Blender, Isaac Sim, and inferred-identity exclusions; render visually reviewed",
        "bucket_publication": "not attempted; generated source map is not a verified application-runtime capture",
    }
    manifest_path = output_dir / "reference-map-receipt.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return png_path, manifest_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    png, manifest = render(args.receipt)
    print(json.dumps({"artifact": str(png.resolve()), "manifest": str(manifest.resolve())}, indent=2))


if __name__ == "__main__":
    main()
