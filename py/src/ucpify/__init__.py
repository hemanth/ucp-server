"""ucpify - Generate UCP-compliant servers for merchants."""

from ucpify.schema import (
    MerchantConfig,
    Item,
    ShippingOption,
    PaymentHandler,
)
from ucpify.server import UCPServer
from ucpify.app import create_flask_app

__version__ = "1.0.0"
__all__ = [
    "MerchantConfig",
    "Item",
    "ShippingOption",
    "PaymentHandler",
    "UCPServer",
    "create_flask_app",
]
