from pydantic import BaseModel, field_validator, model_validator

PROMPT_VERSION = "1.3.0"


class RankResult(BaseModel):
    summary: str
    topics: list[str]
    importance_score: int
    importance_reason: str
    categories: list[str] = []

    @field_validator("importance_score", mode="before")
    @classmethod
    def clamp_importance_score(cls, v: int) -> int:
        return max(0, min(100, int(v)))

    @model_validator(mode="after")
    def truncate_topics(self) -> "RankResult":
        if len(self.topics) > 5:
            self.topics = self.topics[:5]
        return self
