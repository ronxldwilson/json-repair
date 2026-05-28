from pydantic import BaseModel


class Dimensions(BaseModel):
    width: int
    height: int
    depth: int


class Widget(BaseModel):
    name: str
    dimensions: Dimensions
    weight: float
    color: str
