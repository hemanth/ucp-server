"""Schema definitions for UCP merchant configuration."""

from typing import Optional
from pydantic import BaseModel, Field, HttpUrl

UCP_VERSION = "2026-01-11"


class Item(BaseModel):
    """Product/Item in the catalog."""
    id: str
    title: str
    description: Optional[str] = None
    price: int = Field(ge=0, description="Price in minor units (cents)")
    image_url: Optional[HttpUrl] = None
    sku: Optional[str] = None


class ShippingOption(BaseModel):
    """Shipping option configuration."""
    id: str
    title: str
    description: Optional[str] = None
    price: int = Field(ge=0, description="Price in minor units (cents)")
    estimated_days: Optional[str] = None


class PaymentHandler(BaseModel):
    """Payment handler configuration."""
    namespace: str  # e.g., "com.stripe", "com.google.pay"
    id: str
    config: Optional[dict] = None


class MerchantConfig(BaseModel):
    """Merchant configuration schema."""
    name: str
    domain: HttpUrl
    currency: str = "USD"
    terms_url: Optional[HttpUrl] = None
    privacy_url: Optional[HttpUrl] = None
    items: list[Item]
    shipping_options: list[ShippingOption] = []
    payment_handlers: list[PaymentHandler] = []
    tax_rate: float = Field(default=0.0, ge=0, le=1)
    port: int = 3000


# Checkout types
class Buyer(BaseModel):
    """Buyer information."""
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None


class Address(BaseModel):
    """Shipping/billing address."""
    id: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    street_address: str
    address_locality: str
    address_region: str
    postal_code: str
    address_country: str


class Total(BaseModel):
    """Price total entry."""
    type: str
    amount: int


class LineItemInput(BaseModel):
    """Line item in checkout request."""
    id: Optional[str] = None
    item: dict
    quantity: int


class FulfillmentDestination(BaseModel):
    """Fulfillment destination."""
    id: Optional[str] = None
    street_address: str
    address_locality: str
    address_region: str
    postal_code: str
    address_country: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class FulfillmentMethod(BaseModel):
    """Fulfillment method."""
    id: Optional[str] = None
    type: str = "shipping"
    line_item_ids: Optional[list[str]] = None
    selected_destination_id: Optional[str] = None
    destinations: list[FulfillmentDestination] = []
    groups: Optional[list[dict]] = None


class FulfillmentInput(BaseModel):
    """Fulfillment input."""
    methods: list[FulfillmentMethod]


class CheckoutUpdateInput(BaseModel):
    """Checkout update request."""
    buyer: Optional[Buyer] = None
    line_items: Optional[list[LineItemInput]] = None
    fulfillment: Optional[FulfillmentInput] = None
