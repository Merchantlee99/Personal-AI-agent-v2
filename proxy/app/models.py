from pydantic import AliasChoices, BaseModel, Field


class HistoryMessage(BaseModel):
    role: str
    text: str = Field(validation_alias=AliasChoices("text", "content"))
    at: str | None = None


class AgentRequest(BaseModel):
    agent_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    history: list[HistoryMessage] = Field(default_factory=list)
    memory_context: str | None = Field(default=None, max_length=4000)
    source: str = "web"


class AgentResponse(BaseModel):
    agent_id: str
    model: str
    reply: str
    role_boundary: str


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    max_results: int = Field(default=5, ge=1, le=10)


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str


class SearchResponse(BaseModel):
    query: str
    treated_as_data: bool = True
    provider: str = "mock"
    filter_stats: dict[str, int] = Field(default_factory=dict)
    results: list[SearchResult]
