from pydantic import BaseModel


class ProductSpecs(BaseModel):
    cpu: str | None
    ram_gb: int | None
    storage: str | None


class Variant(BaseModel):
    color: str
    sku_suffix: str


class CatalogProduct(BaseModel):
    sku: str
    name: str
    brand: str
    price: float
    in_stock: bool
    specs: ProductSpecs
    variants: list[Variant]
    tags: list[str]


class StoreCatalog(BaseModel):
    store_id: str
    name: str
    products: list[CatalogProduct]
