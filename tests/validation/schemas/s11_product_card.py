from typing import Optional
from pydantic import BaseModel, Field


class ProductCard(BaseModel):
    vendor: Optional[str] = Field(None, description="Manufacturer or brand name")
    product: Optional[str] = Field(None, description="Full product name with brand and model number")
    specs: Optional[dict[str, str]] = Field(None, description="Technical specs as key-value pairs")
    description: Optional[str] = Field(None, description="2-3 sentence buyer-ready summary")
    price_range: Optional[str] = Field(None, description="Current Indian selling price")
    price_reasoning: Optional[str] = Field(None, description="How the price was selected")
    price_source_url: Optional[str] = Field(None, description="URL of the price source")
    availability: Optional[str] = Field(None, description="Stock status")
    warranty: Optional[str] = Field(None, description="Warranty period")
    moq: Optional[str] = Field(None, description="Minimum order quantity")
    delivery: Optional[str] = Field(None, description="Delivery timeline")
    certifications: Optional[list[str]] = Field(None, description="Product certifications")
    email: Optional[str] = Field(None, description="Contact email")
    phone: Optional[str] = Field(None, description="Contact phone")
    manufacturer_info: Optional[str] = Field(None, description="About the manufacturer")
    pros: list[str] = Field(default_factory=list, description="Buyer-relevant advantages")
    cons: list[str] = Field(default_factory=list, description="Honest limitations")
    score_spec_depth: int = Field(0, description="0-30 spec depth score")
    score_brand: int = Field(0, description="0-25 brand recognition score")
    matches_query: bool = Field(True, description="Whether product matches the query category")
    product_image_urls: list[str] = Field(default_factory=list, description="Product image URLs")
