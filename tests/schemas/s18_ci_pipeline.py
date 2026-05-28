from pydantic import BaseModel


class RetryConfig(BaseModel):
    max_attempts: int
    delay_seconds: int


class Artifacts(BaseModel):
    paths: list[str]
    expire_in: str


class Job(BaseModel):
    name: str
    image: str
    commands: list[str]
    timeout_minutes: int
    retry: RetryConfig
    artifacts: Artifacts | None


class Stage(BaseModel):
    name: str
    jobs: list[Job]


class Trigger(BaseModel):
    branch: str
    events: list[str]


class Notifications(BaseModel):
    on_success: list[str]
    on_failure: list[str]


class Pipeline(BaseModel):
    name: str
    trigger: Trigger
    stages: list[Stage]
    notifications: Notifications


class PipelineConfig(BaseModel):
    pipeline: Pipeline
