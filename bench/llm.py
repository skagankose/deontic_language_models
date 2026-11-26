"""Minimal OpenAI chat-completions client (stdlib only) with on-disk caching and retries.

Key lookup: $OPENAI_API_KEY, else OPENAI_API_KEY / OPEN_AI_KEY in project/.env or
project/data/option_a/.env. The key is only ever sent to api.openai.com.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-6-luna"          # the only model used for LLM calls in this project (user choice, 2026-11-25)
# Paper, Table 5: temperature 0.01, top_p 0.01, seed 42, max_tokens 4096. JSON mode added.
PAPER_DECODING = dict(temperature=0.01, top_p=0.01, seed=42, max_tokens=4096, response_format={"type": "json_object"})
# gpt-6-luna is a reasoning model: it rejects max_tokens and top_p and only accepts the default
# temperature (1). Probed 2026-11-25. Budget leaves room for reasoning tokens.
REASONING_DECODING = dict(seed=42, max_completion_tokens=32000, response_format={"type": "json_object"})


def decoding_for(model: str) -> dict:
    return PAPER_DECODING if model.startswith(("gpt-4", "gpt-3.5")) else REASONING_DECODING


DECODING = decoding_for(MODEL)


def api_key() -> str:
    key = os.getenv("OPENAI_API_KEY")
    for env in (PROJECT / ".env", PROJECT / "data/option_a/.env"):
        if key:
            break
        if env.exists():
            for line in env.read_text().splitlines():
                name, _, value = line.partition("=")
                if name.strip() in ("OPENAI_API_KEY", "OPEN_AI_KEY") and value.strip():
                    key = value.strip().strip('"').strip("'")
    if not key:
        raise SystemExit("No OpenAI key: set OPENAI_API_KEY or add it to project/.env")
    return key


def chat(model: str, system: str, user: str, *, cache_dir: Path | None = None, tag: str = "",
         decoding: dict | None = None, retries: int = 5) -> dict:
    """Return the raw API response (cached by request hash when cache_dir is given)."""
    body = {"model": model, "messages": [{"role": "system", "content": system},
                                         {"role": "user", "content": user}], **(decoding or decoding_for(model))}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
    path = cache_dir / f"{tag}{'-' if tag else ''}{digest}.json" if cache_dir else None
    if path and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    req = urllib.request.Request(URL, json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {api_key()}"})
    delay = 2.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                out = json.load(resp)
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(f"OpenAI HTTP {e.code}: {e.read()[:300]!r}") from None
        except (urllib.error.URLError, TimeoutError):
            if attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    out["_request_digest"] = digest
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    return out


def content_json(response: dict) -> dict:
    """Parse the JSON object in a chat response; tolerate ``` fences. Raises ValueError."""
    text = response["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError("response is not a JSON object")
    return obj


def usage(responses) -> dict:
    p = sum(r.get("usage", {}).get("prompt_tokens", 0) for r in responses)
    c = sum(r.get("usage", {}).get("completion_tokens", 0) for r in responses)
    rt = sum((r.get("usage", {}).get("completion_tokens_details") or {}).get("reasoning_tokens", 0) for r in responses)
    return {"calls": len(responses), "prompt_tokens": p, "completion_tokens": c, "reasoning_tokens": rt}
