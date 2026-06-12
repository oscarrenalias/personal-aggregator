from pydantic import BaseModel, Field


class BriefReferenceSchema(BaseModel):
    article_id: int | None = None
    title: str
    url: str | None = None
    internal: bool = False


class BriefTopicSchema(BaseModel):
    headline: str
    what_happened: str
    why_it_matters: str
    historical_context: str | None = None
    references: list[BriefReferenceSchema] = Field(default_factory=list)


class BriefSubmitSchema(BaseModel):
    headline: str
    intro: str
    topics: list[BriefTopicSchema]
