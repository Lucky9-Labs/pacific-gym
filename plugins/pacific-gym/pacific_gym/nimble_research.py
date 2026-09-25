"""Local image understanding followed by Nimble web research for a FLUX brief."""

import base64
import json
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .compare import get_json, post_json
from .cli import sha256


CAPTION_PROMPT = """Describe only visible features of this asset reference that can guide 3D asset creation: silhouette, proportions, joints or moving parts, materials, surface treatment, palette, lighting, camera, and visible motion cues. Do not guess the artist, source, hidden geometry, or physical properties. Return concise plain text."""


RESEARCH_BRIEF = """Research authoritative, useful references for making a 3D asset based on this visual description. Find: (1) animation principles and mechanical motion tricks relevant to the visible form, (2) artist-facing style guides, workflows, or visual references for modeling/materials, and (3) robotics/physics sources relevant to building or validating a similar articulated asset in NVIDIA Isaac Sim. Prioritize original artist/technical documentation, papers, and official Isaac Sim or NVIDIA sources. Separate visual inspiration from engineering constraints. Return a concise brief with clickable source URLs and explain what each source contributes. Do not claim a still image proves animation or physical validity. Visual description: {caption}"""


def caption_image(reference: Path, model: str, host: str) -> tuple[str, dict]:
    if not reference.is_file():
        raise ValueError(f"Reference image not found: {reference}")
    host = host.rstrip("/")
    tags = get_json(f"{host}/api/tags")
    installed = next((item for item in tags.get("models", []) if item.get("name") == model), None)
    if installed is None:
        raise ValueError(f"Model is not installed locally: {model}")
    info = post_json(f"{host}/api/show", {"model": model}, timeout=15)
    if "vision" not in info.get("capabilities", []):
        raise ValueError(f"Model has no Ollama vision capability: {model}")
    response = post_json(f"{host}/api/chat", {
        "model": model, "stream": False,
        "messages": [{"role": "user", "content": CAPTION_PROMPT,
                      "images": [base64.b64encode(reference.read_bytes()).decode("ascii")]}],
    })
    caption = response.get("message", {}).get("content", "").strip()
    if not caption or len(caption) > 5000:
        raise ValueError("Local vision model returned an empty or oversized description")
    return caption, {"tag": model, "digest": installed.get("digest"),
                     "ollama_version": get_json(f"{host}/api/version").get("version")}


def nimble_research(api_key: str, brief: str) -> dict:
    base = "https://sdk.nimbleway.com/v2"

    def call(url: str, payload: dict | None = None) -> dict:
        request = Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                          headers={"Authorization": f"Bearer {api_key}",
                                   "Content-Type": "application/json"},
                          method="POST" if payload is not None else "GET")
        try:
            with urlopen(request, timeout=90) as response:
                return json.load(response)
        except HTTPError as error:
            raise RuntimeError(f"Nimble research request failed with HTTP {error.code}") from None

    try:
        run = call(f"{base}/agents/runs", {"input": brief})
        run_id, agent_id = run.get("id"), run.get("web_search_agent_id")
        if not run_id or not agent_id:
            raise ValueError("Nimble did not return run and agent IDs")
        deadline = time.monotonic() + 8 * 60
        while run.get("is_active", True) and time.monotonic() < deadline:
            time.sleep(5)
            run = call(f"{base}/agents/{agent_id}/runs/{run_id}")
        if run.get("is_active", False):
            raise TimeoutError(f"Nimble research remains active; run ID {run_id}")
        if run.get("status") != "completed":
            raise RuntimeError(f"Nimble research ended with status {run.get('status', 'unknown')}")
        result = call(f"{base}/agents/{agent_id}/runs/{run_id}/result")
    except HTTPError as error:
        # The exception body is intentionally omitted because providers may echo credentials.
        raise RuntimeError(f"Nimble research request failed with HTTP {error.code}") from None
    output = result.get("output", {})
    content = output.get("content", "") if isinstance(output, dict) else ""
    if not content:
        raise ValueError("Nimble response did not contain a completed research brief")
    return {"request_id": result.get("request_id", run_id), "content": content,
            "trust": output.get("trust"), "status": run.get("status", "completed")}


def create_flux_prompt(caption: str, research: str) -> str:
    return ("Create a FLUX image-to-video or asset-generation prompt from the supplied reference. "
            "Preserve the reference identity and visible proportions. Use the visual notes and "
            "artist-facing references for style and motion language. Treat robotics/physics "
            "sources as constraints for later Isaac Sim authoring, not as claims that the image "
            "is physically valid. Do not add unsupported geometry.\n\n"
            f"VISUAL NOTES\n{caption}\n\nRESEARCH NOTES\n{research}\n")


def run(reference: Path, out: Path, api_key: str, model: str,
        host: str = "http://127.0.0.1:11434") -> dict:
    caption, vision_model = caption_image(reference, model, host)
    research = nimble_research(api_key, RESEARCH_BRIEF.format(caption=caption))
    prompt = create_flux_prompt(caption, research["content"])
    result = {
        "schema_version": 1,
        "provider": "Nimbleway Web Search Agents",
        "input": {"path": str(reference), "sha256": sha256(reference),
                  "media_sent_to_nimble": False},
        "local_vision_model": vision_model,
        "caption_prompt": CAPTION_PROMPT,
        "visual_description": caption,
        "nimble_request_id": research["request_id"],
        "research_brief": research["content"],
        "trust": research["trust"],
        "flux_prompt_draft": prompt,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "nimble-research.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    (out / "flux-prompt-draft.txt").write_text(prompt)
    return result


def clipboard_key() -> str:
    key = subprocess.run(["pbpaste"], capture_output=True, text=True, check=True).stdout.strip()
    if not key or not key.isascii() or any(char.isspace() for char in key):
        raise RuntimeError("Clipboard does not contain a plain ASCII Nimble API key")
    return key
