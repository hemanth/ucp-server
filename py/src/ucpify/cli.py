#!/usr/bin/env python3
"""CLI for ucpify - Generate and run UCP-compliant servers."""

import json
import sys
from pathlib import Path

import click
from pydantic import ValidationError

from ucpify.schema import MerchantConfig
from ucpify.app import create_flask_app


SAMPLE_CONFIG = {
    "name": "My Store",
    "domain": "http://localhost:3000",
    "currency": "USD",
    "terms_url": "https://example.com/terms",
    "privacy_url": "https://example.com/privacy",
    "tax_rate": 0.08,
    "port": 3000,
    "items": [
        {
            "id": "item_001",
            "title": "Classic T-Shirt",
            "description": "A comfortable cotton t-shirt",
            "price": 2500,
            "sku": "TSH-001",
        },
        {
            "id": "item_002",
            "title": "Premium Hoodie",
            "description": "Warm and stylish hoodie",
            "price": 5999,
            "sku": "HOO-001",
        },
    ],
    "shipping_options": [
        {
            "id": "standard",
            "title": "Standard Shipping",
            "description": "Arrives in 5-7 business days",
            "price": 500,
            "estimated_days": "5-7 business days",
        },
        {
            "id": "express",
            "title": "Express Shipping",
            "description": "Arrives in 2-3 business days",
            "price": 1500,
            "estimated_days": "2-3 business days",
        },
        {
            "id": "overnight",
            "title": "Overnight Shipping",
            "description": "Arrives next business day",
            "price": 2999,
            "estimated_days": "1 business day",
        },
    ],
    "payment_handlers": [
        {
            "namespace": "com.stripe",
            "id": "stripe_handler",
            "config": {"publishable_key": "pk_test_..."},
        },
        {
            "namespace": "com.paypal",
            "id": "paypal_handler",
            "config": {"client_id": "your_paypal_client_id"},
        },
    ],
}


@click.group()
@click.version_option(version="1.2.0")
def main():
    """ucpify - Generate and run UCP-compliant servers for merchants."""
    pass


@main.command()
@click.option("-o", "--output", default="merchant-config.json", help="Output file path")
def init(output: str):
    """Create a sample merchant configuration file."""
    path = Path(output)
    path.write_text(json.dumps(SAMPLE_CONFIG, indent=2))
    click.echo(f"✅ Created sample config at: {output}")
    click.echo("\n📝 Edit this file to configure your products, shipping, and payment handlers.")
    click.echo(f"\n🚀 Run: ucpify serve {output}")


@main.command()
@click.argument("config", type=click.Path(exists=True))
@click.option("-p", "--port", type=int, help="Port to run on (overrides config)")
@click.option("--no-db", is_flag=True, help="Use in-memory storage instead of SQLite")
def serve(config: str, port: int | None, no_db: bool):
    """Start the UCP server from a configuration file."""
    try:
        config_path = Path(config)
        raw_config = json.loads(config_path.read_text())
        merchant_config = MerchantConfig(**raw_config)

        if port:
            merchant_config.port = port

        use_db = not no_db
        storage_type = "SQLite" if use_db else "In-memory"
        app = create_flask_app(merchant_config, use_db=use_db)
        run_port = merchant_config.port

        # OAuth status
        oauth_status = "disabled"
        oauth_lines = ""
        if merchant_config.oauth:
            oauth_status = merchant_config.oauth.provider
            oauth_lines = f"""
║  OAuth Endpoints:                                            ║
║  • GET  /.well-known/oauth-authorization-server              ║"""
            if merchant_config.oauth.provider == "built-in":
                oauth_lines += """
║  • GET  /oauth2/authorize             - Consent Screen       ║
║  • POST /oauth2/token                 - Token Exchange       ║
║  • POST /oauth2/revoke                - Token Revocation     ║"""

        click.echo(f"""
╔══════════════════════════════════════════════════════════════╗
║                    🛒 UCP Server Running                     ║
╠══════════════════════════════════════════════════════════════╣
║  Merchant: {merchant_config.name:<49}║
║  Domain:   {str(merchant_config.domain):<49}║
║  Port:     {run_port:<49}║
║  Storage:  {storage_type:<49}║
║  OAuth:    {oauth_status:<49}║
╠══════════════════════════════════════════════════════════════╣
║  Endpoints:                                                  ║
║  • GET  /.well-known/ucp              - UCP Profile          ║
║  • POST /ucp/v1/checkout-sessions     - Create Checkout      ║
║  • GET  /ucp/v1/checkout-sessions/:id - Get Checkout         ║
║  • PUT  /ucp/v1/checkout-sessions/:id - Update Checkout      ║
║  • POST /ucp/v1/checkout-sessions/:id/complete - Complete    ║
║  • POST /ucp/v1/checkout-sessions/:id/cancel   - Cancel      ║
║  • GET  /ucp/v1/orders                - List Orders          ║
║  • GET  /ucp/v1/orders/:id            - Get Order            ║
║  • GET  /ucp/v1/items                 - Product Catalog      ║
║  • GET  /health                       - Health Check         ║{oauth_lines}
╚══════════════════════════════════════════════════════════════╝

🔗 UCP Profile: http://localhost:{run_port}/.well-known/ucp
📦 Products: {len(merchant_config.items)} items loaded
🚚 Shipping: {len(merchant_config.shipping_options)} options available
💳 Payment Handlers: {len(merchant_config.payment_handlers)} configured
💾 Storage: {storage_type}{' (./data/ucp.db)' if use_db else ''}
🔐 OAuth: {oauth_status}
        """)

        app.run(host="0.0.0.0", port=run_port, debug=False)

    except ValidationError as e:
        click.echo(f"❌ Configuration validation failed:\n{e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Error: {e}", err=True)
        sys.exit(1)


@main.command()
@click.argument("config", type=click.Path(exists=True))
def validate(config: str):
    """Validate a merchant configuration file."""
    try:
        config_path = Path(config)
        raw_config = json.loads(config_path.read_text())
        merchant_config = MerchantConfig(**raw_config)

        click.echo("✅ Configuration is valid!")
        click.echo(f"\n📊 Summary:")
        click.echo(f"   • Name: {merchant_config.name}")
        click.echo(f"   • Domain: {merchant_config.domain}")
        click.echo(f"   • Currency: {merchant_config.currency}")
        click.echo(f"   • Tax Rate: {merchant_config.tax_rate * 100:.2f}%")
        click.echo(f"   • Products: {len(merchant_config.items)}")
        click.echo(f"   • Shipping Options: {len(merchant_config.shipping_options)}")
        click.echo(f"   • Payment Handlers: {len(merchant_config.payment_handlers)}")
        click.echo(f"   • OAuth: {merchant_config.oauth.provider if merchant_config.oauth else 'disabled'}")

    except ValidationError as e:
        click.echo(f"❌ Validation failed:\n{e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Error: {e}", err=True)
        sys.exit(1)


@main.command("oauth:add-client")
@click.argument("config", type=click.Path(exists=True))
@click.option("--name", required=True, help="Client application name")
@click.option("--redirect-uri", required=True, multiple=True, help="Allowed redirect URIs")
def oauth_add_client(config: str, name: str, redirect_uri: tuple[str, ...]):
    """Register an OAuth client for the built-in provider."""
    try:
        config_path = Path(config)
        raw_config = json.loads(config_path.read_text())
        merchant_config = MerchantConfig(**raw_config)

        if not merchant_config.oauth:
            click.echo("❌ OAuth is not configured in this merchant config.", err=True)
            click.echo("   Add `\"oauth\": { \"provider\": \"built-in\" }` to your config.", err=True)
            sys.exit(1)
        if merchant_config.oauth.provider != "built-in":
            click.echo("❌ oauth:add-client only works with the built-in provider.", err=True)
            sys.exit(1)

        from ucpify.oauth import OAuthManager
        oauth = OAuthManager()
        client = oauth.create_client(name, list(redirect_uri))

        click.echo(f"\n✅ OAuth client registered!")
        click.echo(f"\n🔑 Credentials (save these — the secret won't be shown again):")
        click.echo(f"   Client ID:     {client.client_id}")
        click.echo(f"   Client Secret: {client.client_secret}")
        click.echo(f"   Redirect URIs: {', '.join(client.redirect_uris)}")
        click.echo(f"\n💡 Use these in your OAuth 2.0 Authorization Code flow.")

    except ValidationError as e:
        click.echo(f"❌ Configuration validation failed:\n{e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
