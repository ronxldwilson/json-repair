from pydantic import BaseModel


class NotificationPrefs(BaseModel):
    email: bool
    sms: bool
    push: bool


class UserPreferences(BaseModel):
    theme: str
    language: str
    notifications: NotificationPrefs


class User(BaseModel):
    id: int
    username: str
    email: str
    roles: list[str]
    preferences: UserPreferences
    last_login: str


class Permission(BaseModel):
    resource: str
    actions: list[str]


class ResponseData(BaseModel):
    user: User
    permissions: list[Permission]


class ResponseMeta(BaseModel):
    request_id: str
    timestamp: str
    version: str


class ApiResponse(BaseModel):
    status: str
    data: ResponseData
    meta: ResponseMeta
