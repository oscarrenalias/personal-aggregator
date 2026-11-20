import json


def call_api(prompt: str, options: dict, context: dict) -> dict:
    try:
        messages = json.loads(prompt)
    except (json.JSONDecodeError, TypeError):
        messages = []

    system_content = ""
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "system":
            system_content = msg.get("content", "") or ""
            break

    has_same_story_phrase = (
        "same story" in system_content or "same underlying story" in system_content
    )

    thread_a_title = (context.get("vars") or {}).get("thread_a_title") or ""
    has_merge_prefix = str(thread_a_title).startswith("MERGE")

    same_story = has_same_story_phrase and has_merge_prefix

    return {"output": json.dumps({"same_story": same_story, "reason": "stub"})}
