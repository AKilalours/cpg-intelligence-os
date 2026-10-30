"""Ollama: local models on this machine. Nothing leaves the computer; no per-call cost."""

from __future__ import annotations

import json
import time

import httpx

from cpgos import config
from cpgos.llm.base import LLMError, LLMRequest, LLMResponse, b64, parse_json

DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5vl:7b"


class Ollama:
    name = "ollama"

    def __init__(self, model: str | None = None, url: str | None = None, timeout: float = 300.0):
        self.model = model or config.get("CPGOS_OLLAMA_MODEL", DEFAULT_MODEL)
        self.url = (url or config.get("CPGOS_OLLAMA_URL", DEFAULT_URL)).rstrip("/")
        self.timeout = timeout
        self._digest: str | None = None

    def _get(self, path: str) -> dict:
        try:
            r = httpx.get(self.url + path, timeout=10)
        except httpx.HTTPError as e:
            raise LLMError(f"Ollama is not reachable at {self.url} ({e.__class__.__name__}). Start it: ollama serve") from e
        r.raise_for_status()
        return r.json()

    def model_digest(self) -> str | None:
        """The exact build of the model, so a re-pulled model never reuses old recordings."""
        if self._digest:
            return self._digest
        for m in self._get("/api/tags").get("models", []):
            if m.get("name") == self.model or m.get("model") == self.model:
                self._digest = m.get("digest")
                return self._digest
        raise LLMError(f"Ollama has no model {self.model!r}. Pull it: ollama pull {self.model}")

    def complete(self, req: LLMRequest) -> LLMResponse:
        msgs = []
        if req.system:
            msgs.append({"role": "system", "content": req.system})
        user = {"role": "user", "content": req.prompt}
        if req.images:
            user["images"] = [b64(i) for i in req.images]
        msgs.append(user)
        body = {"model": self.model, "messages": msgs, "stream": False, "keep_alive": "10m",
                "options": {"temperature": req.temperature, "seed": req.seed, "num_predict": req.max_tokens,
                            "num_ctx": 8192}}
        if req.json_schema:
            body["format"] = req.json_schema          # Ollama constrains decoding to this JSON schema
        t = time.time()
        try:
            r = httpx.post(self.url + "/api/chat", json=body, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise LLMError(f"Ollama request failed: {e.__class__.__name__}: {e}") from e
        if r.status_code != 200:
            raise LLMError(f"Ollama returned {r.status_code}: {r.text[:300]}")
        out = r.json()
        text = out.get("message", {}).get("content", "")
        return LLMResponse(text=text, data=parse_json(text) if req.json_schema else None, provider=self.name,
                           model=self.model, model_digest=self.model_digest(), input_tokens=out.get("prompt_eval_count"),
                           output_tokens=out.get("eval_count"), seconds=round(time.time() - t, 3))
