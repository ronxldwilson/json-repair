from pydantic import BaseModel


class EventProperties(BaseModel):
    url: str | None = None
    referrer: str | None = None
    title: str | None = None
    duration_ms: int | None = None
    element: str | None = None
    action: str | None = None
    product_id: str | None = None
    quantity: int | None = None
    cart_total: float | None = None
    cart_items: int | None = None
    payment_method: str | None = None
    coupon_code: str | None = None
    order_id: str | None = None
    total: float | None = None
    tax: float | None = None
    shipping: float | None = None


class Event(BaseModel):
    timestamp: str
    type: str
    properties: EventProperties


class EventLog(BaseModel):
    session_id: str
    user_agent: str
    events: list[Event]
