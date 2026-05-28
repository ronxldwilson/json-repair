from pydantic import BaseModel


class TeamMember(BaseModel):
    name: str
    role: str


class Team(BaseModel):
    name: str
    lead: str
    members: list[TeamMember]


class Department(BaseModel):
    name: str
    teams: list[Team]


class Company(BaseModel):
    name: str
    departments: list[Department]


class CompanyWrapper(BaseModel):
    company: Company
