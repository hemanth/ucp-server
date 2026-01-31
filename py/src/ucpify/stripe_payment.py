"""Stripe payment integration."""

from typing import Optional
import stripe

from .config import config
from .logger import logger

_stripe_initialized = False


def init_stripe() -> bool:
    """Initialize Stripe client if configured."""
    global _stripe_initialized
    if not config.STRIPE_SECRET_KEY:
        return False
    if not _stripe_initialized:
        stripe.api_key = config.STRIPE_SECRET_KEY
        _stripe_initialized = True
        logger.info("stripe_initialized")
    return True


def is_stripe_configured() -> bool:
    """Check if Stripe is configured."""
    return bool(config.STRIPE_SECRET_KEY)


def create_payment_intent(
    amount: int,
    currency: str,
    checkout_id: str,
    customer_email: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Optional[stripe.PaymentIntent]:
    """Create a Stripe PaymentIntent for a checkout."""
    if not init_stripe():
        logger.warning("stripe_not_configured", action="skip_payment_intent")
        return None
    
    try:
        intent = stripe.PaymentIntent.create(
            amount=amount,
            currency=currency.lower(),
            metadata={
                "checkout_id": checkout_id,
                **(metadata or {}),
            },
            receipt_email=customer_email,
            automatic_payment_methods={"enabled": True},
            idempotency_key=f"checkout_{checkout_id}",
        )
        logger.info(
            "payment_intent_created",
            payment_intent_id=intent.id,
            checkout_id=checkout_id,
            amount=amount,
        )
        return intent
    except stripe.StripeError as e:
        logger.error("payment_intent_failed", checkout_id=checkout_id, error=str(e))
        raise


def retrieve_payment_intent(payment_intent_id: str) -> Optional[stripe.PaymentIntent]:
    """Retrieve a PaymentIntent by ID."""
    if not init_stripe():
        return None
    try:
        return stripe.PaymentIntent.retrieve(payment_intent_id)
    except stripe.StripeError as e:
        logger.error("payment_intent_retrieve_failed", payment_intent_id=payment_intent_id, error=str(e))
        raise


def cancel_payment_intent(payment_intent_id: str) -> Optional[stripe.PaymentIntent]:
    """Cancel a PaymentIntent."""
    if not init_stripe():
        return None
    try:
        intent = stripe.PaymentIntent.cancel(payment_intent_id)
        logger.info("payment_intent_canceled", payment_intent_id=payment_intent_id)
        return intent
    except stripe.StripeError as e:
        logger.error("payment_intent_cancel_failed", payment_intent_id=payment_intent_id, error=str(e))
        raise


def construct_webhook_event(payload: bytes, signature: str) -> Optional[stripe.Event]:
    """Construct and verify a Stripe webhook event."""
    if not config.STRIPE_WEBHOOK_SECRET:
        return None
    try:
        return stripe.Webhook.construct_event(
            payload, signature, config.STRIPE_WEBHOOK_SECRET
        )
    except stripe.SignatureVerificationError as e:
        logger.error("webhook_signature_invalid", error=str(e))
        return None
