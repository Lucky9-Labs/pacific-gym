"""One local Ollama image-pair comparison pulse."""

import base64
import hashlib
import io
import json
import re
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont

from .cli import sha256


PROMPT = """You are steering a 3D artist editing a static robot pose. The ONE image has two vertically stacked panels with the SAME horizontal coordinate system: REFERENCE on top and CANDIDATE below.
The orange foot is the robot's LEFT foot. The blue foot is its right foot. The ground label FORWARD > defines forward, which appears toward image-right. Compare the orange foot's HORIZONTAL position on the grid in both images. Ignore its vertical pixel position. Do not judge gait, animation, physics, or Isaac readiness.
Measured from the orange pixels in the original Blender PNGs: REFERENCE foot horizontal center = {reference_x:.1f}px, CANDIDATE foot horizontal center = {candidate_x:.1f}px, both in 900px wide images. These coordinates are evidence and take precedence over uncertain visual impressions. If reference x is larger, the candidate must move RIGHT/FORWARD. If reference x is smaller, the candidate must move LEFT/BACKWARD. Apply this rule before writing next_action.
Return ONLY a JSON object with exactly these fields:
{
  "evidence": "state the REFERENCE and CANDIDATE orange left foot x coordinates above, then state which is farther right; ensure this agrees with next_action",
  "confidence": "high|medium|low",
  "next_action": "one short imperative command using the literal words MOVE THE LEFT FOOT CONTACT and the required direction FORWARD or BACKWARD"
}
If the images are indistinguishable or unclear, say so in evidence, set confidence low, and make next_action a request to inspect the renders. Otherwise do not ask Codex to compare, inspect, or confirm: give the edit command. No numerical score. No general advice."""


def orange_center(path: Path) -> tuple[float, int]:
    image = Image.open(path).convert("RGB")
    positions = []
    for y in range(image.height // 2, image.height):
        for x in range(image.width):
            red, green, blue = image.getpixel((x, y))
            if red > 130 and red > green * 1.35 and green > blue * 1.2:
                positions.append(x)
    if len(positions) < 100:
        raise ValueError(f"Orange contact not visible in {path}")
    return sum(positions) / len(positions), len(positions)


def post_json(url, body, timeout=300):
    request = Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def get_json(url):
    with urlopen(url, timeout=10) as response:
        return json.load(response)


def make_pair(reference: Path, candidate: Path) -> bytes:
    left = Image.open(reference).convert("RGB")
    right = Image.open(candidate).convert("RGB")
    if left.size != right.size:
        raise ValueError("Reference and candidate must have the same pixel dimensions")
    width, height = left.size
    pair = Image.new("RGB", (width, 2 * height + 140), "#18212a")
    pair.paste(left, (0, 70))
    pair.paste(right, (0, height + 140))
    draw = ImageDraw.Draw(pair)
    font = ImageFont.load_default(size=42)
    draw.text((25, 10), "REFERENCE", font=font, fill="white")
    draw.text((25, height + 80), "CANDIDATE", font=font, fill="white")
    encoded = io.BytesIO()
    pair.save(encoded, format="PNG")
    return encoded.getvalue()


def compare(reference: Path, candidate: Path, model: str, host: str, pair_out: Path | None = None) -> dict:
    for path in (reference, candidate):
        if not path.is_file() or path.suffix.lower() != ".png":
            raise ValueError(f"Expected an existing PNG image: {path}")
    host = host.rstrip("/")
    tags = get_json(f"{host}/api/tags")
    installed = next((m for m in tags["models"] if m["name"] == model), None)
    if installed is None:
        raise ValueError(f"Model is not installed locally: {model}")
    info = post_json(f"{host}/api/show", {"model": model}, timeout=15)
    capabilities = info.get("capabilities", [])
    if "vision" not in capabilities:
        raise ValueError(f"Model has no Ollama vision capability: {model}: {capabilities}")
    pair = make_pair(reference, candidate)
    reference_x, reference_pixels = orange_center(reference)
    candidate_x, candidate_pixels = orange_center(candidate)
    prompt = PROMPT.replace("{reference_x:.1f}", f"{reference_x:.1f}").replace(
        "{candidate_x:.1f}", f"{candidate_x:.1f}")
    if pair_out:
        pair_out.parent.mkdir(parents=True, exist_ok=True)
        pair_out.write_bytes(pair)
    response = post_json(f"{host}/api/chat", {
        "model": model,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_predict": 220},
        "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(pair).decode("ascii")]}],
    })
    raw = response["message"]["content"]
    result = json.loads(raw)
    if set(result) != {"evidence", "confidence", "next_action"}:
        raise ValueError(f"Unexpected comparison fields: {list(result)}")
    if result["confidence"] not in {"high", "medium", "low"}:
        raise ValueError("Invalid confidence")
    for field in ("evidence", "next_action"):
        if not isinstance(result[field], str) or not result[field].strip() or len(result[field]) > 300:
            raise ValueError(f"Invalid {field}")
    if re.search(r"\bscore\b|\b[0-9]+/10\b", result["next_action"], re.I):
        raise ValueError("A score is not an edit command")
    return {
        "schema_version": 1,
        "model": {"tag": model, "digest": installed["digest"], "capabilities": capabilities},
        "inputs": [
            {"role": "reference", "path": str(reference), "sha256": sha256(reference)},
            {"role": "candidate", "path": str(candidate), "sha256": sha256(candidate)},
        ],
        "visual_pair": {"path": str(pair_out) if pair_out else None, "sha256": hashlib.sha256(pair).hexdigest()},
        "measurement": {"reference_orange_center_x": reference_x, "candidate_orange_center_x": candidate_x,
                        "reference_orange_pixels": reference_pixels, "candidate_orange_pixels": candidate_pixels},
        "prompt": prompt,
        "raw_response": raw,
        "comparison": result,
        "ollama": {"version": get_json(f"{host}/api/version")["version"], "done_reason": response.get("done_reason")},
    }
