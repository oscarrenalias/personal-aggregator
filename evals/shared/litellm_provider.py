import hashlib
import json
import os
from pathlib import Path

import litellm

from aggregator_common.env import load_env

load_env()

_CACHE_DIR = Path(__file__).parent.parent / ".cache" / "litellm"


def _cache_key(model: str, messages: list, params: dict, repeat_index: int) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "params": params,
        "repeat_index": repeat_index,
    }
    canon = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode()).hexdigest()


def _cache_read(key: str) -> dict | None:
    if os.environ.get("EVAL_NO_CACHE"):
        return None
    path = _CACHE_DIR / f"{key}.json"
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _cache_write(key: str, result: dict) -> None:
    if os.environ.get("EVAL_NO_CACHE"):
        return
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _CACHE_DIR / f"{key}.json"
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(result, f)
        tmp.rename(path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def call_api(prompt: str, options: dict, context: dict) -> dict:
    config = options.get("config", {})
    model = config["model"]
    params = dict(config.get("params", {}))

    if "reasoning_effort" in params:
        params["allowed_openai_params"] = ["reasoning_effort"]

    repeat_index = (context or {}).get("repeatIndex", 0)
    messages = json.loads(prompt)
    key = _cache_key(model, messages, params, repeat_index)

    cached = _cache_read(key)
    if cached is not None:
        cached["cached"] = True
        return cached

    try:
        response = litellm.completion(model=model, messages=messages, **params)
        output = response.choices[0].message.content
        usage = response.usage
        result: dict = {
            "output": output,
            "tokenUsage": {
                "prompt": usage.prompt_tokens,
                "completion": usage.completion_tokens,
                "total": usage.total_tokens,
            },
        }
        try:
            result["cost"] = litellm.completion_cost(completion_response=response)
        except Exception:
            pass
        _cache_write(key, result)
        return result
    except Exception as exc:
        return {"error": str(exc)}
