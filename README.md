# ucpify

Generate UCP-compliant commerce servers from a simple JSON config.

## Install

```bash
npm install ucpify        # Node.js
pip install ucpify-py     # Python
```

## What is UCP?

The [Universal Commerce Protocol](https://ucp.dev) is an open standard for agentic commerce, enabling AI agents to interact with merchants through a unified API.

## Quick Start

```bash
# Node.js
npx ucpify init
npx ucpify serve merchant-config.json

# Python
ucpify init
ucpify serve merchant-config.json
```

## Config

```json
{
  "name": "My Store",
  "domain": "https://mystore.com",
  "currency": "USD",
  "items": [{ "id": "tshirt", "title": "T-Shirt", "price": 2500 }],
  "shipping_options": [{ "id": "standard", "title": "Standard", "price": 500 }],
  "payment_handlers": [{ "namespace": "com.stripe", "id": "stripe_1" }]
}
```

## Endpoints

```
GET  /.well-known/ucp                    Discovery
POST /ucp/v1/checkout-sessions           Create checkout
GET  /ucp/v1/checkout-sessions/:id       Get checkout
PUT  /ucp/v1/checkout-sessions/:id       Update checkout
POST /ucp/v1/checkout-sessions/:id/complete
POST /ucp/v1/checkout-sessions/:id/cancel
GET  /ucp/v1/orders/:id                  Get order
GET  /admin                              Dashboard
GET  /health                             Health check
```

## Features

- SQLite persistence
- Stripe and PayPal payments
- Rate limiting and input validation
- Admin dashboard
- Docker support

## Environment

```bash
UCP_DOMAIN=https://your-store.com
STRIPE_SECRET_KEY=sk_...
STRIPE_WEBHOOK_SECRET=whsec_...
# Or PayPal
PAYPAL_CLIENT_ID=...
PAYPAL_CLIENT_SECRET=...
```

## Spec Compliance

Built to the [UCP Specification](https://github.com/Universal-Commerce-Protocol/ucp/tree/main/docs/specification). Supports checkout, fulfillment, and order capabilities.

## License

MIT
