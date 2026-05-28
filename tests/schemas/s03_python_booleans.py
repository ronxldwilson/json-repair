from pydantic import BaseModel


class UserFlags(BaseModel):
    username: str
    is_active: bool
    is_admin: bool
    profile: dict | None
