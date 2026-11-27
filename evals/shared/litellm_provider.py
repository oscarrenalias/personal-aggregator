import hashlib
import importlib
import json
import os
from pathlib import Path

from aggregator_common.env import load_env

load_env()

_CACHE_DIR = Path(__file__).parent.parent / ".cache" / "litellm"


def _resolve_response_format(import_path: str) -> type:
    """Resolve a dotted import path like 'module.submodule:ClassName' to the class."""
    if ":" not in import_path:
        raise ValueError(
            f"response_format import path must use 'module:ClassName' syntax, got: {import_path!r}"
        )
    module_path, class_name = import_path.split(":", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _cache_key(model: str, messages: list, params: dict, repeat_index: int, response_format_path: str | None = None) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "params": params,
        "repeat_index": repeat_index,
        "response_format_path": response_format_path,
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

    # response_format may be specified as a dotted import path string in config.
    # It is resolved at call time and passed to litellm, but stored as the path
    # string in the cache key so the cache remains serialisable.
    response_format_path: str | None = config.get("response_format")
    response_format_class = None
    if response_format_path:
        response_format_class = _resolve_response_format(response_format_path)

    repeat_index = (context or {}).get("repeatIndex", 0)
    messages = json.loads(prompt)
    key = _cache_key(model, messages, params, repeat_index, response_format_path)

    cached = _cache_read(key)
    if cached is not None:
        cached["cached"] = True
        return cached

    import litellm  # noqa: PLC0415 — lazy: only imported on actual API call (cache miss)
    try:
        call_kwargs = dict(params)
        if response_format_class is not None:
            call_kwargs["response_format"] = response_format_class
        response = litellm.completion(model=model, messages=messages, **call_kwargs)
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
