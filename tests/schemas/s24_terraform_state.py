from pydantic import BaseModel
from typing import Any


class ResourceInstance(BaseModel):
    attributes: dict[str, Any]
    private: str | None


class Resource(BaseModel):
    mode: str
    type: str
    name: str
    provider: str
    instances: list[ResourceInstance]


class TerraformState(BaseModel):
    version: int
    serial: int
    lineage: str
    resources: list[Resource]
