"""LLM access: Gemini (REST) and any OpenAI-compatible API, both in JSON mode."""
import json
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from app.config import Settings, get_settings


class LLMError(Exception):
    pass


@dataclass
class LLMResult:
    data: dict
    provider: str
    model: str
    seconds: float
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[4:] if text.lower().startswith("json") else text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    raise LLMError("LLM did not return valid JSON")


def _call_gemini(system: str, user: str, s: Settings, client: httpx.Client) -> LLMResult:
    if not s.gemini_api_key:
        raise LLMError("GEMINI_API_KEY is not configured")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{s.gemini_model}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3},
    }
    t0 = time.perf_counter()
    r = client.post(url, json=body, headers={"x-goog-api-key": s.gemini_api_key})
    dt = time.perf_counter() - t0
    if r.status_code != 200:
        raise LLMError(f"Gemini API error {r.status_code}")
    j = r.json()
    try:
        text = "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError) as exc:
        raise LLMError("Gemini returned no content") from exc
    usage = j.get("usageMetadata", {})
    return LLMResult(_parse_json(text), "gemini", s.gemini_model, dt,
                     usage.get("promptTokenCount"), usage.get("candidatesTokenCount"))


def _call_openai(system: str, user: str, s: Settings, client: httpx.Client) -> LLMResult:
    if not s.openai_api_key:
        raise LLMError("OPENAI_API_KEY is not configured")
    body = {
        "model": s.openai_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "temperature": 0.3,
    }
    t0 = time.perf_counter()
    r = client.post(s.openai_base_url.rstrip("/") + "/chat/completions", json=body,
                    headers={"Authorization": f"Bearer {s.openai_api_key}"})
    dt = time.perf_counter() - t0
    if r.status_code != 200:
        raise LLMError(f"OpenAI-compatible API error {r.status_code}")
    j = r.json()
    try:
        text = j["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMError("LLM returned no content") from exc
    usage = j.get("usage", {})
    return LLMResult(_parse_json(text), "openai", s.openai_model, dt,
                     usage.get("prompt_tokens"), usage.get("completion_tokens"))


PROVIDERS = {"gemini": _call_gemini, "openai": _call_openai}


def generate_json(system: str, user: str, settings: Optional[Settings] = None,
                  client: Optional[httpx.Client] = None) -> LLMResult:
    """Call the primary provider, then the fallback provider if configured."""
    s = settings or get_settings()
    order = [s.llm_provider]
    if s.fallback_provider and s.fallback_provider != s.llm_provider:
        order.append(s.fallback_provider)
    own = client is None
    client = client or httpx.Client(timeout=60)
    last: Optional[Exception] = None
    try:
        for name in order:
            fn = PROVIDERS.get(name)
            if not fn:
                last = LLMError(f"Unknown LLM provider: {name}")
                continue
            try:
                return fn(system, user, s, client)
            except (LLMError, httpx.HTTPError) as exc:
                last = exc
        raise LLMError(str(last) if last else "No LLM provider configured")
    finally:
        if own:
            client.close()
