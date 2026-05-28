from pydantic import BaseModel


class PersonInfo(BaseModel):
    name: str
    age: int
    city: str
    employed: bool
