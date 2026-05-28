from pydantic import BaseModel


class Address(BaseModel):
    street: str
    city: str
    zip: int


class Person(BaseModel):
    name: str
    age: int
    email: str
    address: Address
    hobbies: list[str]
    active: bool
