from pydantic import BaseModel


class Student(BaseModel):
    id: int
    name: str
    score: int


class StudentList(BaseModel):
    students: list[Student]
