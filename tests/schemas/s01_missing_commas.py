from pydantic import BaseModel


class UserScore(BaseModel):
    id: int
    name: str
    email: str
    score: float
