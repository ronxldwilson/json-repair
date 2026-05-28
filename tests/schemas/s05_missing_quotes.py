from pydantic import BaseModel


class Report(BaseModel):
    title: str
    author: str
    pages: int
    summary: str
