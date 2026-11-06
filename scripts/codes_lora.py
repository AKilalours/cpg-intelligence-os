#!/usr/bin/env python3
"""Fine-tune a small open model with LoRA to read deduction reasons, then predict validation and held-out.

Runs on this Mac (Apple GPU through MPS, else CPU), $0. Needs: make setup-lora (torch, transformers, peft).
Settings are fixed here before any score is seen and are not tuned on the held-out phrasings. Validation
cannot guide them either: it holds only seen phrasings, which every reader gets nearly all right.

Writes local_runs/codes_lora_predictions.json; make codes-lora then scores it once.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cpgos.codes import data, llm  # noqa: E402

BASE = "Qwen/Qwen2.5-0.5B-Instruct"      # Apache-2.0, small enough to train on a laptop
SETTINGS = {"r": 8, "alpha": 16, "dropout": 0.05, "targets": ["q_proj", "k_proj", "v_proj", "o_proj"],
            "epochs": 2, "lr": 2e-4, "batch": 8, "max_len": 96, "seed": 12, "prompt": "short"}
# 1.1 (before any LoRA score): a short prompt without the definitions, batch 8. 1.0 used the full prompt,
# about 200 tokens a line, and its first step took 347 s on a laptop that was also holding a 7B model.
ADAPTER_VERSION = "lora-1.1.0"
TOO_SLOW_SECONDS = 30        # a step this slow means the machine is swapping; stop and say why


def main() -> int:
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    random.seed(SETTINGS["seed"]); torch.manual_seed(SETTINGS["seed"])
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(BASE)
    tok.padding_side = "left"
    print(f"device: {dev} ({'Apple GPU' if dev == 'mps' else 'CPU only: this will be slow'})", flush=True)
    model = AutoModelForCausalLM.from_pretrained(BASE, dtype=torch.float32).to(dev)
    model = get_peft_model(model, LoraConfig(r=SETTINGS["r"], lora_alpha=SETTINGS["alpha"], lora_dropout=SETTINGS["dropout"],
                                             target_modules=SETTINGS["targets"], task_type="CAUSAL_LM"))
    ex = data.generate()
    train = [e for e in ex if e.split == "train"]

    def chat(text: str) -> str:
        return tok.apply_chat_template([{"role": "system", "content": llm.SYSTEM},
                                        {"role": "user", "content": llm.short_prompt(text)}],
                                       tokenize=False, add_generation_prompt=True)

    def batch(items):
        ids, labels = [], []
        for e in items:
            p = tok(chat(e.text), add_special_tokens=False)["input_ids"]
            a = tok(e.category + tok.eos_token, add_special_tokens=False)["input_ids"]
            seq = (p + a)[-SETTINGS["max_len"]:]
            lab = ([-100] * len(p) + a)[-SETTINGS["max_len"]:]      # learn the answer, not the prompt
            ids.append(seq); labels.append(lab)
        n = max(map(len, ids))
        pad = tok.pad_token_id
        return (torch.tensor([[pad] * (n - len(s)) + s for s in ids]).to(dev),
                torch.tensor([[-100] * (n - len(s)) + s for s in labels]).to(dev),
                torch.tensor([[0] * (n - len(s)) + [1] * len(s) for s in ids]).to(dev))

    opt = torch.optim.AdamW(model.parameters(), lr=SETTINGS["lr"])
    model.train()
    per_epoch = (len(train) + SETTINGS["batch"] - 1) // SETTINGS["batch"]
    total = per_epoch * SETTINGS["epochs"]
    print(f"{len(train)} examples, {total} steps; the first steps also warm up the GPU", flush=True)
    t0, done = time.time(), 0
    for epoch in range(SETTINGS["epochs"]):
        random.shuffle(train)
        for i in range(0, len(train), SETTINGS["batch"]):
            ts = time.time()
            x, y, m = batch(train[i:i + SETTINGS["batch"]])
            loss = model(input_ids=x, attention_mask=m, labels=y).loss
            loss.backward(); opt.step(); opt.zero_grad()
            done += 1
            step_s = time.time() - ts
            if done == 4 and (time.time() - t0) / 4 > TOO_SLOW_SECONDS:
                print(f"stopping: {step_s:.0f} s per step means this Mac is short of memory. Close the web app and "
                      "other heavy apps, make sure Ollama has unloaded its model (ollama stop qwen2.5vl:7b), and run "
                      "make codes-lora again.", flush=True)
                return 2
            if done <= 5 or done % 25 == 0 or done == total:
                left = (time.time() - t0) / done * (total - done)
                print(f"epoch {epoch + 1} step {done:4d}/{total} loss {loss.item():.4f}  {step_s:4.1f}s/step"
                      f"  about {left / 60:4.1f} min left", flush=True)

    model.eval()
    preds = {}
    todo = [e for e in ex if e.split in ("validation", "heldout")]
    with torch.no_grad():
        for i in range(0, len(todo), 32):
            chunk = todo[i:i + 32]
            enc = tok([chat(e.text) for e in chunk], return_tensors="pt", padding=True, add_special_tokens=False).to(dev)
            out = model.generate(**enc, max_new_tokens=6, do_sample=False, pad_token_id=tok.pad_token_id)
            for e, o in zip(chunk, out):
                preds[e.text] = llm.parse(tok.decode(o[enc["input_ids"].shape[1]:], skip_special_tokens=True))
    adapter = ROOT / "data" / "processed" / "codes" / ADAPTER_VERSION
    model.save_pretrained(adapter)
    digest = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(adapter.glob("*.safetensors")))).hexdigest()[:12]
    (ROOT / "local_runs").mkdir(exist_ok=True)
    (ROOT / "local_runs" / "codes_lora_predictions.json").write_text(json.dumps(
        {"base_model": BASE, "adapter_version": f"{ADAPTER_VERSION}@{digest}", "settings": SETTINGS,
         "data": data.DATA_VERSION, "device": dev, "train_seconds": round(time.time() - t0), "predictions": preds}))
    print(f"trained on {len(train)} examples in {time.time() - t0:.0f}s on {dev}; adapter {adapter.relative_to(ROOT)} ({digest})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
