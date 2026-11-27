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

    # Rank eval: system prompt references RankResult schema
    is_rank_prompt = "RankResult" in system_content or "importance_score" in system_content

    if is_rank_prompt:
        vars_ = context.get("vars") or {}
        production_score = vars_.get("production_score") or 50
        title = vars_.get("title") or vars_.get("clean_title") or "(stub article)"
        return {
            "output": json.dumps({
                "summary": f"Stub summary for: {title}",
                "topics": ["stub-topic"],
                "importance_score": int(production_score),
                "importance_reason": "stub",
                "categories": [],
                "entities": ["StubEntity"],
            })
        }

    # Merge eval: check for same-story phrases
    has_same_story_phrase = (
        "same story" in system_content or "same underlying story" in system_content
    )

    thread_a_title = (context.get("vars") or {}).get("thread_a_title") or ""
    has_merge_prefix = str(thread_a_title).startswith("MERGE")

    same_story = has_same_story_phrase and has_merge_prefix

    return {"output": json.dumps({"same_story": same_story, "reason": "stub"})}
