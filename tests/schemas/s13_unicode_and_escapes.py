from pydantic import BaseModel


class TextData(BaseModel):
    greeting: str
    path: str
    quote: str
    emoji: str
    tab_data: str
