"""ucp-server - Generate UCP-compliant servers for merchants."""

from ucp_server.schema import (
    MerchantConfig,
    Item,
    ShippingOption,
    PaymentHandler,
)
from ucp_server.server import UCPServer
from ucp_server.app import create_flask_app

__version__ = "1.0.0"
__all__ = [
    "MerchantConfig",
    "Item",
    "ShippingOption",
    "PaymentHandler",
    "UCPServer",
    "create_flask_app",
]
