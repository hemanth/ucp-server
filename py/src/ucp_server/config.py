"""Configuration from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class Config:
    """Application configuration."""
    
    # Server
    PORT: int = int(os.getenv("PORT", "3000"))
    ENV: str = os.getenv("ENV", "development")
    
    # Database
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "./data/ucp.db")
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    # UCP
    UCP_DOMAIN: str = os.getenv("UCP_DOMAIN", "http://localhost:3000")
    
    # Stripe
    STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    
    # PayPal
    PAYPAL_CLIENT_ID: str = os.getenv("PAYPAL_CLIENT_ID", "")
    PAYPAL_CLIENT_SECRET: str = os.getenv("PAYPAL_CLIENT_SECRET", "")
    PAYPAL_MODE: str = os.getenv("PAYPAL_MODE", "sandbox")


config = Config()
