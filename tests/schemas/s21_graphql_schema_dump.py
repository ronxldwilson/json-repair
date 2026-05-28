from pydantic import BaseModel


class Field(BaseModel):
    name: str
    type: str
    description: str | None


class TypeDef(BaseModel):
    name: str
    kind: str
    fields: list[Field]


class QueryArg(BaseModel):
    name: str
    type: str


class Query(BaseModel):
    name: str
    args: list[QueryArg]
    returns: str


class Schema(BaseModel):
    types: list[TypeDef]
    queries: list[Query]


class GraphQLDump(BaseModel):
    schema_: Schema

    class Config:
        fields = {"schema_": "schema"}
