from pydantic import BaseModel


class RestaurantAddress(BaseModel):
    street: str
    city: str
    state: str
    zip: str


class RestaurantInfo(BaseModel):
    name: str
    cuisine: str
    rating: float
    address: RestaurantAddress


class MenuItem(BaseModel):
    name: str
    price: float
    description: str
    vegetarian: bool
    spicy: bool
    allergens: list[str]


class MenuCategory(BaseModel):
    name: str
    items: list[MenuItem]


class Menu(BaseModel):
    categories: list[MenuCategory]


class RestaurantMenu(BaseModel):
    restaurant: RestaurantInfo
    menu: Menu
