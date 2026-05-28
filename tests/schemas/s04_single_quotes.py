from pydantic import BaseModel


class Product(BaseModel):
    product: str
    price: float
    in_stock: bool
    categories: list[str]
