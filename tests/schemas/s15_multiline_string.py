from pydantic import BaseModel


class CodeDoc(BaseModel):
    description: str
    code: str
    valid_field: bool
