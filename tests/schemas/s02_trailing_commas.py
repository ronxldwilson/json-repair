from pydantic import BaseModel


class Metadata(BaseModel):
    created: str
    updated: str


class TaggedItem(BaseModel):
    id: int
    tags: list[str]
    metadata: Metadata
