from pydantic import BaseModel


class Project(BaseModel):
    name: str
    status: str
    priority: int


class Employee(BaseModel):
    name: str
    department: str
    salary: int
    is_manager: bool
    skills: list[str]
    projects: list[Project]


class EmployeeWrapper(BaseModel):
    employee: Employee
