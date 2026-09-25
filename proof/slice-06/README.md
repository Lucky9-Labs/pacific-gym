# Liquid comparison pulse

This is a controlled **static pose** fixture. Blender 5.2.1 renders the same simple robot and camera twice. In `reference.png`, the orange left foot contact is at world X=+0.55; in `candidate.png`, it is at X=-0.55. The blue right foot is fixed. `pair.png` stacks the two renders with explicit labels. The reference is pinned by its SHA-256 in `proof.json`.

The comparison command sends the labeled visual pair to Liquid AI's official [LFM2.5-VL-3B-GGUF](https://huggingface.co/LiquidAI/LFM2.5-VL-3B-GGUF) through local Ollama. Use the exact tag `hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q4_K_M`; the tested digest is `4e3daebe4fb42e458b26090e6b5e6b38d34ddd1ff9f0673565d83acb3a2c5682`. A separate single-image smoke check in `vision-smoke.json` returned "Orange" for the candidate's foot left of the blue foot, confirming that image input is processed. The command also measures the orange pixel center in the original PNGs. The measured coordinates anchor the visual comparison because unassisted VLM trials reversed the spatial direction; one failed response is retained in `failed-unassisted-trial.json`. The full final prompt, raw model response, model digest, measurements, and input hashes are in `comparison.json`.

Run the local acceptance gate from the repository root:

```sh
ollama pull hf.co/LiquidAI/LFM2.5-VL-3B-GGUF:Q4_K_M
python3 -m pip install 'Pillow>=11,<13'
sh scripts/accept-comparison-pulse.sh
```

The final command verifies the pinned image hashes and model digest, then requires a specific forward movement of the left foot. To regenerate the Blender renders before a new proof run:

```sh
/Applications/Blender.app/Contents/MacOS/blender -b -t 4 --python scripts/render-comparison-fixture.py -- --out proof/slice-06
```

This proves one local feedback pulse only. It does not compare a gait, apply a Blender edit, trace into RawTree, validate physics, or publish an artifact.
