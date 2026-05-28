from pydantic import BaseModel


class DbConfig(BaseModel):
    host: str
    port: int
    database: str
    username: str
    password: str
    ssl: bool
    pool_size: int
