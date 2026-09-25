"""Local image understanding followed by Nimble web research for a FLUX brief."""

import base64
import json
import html
import os
import re
from urllib.parse import urlparse
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .compare import get_json, post_json
from .cli import sha256


CAPTION_PROMPT = """Describe only visible features of this asset reference that can guide 3D asset creation: silhouette, proportions, joints or moving parts, materials, surface treatment, palette, lighting, camera, and visible motion cues. Do not guess the artist, source, hidden geometry, or physical properties. Return concise plain text."""


RESEARCH_BRIEF = """Research authoritative, useful references for making a 3D asset based on this visual description. Find: (1) animation principles and mechanical motion tricks relevant to the visible form, (2) artist-facing style guides, workflows, or visual references for modeling/materials, and (3) robotics/physics sources relevant to building or validating a similar articulated asset in NVIDIA Isaac Sim. Prioritize original artist/technical documentation, papers, and official Isaac Sim or NVIDIA sources. Separate visual inspiration from engineering constraints. Return a concise brief with clickable source URLs and explain what each source contributes. Do not claim a still image proves animation or physical validity. Visual description: {caption}"""


def configured_key(workspace: Path) -> str:
    """Read only NIMBLE_API_KEY from the process environment or workspace .env."""
    key = os.environ.get("NIMBLE_API_KEY", "")
    if not key:
        env_file = workspace.expanduser().resolve() / ".env"
        try:
            lines = env_file.read_text().splitlines()
        except OSError:
            lines = []
        for line in lines:
            match = re.match(r"^\s*(?:export\s+)?NIMBLE_API_KEY\s*=\s*(.*?)\s*$", line)
            if match:
                key = match.group(1)
                if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
                    key = key[1:-1]
                else:
                    key = re.split(r"\s+#", key, maxsplit=1)[0].strip()
                break
    if not key or not key.isascii() or any(char.isspace() for char in key):
        return ""
    return key


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


def _nimble_call(api_key: str, url: str, payload: dict | None = None) -> dict:
    request = Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                      headers={"Authorization": f"Bearer {api_key}",
                               "Content-Type": "application/json"},
                      method="POST" if payload is not None else "GET")
    try:
        with urlopen(request, timeout=90) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"Nimble research request failed with HTTP {error.code}") from None


def start_nimble_job(api_key: str, brief: str) -> dict:
    run = _nimble_call(api_key, "https://sdk.nimbleway.com/v2/agents/runs", {"input": brief})
    run_id, agent_id = run.get("id"), run.get("web_search_agent_id")
    if not run_id or not agent_id:
        raise ValueError("Nimble did not return run and agent IDs")
    return {"run_id": run_id, "agent_id": agent_id, "status": run.get("status", "queued"),
            "is_active": run.get("is_active", True)}


def poll_nimble_job(api_key: str, job: dict) -> dict:
    base = "https://sdk.nimbleway.com/v2/agents"
    run = _nimble_call(api_key, f"{base}/{job['agent_id']}/runs/{job['run_id']}")
    if run.get("is_active", False):
        return {"status": run.get("status", "running"), "is_active": True}
    if run.get("status") != "completed":
        return {"status": run.get("status", "unknown"), "is_active": False}
    result = _nimble_call(api_key, f"{base}/{job['agent_id']}/runs/{job['run_id']}/result")
    output = result.get("output", {})
    content = output.get("content", "") if isinstance(output, dict) else ""
    if not content:
        raise ValueError("Nimble response did not contain a completed research brief")
    return {"status": "completed", "is_active": False,
            "request_id": result.get("request_id", job["run_id"]),
            "content": content, "trust": output.get("trust")}


def save_research_result(reference: Path, output_dir: Path, caption: str,
                         vision_model: dict, research: dict) -> dict:
    result = {
        "schema_version": 1, "provider": "Nimbleway Web Search Agents",
        "input": {"path": str(reference.resolve()), "sha256": sha256(reference),
                  "media_sent_to_nimble": False},
        "local_vision_model": vision_model, "caption_prompt": CAPTION_PROMPT,
        "visual_description": caption, "nimble_request_id": research["request_id"],
        "research_brief": research["content"], "trust": research.get("trust"),
    }
    annotate_result(result)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "nimble-research.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    (output_dir / "flux-prompt-draft.txt").write_text(result["flux_prompt_draft"])
    (output_dir / "reference-map.html").write_text(render_reference_report(result, reference, output_dir))
    return result


def start_run_research(manifest: Path, api_key: str, model: str,
                       host: str = "http://127.0.0.1:11434") -> dict:
    from .run import load, save
    data = load(manifest)
    research_state = data["reference_research"]
    if research_state.get("status") not in {"waiting_for_credentials", "failed"}:
        raise ValueError(f"Research cannot start from status {research_state.get('status')}")
    image = next((Path(item["path"]) for item in data["inputs"]
                  if item["role"] in {"reference", "reference_frame"}
                  and Path(item["path"]).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}), None)
    if image is None:
        research_state.update(status="waiting_for_reference_image",
                              error="Provide a pinned PNG/JPG/WebP reference frame to start visual research.")
        save(data)
        return research_state
    caption, vision_model = caption_image(image, model, host)
    job = start_nimble_job(api_key, RESEARCH_BRIEF.format(caption=caption))
    task_path = Path(data["manifest"]).parent / "research" / "task.json"
    task = {"schema_version": 1, "reference": str(image.resolve()),
            "reference_sha256": sha256(image), "caption": caption,
            "vision_model": vision_model, "job": job}
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(json.dumps(task, indent=2, sort_keys=True) + "\n")
    research_state.update(status="running", reference_sha256=task["reference_sha256"],
                          job={"run_id": job["run_id"], "agent_id": job["agent_id"]},
                          task=str(task_path), receipt=None, error=None)
    save(data)
    return research_state


def poll_run_research(manifest: Path, api_key: str) -> dict:
    from .run import load, save
    data = load(manifest)
    state = data["reference_research"]
    if state.get("status") != "running":
        return state
    task_path = Path(state["task"]).resolve(strict=True)
    run_root = Path(data["manifest"]).parent.resolve()
    if not task_path.is_relative_to(run_root):
        raise ValueError("Research task is outside this run")
    task = json.loads(task_path.read_text())
    reference = Path(task["reference"])
    if sha256(reference) != task["reference_sha256"]:
        state.update(status="failed", error="Reference image hash changed while research was running")
        save(data)
        return state
    result = poll_nimble_job(api_key, task["job"])
    if result["is_active"]:
        state["provider_status"] = result["status"]
        save(data)
        return state
    if result["status"] != "completed":
        state.update(status="failed", error=f"Nimble job ended with status {result['status']}")
        save(data)
        return state
    output_dir = task_path.parent
    receipt = save_research_result(reference, output_dir, task["caption"], task["vision_model"], result)
    state.update(status="complete", job={"run_id": task["job"]["run_id"],
                                          "agent_id": task["job"]["agent_id"]},
                  receipt=str(output_dir / "nimble-research.json"),
                  flux_prompt=str(output_dir / "flux-prompt-draft.txt"),
                  reference_map=str(output_dir / "reference-map.html"),
                  source_count=len(receipt.get("tagged_references", [])), error=None)
    save(data)
    return state


def _host_matches_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def tag_references(trust: dict) -> list[dict]:
    """Apply stable editorial tags to Nimble's ordered, cited source list."""
    sources = trust.get("sources", [])
    claims = trust.get("claims", [])
    tagged = []
    for index, source in enumerate(sources, 1):
        title = (source.get("title") or "").lower()
        host = urlparse(source.get("url", "")).hostname or ""
        host = host.lower()
        if any(term in title for term in ("big hero 6", "baymax")) or host in {
                "wikipedia.org", "wordpress.com", "makerworld.com"}:
            tags = ["similarity-lead", "inferred-identity", "excluded"]
            used_by = "Excluded from prompt"
            use = "Nimble inferred a character resemblance from a generic silhouette; this was not supplied as identity by the user."
        elif _host_matches_domain(host, "blender.org") or any(
                term in title for term in ("rigify", "inverse kinematics constraint")):
            tags = ["blender", "rigging", "inverse-kinematics"]
            used_by = "Blender authoring"
            use = "Guide later rig controls and IK setup; these sources do not validate this asset's rig."
        elif any(term in title for term in ("isaac sim", "robot", "joint", "degree of freedom", "humanoid usd", "kinematic")) or _host_matches_domain(host, "nvidia.com"):
            tags = ["isaac-sim", "robotics", "articulation"]
            used_by = "Isaac Sim handoff"
            use = "Inform later USD, articulation, or joint authoring. A source guide is not a runtime physics result."
        elif any(term in title for term in ("animation", "animator", "stop-motion", "core competencies")):
            tags = ["animation", "motion-language"]
            used_by = "FLUX prompt"
            use = "Inform readable timing, anticipation, arcs, and follow-through if motion is requested."
        elif any(term in title for term in ("pbr", "texturing", "toolbag", "marmoset", "stylized character", "material")):
            tags = ["artist-reference", "materials", "look-development"]
            used_by = "FLUX prompt"
            use = "Inform matte material, lighting, and presentation language while preserving the reference design."
        else:
            tags = ["artist-reference", "style-research"]
            used_by = "Review before use"
            use = "General reference lead. Review its contents before using it to steer asset style."
        claim = claims[index - 1] if index <= len(claims) else {}
        citations = claim.get("citations", []) if isinstance(claim, dict) else []
        citation = next((item for item in citations if item.get("url") == source.get("url")), {})
        url = source.get("url", "")
        if urlparse(url).scheme != "https":
            url = ""
        tagged.append({
            "id": index,
            "title": source.get("title") or url or f"Reference {index}",
            "url": url,
            "tags": tags,
            "used_by": used_by,
            "use": use,
            "source_type": source.get("type", citation.get("source_type", "unknown")),
            "source_category": source.get("source_category", citation.get("source_category", "unknown")),
            "confidence": claim.get("confidence", "unknown") if isinstance(claim, dict) else "unknown",
        })
    return tagged


def create_flux_prompt(caption: str, references: list[dict]) -> str:
    visual_refs = [item for item in references if item["used_by"] == "FLUX prompt"]
    motion = [item for item in visual_refs if "animation" in item["tags"]]
    lookdev = [item for item in visual_refs if "materials" in item["tags"]]
    motion_sources = "; ".join(f"[{item['id']}] {item['title']}" for item in motion[:3])
    look_sources = "; ".join(f"[{item['id']}] {item['title']}" for item in lookdev[:3])
    return (
        "Use the supplied image as the identity and design authority. Preserve its silhouette, "
        "visible proportions, joint placement, and neutral matte-white treatment. Do not infer "
        "or add a named character, hidden features, armor, props, or extra geometry.\n\n"
        "VISUAL DESCRIPTION\n" + caption + "\n\n"
        "STYLE DIRECTION\nKeep the matte white, low-texture surface, soft gray contact shadows, "
        "and diffuse studio lighting visible in the reference. Use restrained rim separation "
        "and clear silhouette presentation. Artist-facing material and look-development leads: "
        + (look_sources or "none") + ".\n\n"
        "OPTIONAL MOTION DIRECTION\nThe supplied image is static and does not establish a gait. "
        "If a motion variant is requested, make weight readable through anticipation, gradual "
        "ease-in/ease-out, curved limb paths, and staggered follow-through with a settled hold. "
        "Keep the head, torso, limbs, and visible joints consistent with the reference. "
        "Animation-principle leads: " + (motion_sources or "none") + ".\n\n"
        "Use this draft for visual generation only. Blender rigging and Isaac Sim/physics "
        "references are tagged separately for later authoring; they are intentionally not part "
        "of the FLUX prompt and do not prove physical validity."
    )


def render_reference_report(result: dict, reference_path: Path, output_dir: Path) -> str:
    """Render a self-contained source map; all remote content is HTML-escaped."""
    refs = result.get("tagged_references", [])
    cards = []
    for item in refs:
        title = html.escape(str(item["title"]))
        url = html.escape(str(item.get("url", "")), quote=True)
        link = f'<a href="{url}" target="_blank" rel="noreferrer">Open source ↗</a>' if url else "URL unavailable"
        tags = "".join(f'<span class="tag">{html.escape(tag)}</span>' for tag in item["tags"])
        cards.append(
            f'<article class="source" data-use="{html.escape(item["used_by"], quote=True)}" '
            f'data-search="{html.escape((item["title"] + " " + " ".join(item["tags"])).lower(), quote=True)}">'
            f'<div class="source-top"><span class="source-id">REF {item["id"]:02d}</span>'
            f'<span class="confidence">Nimble: {html.escape(str(item["confidence"]))}</span></div>'
            f'<h3>{title}</h3><div class="tags">{tags}</div>'
            f'<p>{html.escape(str(item["use"]))}</p>'
            f'<p class="origin">Nimble labels this source: {html.escape(str(item["source_type"]))} · '
            f'{html.escape(str(item["source_category"]))}</p>{link}</article>'
        )
    caption = html.escape(str(result.get("visual_description", "")))
    digest = html.escape(str(result.get("input", {}).get("sha256", "")))
    rel_image = os.path.relpath(reference_path.resolve(), output_dir.resolve())
    image_uri = html.escape(rel_image, quote=True)
    flux_count = sum(item["used_by"] == "FLUX prompt" for item in refs)
    blender_count = sum(item["used_by"] == "Blender authoring" for item in refs)
    isaac_count = sum(item["used_by"] == "Isaac Sim handoff" for item in refs)
    excluded_count = sum(item["used_by"] == "Excluded from prompt" for item in refs)
    prompt = html.escape(str(result.get("flux_prompt_draft", "")))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pacific Gym · Nimble reference map</title>
<style>
:root{{--bg:#0b1016;--panel:#111a23;--line:#243441;--ink:#edf4f4;--muted:#9dafb7;--mint:#89edcc;--amber:#ffc879;--blue:#93c5fd;--rose:#f3a2a7}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(ellipse at 15% 0,#1b2d32 0,transparent 35%),var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1440px;margin:auto;padding:42px 48px 70px}}.eyebrow{{letter-spacing:.18em;text-transform:uppercase;color:var(--mint);font-size:11px;font-weight:700}}h1{{font-size:42px;line-height:1.05;letter-spacing:-.04em;margin:12px 0}}.lede{{color:var(--muted);max-width:780px;font-size:16px}}
.hero{{display:grid;grid-template-columns:300px 1fr;gap:28px;margin:30px 0 32px;padding:20px;border:1px solid var(--line);border-radius:20px;background:#101922e8}}.hero img{{width:100%;height:280px;object-fit:contain;background:#d8dadd;border-radius:12px}}.caption{{color:#d8e3e6;font-size:14px}}.hash{{color:var(--muted);font:11px ui-monospace,monospace;word-break:break-all}}
.flow{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:22px 0 32px}}.step{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;min-height:112px}}.step b{{display:block;color:var(--mint);font-size:11px;letter-spacing:.1em;text-transform:uppercase;margin-bottom:6px}}.step strong{{font-size:16px}}.step small{{display:block;color:var(--muted);margin-top:5px}}
h2{{font-size:23px;letter-spacing:-.025em;margin:36px 0 12px}}.uses{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.use{{border-radius:16px;padding:18px;border:1px solid var(--line);background:var(--panel)}}.use h3{{margin:0 0 6px;font-size:18px}}.use p{{margin:0;color:var(--muted);font-size:13px}}.use .count{{font:12px ui-monospace,monospace;color:var(--mint);margin-top:12px}}
.use.flux{{border-top:3px solid var(--mint)}}.use.blender{{border-top:3px solid var(--amber)}}.use.isaac{{border-top:3px solid var(--blue)}}.filterbar{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:18px 0}}button{{font:inherit;border:1px solid var(--line);background:#111a23;color:var(--muted);padding:8px 13px;border-radius:999px;cursor:pointer}}button.active,button:hover{{color:var(--ink);border-color:var(--mint)}}.filter-count{{margin-left:auto;color:var(--muted);font:12px ui-monospace,monospace}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.source{{min-height:218px;padding:16px;border-radius:14px;background:var(--panel);border:1px solid var(--line);display:flex;flex-direction:column}}.source[hidden]{{display:none}}.source[data-use="FLUX prompt"]{{border-left:3px solid var(--mint)}}.source[data-use="Blender authoring"]{{border-left:3px solid var(--amber)}}.source[data-use="Isaac Sim handoff"]{{border-left:3px solid var(--blue)}}.source[data-use="Excluded from prompt"]{{border-left:3px solid var(--rose)}}.source-top{{display:flex;justify-content:space-between;color:var(--muted);font:10px ui-monospace,monospace}}.source h3{{font-size:16px;line-height:1.3;margin:10px 0}}.tags{{display:flex;gap:5px;flex-wrap:wrap}}.tag{{font-size:10px;border-radius:99px;padding:3px 8px;background:#203139;color:var(--mint)}}.source p{{font-size:12px;color:#c1cdd0;margin:12px 0 5px}}.source .origin{{font-size:10px;color:var(--muted);margin-top:auto;padding-top:10px}}.source a{{font-size:12px;color:var(--mint);text-decoration:none;margin-top:9px}}.source a:hover{{text-decoration:underline}}
.prompt{{white-space:pre-wrap;background:#080d12;border:1px solid var(--line);border-radius:14px;padding:20px;color:#c9d9db;font:12px/1.7 ui-monospace,SFMono-Regular,monospace;max-height:430px;overflow:auto}}.note{{color:var(--muted);font-size:12px;margin:14px 0}}footer{{margin-top:32px;border-top:1px solid var(--line);padding-top:16px;color:var(--muted);font-size:11px}}
@media(max-width:900px){{main{{padding:28px 20px}}.hero{{grid-template-columns:1fr}}.hero img{{height:220px}}.flow,.uses{{grid-template-columns:repeat(2,1fr)}}.grid{{grid-template-columns:repeat(2,1fr)}}h1{{font-size:34px}}}}@media(max-width:560px){{.flow,.uses,.grid{{grid-template-columns:1fr}}}}
</style></head><body><main>
<div class="eyebrow">Pacific Gym / Reference research</div><h1>From one image to usable guidance.</h1>
<p class="lede">Nimble’s cited leads are grouped by where they belong. Visual and animation notes can shape FLUX. Rigging notes stay with Blender. Physics and USD references stay with the Isaac Sim handoff.</p>
<section class="hero"><img src="{image_uri}" alt="Local reference image"><div><div class="eyebrow">Local visual read · image not sent to Nimble</div><p class="caption">{caption}</p><p class="hash">SHA-256 · {digest}</p><p class="note">This is a static neutral pose. It does not demonstrate a working rig, gait, balance, or physical validity.</p></div></section>
<section class="flow"><div class="step"><b>01 · Input</b><strong>Reference PNG</strong><small>Captioned on this machine with Ollama vision</small></div><div class="step"><b>02 · Research</b><strong>Nimble Web Search Agent</strong><small>Text caption only · cited run {html.escape(str(result.get('nimble_request_id', '')))}</small></div><div class="step"><b>03 · Tag</b><strong>{len(refs)} cited sources</strong><small>Grouped by use and provider confidence</small></div><div class="step"><b>04 · Hand off</b><strong>FLUX draft + authoring map</strong><small>Prompt draft is reviewable; generation stays manual</small></div></section>
<h2>Where the references go</h2><section class="uses"><div class="use flux"><h3>FLUX · visual direction</h3><p>Use artist and animation leads for material, lighting, readable timing, arcs, and follow-through. Preserve the pictured identity and proportions.</p><div class="count">{flux_count} tagged references</div></div><div class="use blender"><h3>Blender · rig authoring</h3><p>Use the Blender manual leads to guide rig controls and inverse kinematics. These do not certify this image’s rig.</p><div class="count">{blender_count} tagged references</div></div><div class="use isaac"><h3>Isaac Sim · later physics work</h3><p>Use NVIDIA/USD and robotics leads for articulation setup and joint decisions. Verify physics in the running simulator.</p><div class="count">{isaac_count} tagged references</div></div></section>
<h2>Tagged sources</h2><div class="filterbar"><button class="active" data-filter="all">All</button><button data-filter="FLUX prompt">FLUX</button><button data-filter="Blender authoring">Blender</button><button data-filter="Isaac Sim handoff">Isaac Sim</button><button data-filter="Excluded from prompt">Excluded leads</button><span class="filter-count" id="count"></span></div><section class="grid">{''.join(cards)}</section>
<p class="note">“Nimble: high/medium” and source-type labels are provider trust metadata. URLs are cited search leads, not independent verification. The lookalike results are tagged as inferred and excluded from the FLUX prompt.</p>
<h2>FLUX prompt draft</h2><div class="prompt">{prompt}</div>
<footer>Source map generated from the saved Nimble receipt · reference bytes remain local · FLUX submission is not automatic.</footer>
</main><script>
const buttons=[...document.querySelectorAll('[data-filter]')], cards=[...document.querySelectorAll('.source')], count=document.querySelector('#count');
function filter(value){{let visible=0;for(const card of cards){{const show=value==='all'||card.dataset.use===value;card.hidden=!show;if(show)visible++;}}count.textContent=visible+' / '+cards.length+' sources';}}
buttons.forEach(button=>button.addEventListener('click',()=>{{buttons.forEach(item=>item.classList.toggle('active',item===button));filter(button.dataset.filter);}}));filter('all');
</script></body></html>"""


def annotate_result(result: dict) -> dict:
    references = tag_references(result.get("trust") or {})
    result["tagged_references"] = references
    result["flux_prompt_draft"] = create_flux_prompt(result.get("visual_description", ""), references)
    return result


def run(reference: Path, out: Path, api_key: str, model: str,
        host: str = "http://127.0.0.1:11434") -> dict:
    caption, vision_model = caption_image(reference, model, host)
    research = nimble_research(api_key, RESEARCH_BRIEF.format(caption=caption))
    return save_research_result(reference, out, caption, vision_model, research)


def clipboard_key() -> str:
    key = subprocess.run(["pbpaste"], capture_output=True, text=True, check=True).stdout.strip()
    if not key or not key.isascii() or any(char.isspace() for char in key):
        raise RuntimeError("Clipboard does not contain a plain ASCII Nimble API key")
    return key
