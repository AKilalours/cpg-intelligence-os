"""One interface for every model provider, with record and replay.

Analogy: a court stenographer. Every question put to a model and every answer
it gave is written down. Later, tests and CI "replay the transcript" instead
of calling the model again: same answers, no Ollama running, no API key, no
cost, and a score that cannot drift because a model was updated overnight.

Modes (CPGOS_LLM_MODE):
- "auto" (default): use a recorded answer when one exists, otherwise call and record.
- "replay": never call a model; a missing recording is an error. CI uses this.
- "record": always call and overwrite the recording.

A recording is keyed by everything that can change the answer: provider,
model, the model's exact build (digest) when the provider reports one,
prompt, images, output schema, and sampling options. Images are identified
by a stable id (source document hash + how it was rendered) when the caller
gives one, because encoded bytes differ between machines for identical
pixels. Secrets never enter a key or a recording.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from cpgos import config

CASSETTE_ROOT = Path(__file__).resolve().parents[2] / "data" / "processed" / "llm_cassettes"


class LLMError(RuntimeError):
    pass


class ReplayMiss(LLMError):
    pass


@dataclass
class LLMRequest:
    system: str
    prompt: str
    images: list[bytes] = field(default_factory=list)          # PNG or JPEG bytes
    json_schema: dict | None = None
    temperature: float = 0.0
    max_tokens: int = 1024
    seed: int = 0
    # Stable identity of each image, e.g. "<source sha256>@png-x1.0". Recording keys use it
    # instead of the encoded bytes: PNG encoders on different machines write different bytes
    # for identical pixels, which made recordings impossible to replay anywhere else.
    # Last on purpose: adding it mid-list shifted every positional argument after it.
    image_ids: list[str] = field(default_factory=list)


@dataclass
class LLMResponse:
    text: str
    data: dict | None                    # parsed JSON when a schema was requested and the text parsed
    provider: str
    model: str
    model_digest: str | None
    input_tokens: int | None
    output_tokens: int | None
    seconds: float
    replayed: bool = False
    key: str = ""


class Provider(Protocol):
    name: str
    model: str

    def model_digest(self) -> str | None: ...

    def complete(self, req: LLMRequest) -> LLMResponse: ...


def request_key(provider: str, model: str, digest: str | None, req: LLMRequest) -> str:
    body = {"provider": provider, "model": model, "digest": digest, "system": req.system, "prompt": req.prompt,
            "images": req.image_ids or [hashlib.sha256(b).hexdigest() for b in req.images], "schema": req.json_schema,
            "temperature": req.temperature, "max_tokens": req.max_tokens, "seed": req.seed}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def parse_json(text: str) -> dict | None:
    """The JSON object in a model's text: whole text, or the first {...} block (models sometimes add prose or fences)."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{"):] if "{" in t else t
    for candidate in (t, t[t.find("{"): t.rfind("}") + 1] if "{" in t and "}" in t else ""):
        if not candidate:
            continue
        try:
            v = json.loads(candidate)
            return v if isinstance(v, dict) else None
        except json.JSONDecodeError:
            continue
    return None


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


class Recorder:
    """Wraps a provider with record/replay."""

    def __init__(self, provider: Provider, mode: str | None = None, root: Path | None = None):
        self.p = provider
        self.mode = mode or config.get("CPGOS_LLM_MODE", "auto")
        if self.mode not in ("auto", "replay", "record"):
            raise ValueError(f"CPGOS_LLM_MODE must be auto, replay, or record, not {self.mode!r}")
        self.root = root or CASSETTE_ROOT
        self._digest: str | None = None
        self._digest_known = False

    @property
    def name(self) -> str:
        return self.p.name

    @property
    def model(self) -> str:
        return self.p.model

    def digest(self) -> str | None:
        if not self._digest_known:
            # In replay mode the provider may be unreachable; recordings then match on model name.
            self._digest = None if self.mode == "replay" else self.p.model_digest()
            self._digest_known = True
        return self._digest

    def _path(self, key: str) -> Path:
        return self.root / self.p.name / self.p.model.replace("/", "_").replace(":", "_") / key[:2] / f"{key}.json"

    def _find(self, req: LLMRequest) -> tuple[str, Path | None]:
        key = request_key(self.p.name, self.p.model, self.digest(), req)
        path = self._path(key)
        if path.is_file():
            return key, path
        if self.mode == "replay":
            # Without the live digest, accept a recording of the same request from any build of this model.
            idx = self.root / self.p.name / self.p.model.replace("/", "_").replace(":", "_") / "index.json"
            if idx.is_file():
                alias = json.loads(idx.read_text()).get(request_key(self.p.name, self.p.model, None, req))
                if alias and self._path(alias).is_file():
                    return alias, self._path(alias)
        return key, None

    def complete(self, req: LLMRequest) -> LLMResponse:
        key, path = self._find(req)
        if path is not None and self.mode != "record":
            r = LLMResponse(**json.loads(path.read_text())["response"])
            r.replayed, r.key = True, key
            return r
        if self.mode == "replay":
            raise ReplayMiss(f"no recording for {self.p.name}/{self.p.model} request {key[:12]}; "
                             "run once with CPGOS_LLM_MODE=auto on a machine with the model")
        r = self.p.complete(req)
        r.key = key
        out = self._path(key)
        out.parent.mkdir(parents=True, exist_ok=True)
        rec = {"request": {"system": req.system, "prompt": req.prompt, "schema": req.json_schema,
                           "images_sha256": [hashlib.sha256(b).hexdigest() for b in req.images],
                           "image_ids": req.image_ids,
                           "temperature": req.temperature, "max_tokens": req.max_tokens, "seed": req.seed},
               "response": {k: v for k, v in asdict(r).items() if k not in ("replayed", "key")},
               "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, indent=1))
        tmp.replace(out)
        idx = out.parents[1] / "index.json"
        index = json.loads(idx.read_text()) if idx.is_file() else {}
        index[request_key(self.p.name, self.p.model, None, req)] = key
        idx.write_text(json.dumps(index))
        return r


def env_flag(name: str) -> str | None:
    return os.environ.get(name) or config.get(name)
