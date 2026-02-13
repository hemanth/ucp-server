# ucpify

**JSON → Commerce Server in seconds.**

Generate a fully [UCP](https://ucp.dev)-compliant commerce server from a single JSON config. Stripe, PayPal, OAuth 2.0, SQLite — all wired up.

![ucpify architecture](assets/ucpify-arch.png)

## Install

```bash
npm install ucpify        # Node.js
pip install ucpify        # Python
```

## Quick Start

```bash
npx ucpify init
npx ucpify serve merchant-config.json
```

## Config

One JSON. Full commerce.

![ucpify config](assets/ucpify-config.png)

```json
{
  "name": "My Store",
  "domain": "https://mystore.com",
  "currency": "USD",
  "tax_rate": 0.08,
  "items": [
    { "id": "tshirt", "title": "Classic Tee", "price": 2500 },
    { "id": "hoodie", "title": "Premium Hoodie", "price": 5999 }
  ],
  "shipping_options": [
    { "id": "standard", "title": "Standard Shipping", "price": 500 },
    { "id": "express", "title": "Express Shipping", "price": 1500 }
  ],
  "payment_handlers": [
    { "namespace": "com.stripe", "id": "stripe_handler" },
    { "namespace": "com.paypal", "id": "paypal_handler" }
  ],
  "oauth": { "provider": "built-in" }
}
```

## Endpoints

![ucpify endpoints](assets/ucpify-endpoints.png)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/.well-known/ucp` | UCP discovery |
| `POST` | `/ucp/v1/checkout-sessions` | Create checkout |
| `GET` | `/ucp/v1/checkout-sessions/:id` | Get session |
| `PUT` | `/ucp/v1/checkout-sessions/:id` | Update session |
| `POST` | `.../complete` | Complete checkout |
| `POST` | `.../cancel` | Cancel checkout |
| `GET` | `/ucp/v1/orders` | List orders |
| `GET` | `/ucp/v1/orders/:id` | Get order |
| `GET` | `/ucp/v1/items` | Product catalog |
| `GET` | `/.well-known/oauth-authorization-server` | OAuth metadata |
| `GET` | `/oauth2/authorize` | Consent screen |
| `POST` | `/oauth2/token` | Token exchange |
| `POST` | `/oauth2/revoke` | Token revocation |

## OAuth 2.0 Identity Linking

ucpify supports [UCP Identity Linking](https://ucp.dev/specification/identity-linking) via OAuth 2.0:

```json
// Built-in OAuth server
{ "oauth": { "provider": "built-in" } }

// External provider (Auth0, Okta, etc.)
{ "oauth": { "provider": "external", "issuer": "https://...", "authorization_endpoint": "https://...", "token_endpoint": "https://..." } }
```

Register clients via CLI:

```bash
npx ucpify oauth:add-client merchant-config.json \
  --name "My Agent" \
  --redirect-uri "http://localhost:8080/callback"
```

Features: Authorization Code + PKCE, token refresh/revocation, Bearer middleware on `/ucp/v1/*`, RFC 8414 metadata.

## Features

- **Payments** — Stripe + PayPal, webhook handlers included
- **OAuth 2.0** — Built-in or external provider, PKCE, scoped tokens
- **SQLite** — WAL mode, proper schemas, foreign keys, indexes
- **Validation** — Zod/Pydantic schemas, rate limiting
- **Admin** — Dashboard + health endpoint
- **Dual runtime** — Node.js and Python, feature parity

## Environment

```bash
UCP_DOMAIN=https://your-store.com
STRIPE_SECRET_KEY=sk_...
STRIPE_WEBHOOK_SECRET=whsec_...
PAYPAL_CLIENT_ID=...
PAYPAL_CLIENT_SECRET=...
```

## CLI

```bash
ucpify init                          # Generate sample config
ucpify validate config.json          # Validate config
ucpify serve config.json             # Start server (SQLite)
ucpify serve config.json --no-db     # In-memory mode
ucpify oauth:add-client config.json  # Register OAuth client
```

## Links

- [npm](https://www.npmjs.com/package/ucpify) · [PyPI](https://pypi.org/project/ucpify/) · [GitHub](https://github.com/hemanth/ucpify) · [UCP Spec](https://ucp.dev)

## License

MIT
