import json

import litellm

from aggregator_common.env import load_env

load_env()


def call_api(prompt: str, options: dict, context: dict) -> dict:
    config = options.get("config", {})
    model = config["model"]
    params = dict(config.get("params", {}))

    if "reasoning_effort" in params:
        params["allowed_openai_params"] = ["reasoning_effort"]

    try:
        messages = json.loads(prompt)
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
        return result
    except Exception as exc:
        return {"error": str(exc)}
