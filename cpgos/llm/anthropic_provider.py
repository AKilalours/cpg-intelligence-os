"""Claude through the Anthropic Messages API.

The API key is read from ANTHROPIC_API_KEY (environment or .env) at call time,
sent only in the request header, and never logged, recorded, or put in an
error message.
"""

from __future__ import annotations

import time

import httpx

from cpgos import config
from cpgos.llm.base import LLMError, LLMRequest, LLMResponse, b64, parse_json

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5-5"


def _media_type(data: bytes) -> str:
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


class Anthropic:
    name = "anthropic"

    def __init__(self, model: str | None = None, timeout: float = 120.0):
        self.model = model or config.get("CPGOS_CLAUDE_MODEL", DEFAULT_MODEL)
        self.timeout = timeout

    def model_digest(self) -> str | None:
        return None            # the API model id already names an exact snapshot

    def complete(self, req: LLMRequest) -> LLMResponse:
        key = config.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMError("ANTHROPIC_API_KEY is not set. Put it in .env (never in code or chat).")
        content = [{"type": "image", "source": {"type": "base64", "media_type": _media_type(i), "data": b64(i)}}
                   for i in req.images]
        prompt = req.prompt
        if req.json_schema:
            import json
            prompt += "\n\nReply with only a JSON object matching this schema, no prose:\n" + json.dumps(req.json_schema)
        content.append({"type": "text", "text": prompt})
        body = {"model": self.model, "max_tokens": req.max_tokens, "temperature": req.temperature,
                "messages": [{"role": "user", "content": content}]}
        if req.system:
            body["system"] = req.system
        t = time.time()
        try:
            r = httpx.post(API_URL, json=body, timeout=self.timeout,
                           headers={"x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json"})
        except httpx.HTTPError as e:
            raise LLMError(f"Anthropic request failed: {e.__class__.__name__}") from None
        if r.status_code != 200:
            raise LLMError(f"Anthropic returned {r.status_code}: {r.text[:300]}")
        out = r.json()
        text = "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
        usage = out.get("usage", {})
        return LLMResponse(text=text, data=parse_json(text) if req.json_schema else None, provider=self.name,
                           model=out.get("model", self.model), model_digest=None,
                           input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
                           seconds=round(time.time() - t, 3))
