from pydantic import BaseModel, field_validator, model_validator

PROMPT_VERSION = "1.3.0"
PROMPT_VERSION_V2 = "1.4.0"
PROMPT_VERSION_V3 = "1.5.1"


class RankResult(BaseModel):
    summary: str
    topics: list[str]
    importance_score: int
    importance_reason: str
    categories: list[str] = []
    entities: list[str] = []

    @field_validator("importance_score", mode="before")
    @classmethod
    def clamp_importance_score(cls, v: int) -> int:
        return max(0, min(100, int(v)))

    @model_validator(mode="after")
    def truncate_topics(self) -> "RankResult":
        if len(self.topics) > 5:
            self.topics = self.topics[:5]
        return self

    @model_validator(mode="after")
    def normalize_entities(self) -> "RankResult":
        seen: dict[str, str] = {}
        for e in self.entities:
            stripped = e.strip()
            if not stripped:
                continue
            key = stripped.casefold()
            if key not in seen:
                seen[key] = stripped
        self.entities = list(seen.values())[:6]
        return self
