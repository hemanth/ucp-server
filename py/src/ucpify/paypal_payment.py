"""PayPal payment integration."""

from typing import Optional
from paypalserversdk import (
    Client,
    Environment,
    OrdersController,
    OrderRequest,
    CheckoutPaymentIntent,
    PurchaseUnitRequest,
    AmountWithBreakdown,
)

from .config import config
from .logger import logger

_paypal_client: Optional[Client] = None
_orders_controller: Optional[OrdersController] = None


def _get_paypal_client() -> Optional[Client]:
    """Get or initialize PayPal client."""
    global _paypal_client, _orders_controller
    if not config.PAYPAL_CLIENT_ID or not config.PAYPAL_CLIENT_SECRET:
        return None
    if _paypal_client is None:
        _paypal_client = Client(
            client_credentials_auth_credentials={
                "o_auth_client_id": config.PAYPAL_CLIENT_ID,
                "o_auth_client_secret": config.PAYPAL_CLIENT_SECRET,
            },
            environment=Environment.PRODUCTION if config.PAYPAL_MODE == "live" else Environment.SANDBOX,
        )
        _orders_controller = _paypal_client.orders
        logger.info("paypal_initialized")
    return _paypal_client


def is_paypal_configured() -> bool:
    """Check if PayPal is configured."""
    return bool(config.PAYPAL_CLIENT_ID and config.PAYPAL_CLIENT_SECRET)


def create_paypal_order(
    amount: int,
    currency: str,
    checkout_id: str,
    description: Optional[str] = None,
) -> Optional[dict]:
    """Create a PayPal order for checkout."""
    client = _get_paypal_client()
    if not client or not _orders_controller:
        logger.warning("paypal_not_configured", action="skip_order_creation")
        return None

    # Convert cents to dollars
    amount_value = f"{amount / 100:.2f}"

    try:
        result = _orders_controller.create_order({
            "body": OrderRequest(
                intent=CheckoutPaymentIntent.CAPTURE,
                purchase_units=[
                    PurchaseUnitRequest(
                        amount=AmountWithBreakdown(
                            currency_code=currency.upper(),
                            value=amount_value,
                        ),
                        description=description or f"Checkout {checkout_id}",
                        custom_id=checkout_id,
                    )
                ],
            ),
            "prefer": "return=representation",
        })
        order = result.body
        logger.info(
            "paypal_order_created",
            paypal_order_id=order.id,
            checkout_id=checkout_id,
            amount=amount,
        )
        return {"id": order.id, "status": order.status}
    except Exception as e:
        logger.error("paypal_order_creation_failed", checkout_id=checkout_id, error=str(e))
        raise


def capture_paypal_order(order_id: str) -> Optional[dict]:
    """Capture a PayPal order."""
    client = _get_paypal_client()
    if not client or not _orders_controller:
        return None

    try:
        result = _orders_controller.capture_order({
            "id": order_id,
            "prefer": "return=representation",
        })
        order = result.body
        logger.info("paypal_order_captured", paypal_order_id=order_id, status=order.status)
        return {"id": order.id, "status": order.status}
    except Exception as e:
        logger.error("paypal_capture_failed", paypal_order_id=order_id, error=str(e))
        raise


def get_paypal_order(order_id: str) -> Optional[dict]:
    """Get PayPal order details."""
    client = _get_paypal_client()
    if not client or not _orders_controller:
        return None

    try:
        result = _orders_controller.get_order({"id": order_id})
        order = result.body
        custom_id = order.purchase_units[0].custom_id if order.purchase_units else None
        return {"id": order.id, "status": order.status, "custom_id": custom_id}
    except Exception as e:
        logger.error("paypal_get_order_failed", paypal_order_id=order_id, error=str(e))
        raise
