"""
V9 Gateway client for the computer-use Session 9 skill.

All LLM and vision calls route through the LLM Gateway V9 (port 8109).
No paid third-party APIs.  No external agentic frameworks.

Endpoints used:
  POST /v1/chat    — text LLM calls (Layer 2b tree parsing, verification)
  POST /v1/vision  — multimodal vision calls (Layer 3 perception)

The /v1/vision endpoint is V9-only and accepts:
  {
    "image":    "<data: URL or http URL>",
    "prompt":   "<instruction to the vision model>",
    "schema":   {<JSON Schema for structured output>},  # optional
    "provider": "<provider shortcut>",                  # optional
    "agent":    "<skill name>",
    "session":  "<session id>"
  }
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, Optional

import httpx

from .config import GATEWAY_URL

logger = logging.getLogger("gateway_client")


class GatewayClient:
    """Sync HTTP client for LLM Gateway V9."""

    def __init__(self, base_url: str = GATEWAY_URL, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.timeout  = timeout

    # ── Text LLM ─────────────────────────────────────────────────────────────

    def chat(
        self,
        prompt: str,
        *,
        system: str = None,
        provider: str = None,
        model: str = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        auto_route: str = None,
        agent: str = "computer_use",
        session: str = None,
        response_format: dict = None,
    ) -> str:
        """
        Single-turn text chat.  Returns the text field of the response.
        Used for Layer 2b AX-tree parsing and post-action verification.
        """
        body: dict[str, Any] = {
            "prompt":      prompt,
            "max_tokens":  max_tokens,
            "temperature": temperature,
            "agent":       agent,
        }
        if system:         body["system"]          = system
        if provider:       body["provider"]        = provider
        if model:          body["model"]           = model
        if auto_route:     body["auto_route"]      = auto_route
        if session:        body["session"]         = session
        if response_format: body["response_format"] = response_format

        logger.debug(f"[gateway] POST /v1/chat  agent={agent}  tokens~={len(prompt.split())}")
        r = httpx.post(
            f"{self.base_url}/v1/chat",
            json=body,
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        logger.debug(f"[gateway] /v1/chat  provider={data.get('provider')}  latency={data.get('latency_ms')}ms")
        return data.get("text", "")

    def chat_structured(
        self,
        prompt: str,
        schema: dict,
        schema_name: str = "out",
        **kw,
    ) -> dict:
        """
        Chat with JSON schema enforcement.  Returns the `parsed` dict.
        Useful for extracting typed data (pixel coordinates, verification
        verdicts) without regex fragility.
        """
        body_rf = {"type": "json_schema", "schema": schema, "name": schema_name, "strict": True}
        text = self.chat(prompt, response_format=body_rf, **kw)
        import json
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            import re
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                return json.loads(m.group())
            return {"raw": text}

    # ── Vision LLM ───────────────────────────────────────────────────────────

    def vision(
        self,
        image_source: str,
        prompt: str,
        *,
        system: str = None,
        schema: dict = None,
        schema_name: str = "out",
        provider: str = None,
        model: str = None,
        max_tokens: int = 1024,
        agent: str = "computer_use_vision",
        session: str = None,
    ) -> dict:
        """
        Vision call via POST /v1/vision (V9 only).

        `image_source` accepts:
          - Local file path   → auto-encoded to data:image/png;base64,...
          - data: URL         → passed as-is
          - http(s) URL       → passed as-is (gateway resolves it)

        Returns the full response dict from the gateway (same shape as
        /v1/chat: {text, provider, model, latency_ms, parsed, ...}).
        """
        image = _resolve_image(image_source)

        body: dict[str, Any] = {
            "image":      image,
            "prompt":     prompt,
            "max_tokens": max_tokens,
            "temperature": 0.0,   # deterministic for coordinate extraction
            "agent":      agent,
        }
        if system:      body["system"]       = system
        if schema:      body["schema"]       = schema
        if schema_name: body["schema_name"]  = schema_name
        if provider:    body["provider"]     = provider
        if model:       body["model"]        = model
        if session:     body["session"]      = session

        logger.info(
            f"[gateway] POST /v1/vision  agent={agent}  "
            f"image_source={image_source[:60]}…  prompt={prompt[:80]}…"
        )
        r = httpx.post(
            f"{self.base_url}/v1/vision",
            json=body,
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        logger.info(
            f"[gateway] /v1/vision  provider={data.get('provider')}  "
            f"latency={data.get('latency_ms')}ms  text={data.get('text','')[:120]}"
        )
        return data

    def vision_text(self, image_source: str, prompt: str, **kw) -> str:
        """Convenience wrapper — returns just the text field."""
        return self.vision(image_source, prompt, **kw).get("text", "")

    def vision_structured(
        self,
        image_source: str,
        prompt: str,
        schema: dict,
        schema_name: str = "out",
        **kw,
    ) -> dict:
        """
        Vision call with structured JSON output.
        Returns the `parsed` dict if the gateway populated it, otherwise
        falls back to JSON-parsing the `text` field.
        """
        resp = self.vision(
            image_source,
            prompt,
            schema=schema,
            schema_name=schema_name,
            **kw,
        )
        if resp.get("parsed"):
            return resp["parsed"]
        # Fallback: parse text
        import json, re
        text = resp.get("text", "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group())
                except json.JSONDecodeError:
                    pass
        return {"raw": text}

    # ── Health check ──────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Return True if the V9 gateway is reachable."""
        try:
            r = httpx.get(f"{self.base_url}/v1/status", timeout=5.0)
            return r.status_code == 200
        except Exception:
            return False

    def assert_available(self) -> None:
        if not self.is_available():
            raise RuntimeError(
                f"LLM Gateway V9 not reachable at {self.base_url}. "
                "Start it with: cd llm_gatewayV9 && ./run.sh"
            )


# ── Image resolution helper ───────────────────────────────────────────────────

def _resolve_image(source: str) -> str:
    """
    Convert a local file path to a data: URL.
    Pass data: URLs and http(s) URLs through unchanged.
    """
    if source.startswith("data:") or source.startswith("http"):
        return source
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Screenshot not found: {source}")
    mime = _guess_mime(path)
    raw  = path.read_bytes()
    b64  = base64.b64encode(raw).decode()
    return f"data:{mime};base64,{b64}"


def _guess_mime(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    return {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
        ext, "image/png"
    )
