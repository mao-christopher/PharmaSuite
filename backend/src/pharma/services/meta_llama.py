"""Minimal client for Meta's Llama API (https://api.llama.com), used for structured JSON answers.

The token comes from the environment only: `META_API_KEY`, or `LLAMA_API_KEY` as Meta's
own SDK names it. Nothing here stores or logs the token. Callers must validate every
answer: a model can return well-formed JSON that is still wrong.
"""

import json
import os
from typing import Any, Dict, List, Optional

import httpx

DEFAULT_BASE_URL = "https://api.llama.com/v1"
DEFAULT_MODEL = "Llama-4-Maverick-17B-128E-Instruct-FP8"
TIMEOUT_S = 60.0


class MetaApiError(RuntimeError):
    """The Llama API could not produce a usable answer (no token, HTTP error, or bad JSON)."""


def api_key() -> Optional[str]:
    return (os.getenv("META_API_KEY") or os.getenv("LLAMA_API_KEY") or "").strip() or None


def model_name() -> str:
    return os.getenv("META_MODEL") or DEFAULT_MODEL


def status() -> Dict[str, Any]:
    """Whether a token is configured, for the dashboard. Never includes the token."""
    return {"configured": api_key() is not None, "model": model_name()}


def _message_text(body: Dict[str, Any]) -> str:
    # Native Llama API shape, with the OpenAI-compatible shape as a fallback.
    message = body.get("completion_message") or (body.get("choices") or [{}])[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, dict):
        content = content.get("text")
    if not isinstance(content, str) or not content.strip():
        raise MetaApiError(f"The Llama API returned no text (stop reason: {message.get('stop_reason')}).")
    return content


def structured_chat(messages: List[Dict[str, str]], schema: Dict[str, Any], schema_name: str,
                    temperature: float = 0.2, max_tokens: int = 4000,
                    client: Optional[httpx.Client] = None) -> Dict[str, Any]:
    """Ask the model for a JSON object matching `schema` and return it parsed."""
    key = api_key()
    if key is None:
        raise MetaApiError("No Meta API token configured. Set META_API_KEY in backend/.env.")
    payload = {
        "model": model_name(),
        "messages": messages,
        "temperature": temperature,
        "max_completion_tokens": max_tokens,
        "response_format": {"type": "json_schema", "json_schema": {"name": schema_name, "schema": schema}},
    }
    base_url = (os.getenv("META_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    owned = client is None
    http = client or httpx.Client(timeout=TIMEOUT_S)
    try:
        res = http.post(f"{base_url}/chat/completions", json=payload,
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    except httpx.HTTPError as exc:
        raise MetaApiError(f"Could not reach the Llama API: {exc.__class__.__name__}.") from exc
    finally:
        if owned:
            http.close()
    if res.status_code == 401 or res.status_code == 403:
        raise MetaApiError("The Llama API rejected the token. Check META_API_KEY.")
    if res.status_code >= 400:
        raise MetaApiError(f"The Llama API returned HTTP {res.status_code}.")
    try:
        text = _message_text(res.json())
        answer = json.loads(text)
    except ValueError as exc:
        raise MetaApiError("The Llama API answer was not valid JSON.") from exc
    if not isinstance(answer, dict):
        raise MetaApiError("The Llama API answer was not a JSON object.")
    return answer
