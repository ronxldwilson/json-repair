from pydantic import BaseModel


class Address(BaseModel):
    street: str
    city: str
    state: str
    zip: str


class Customer(BaseModel):
    name: str
    email: str
    address: Address


class OrderItem(BaseModel):
    sku: str
    qty: int
    price: float


class Order(BaseModel):
    order_id: str
    customer: Customer
    items: list[OrderItem]
    total: float
