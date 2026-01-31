# ucpify

Generate UCP-compliant (Universal Commerce Protocol) servers for merchants from a simple schema.

## What is UCP?

The [Universal Commerce Protocol](https://ucp.dev) is an open standard for agentic commerce, enabling AI agents, apps, businesses, and payment providers to interact seamlessly. UCP was co-developed by Google, Shopify, Etsy, Wayfair, Target, and Walmart.

## Quick Start

```bash
# Initialize a sample merchant config
npx ts-node src/cli.ts init

# Edit merchant-config.json to add your products, shipping, payments

# Start the UCP server
npx ts-node src/cli.ts serve merchant-config.json
```

## Merchant Configuration Schema

```json
{
  "name": "My Store",
  "domain": "http://localhost:3000",
  "currency": "USD",
  "tax_rate": 0.08,
  "terms_url": "https://example.com/terms",
  "port": 3000,
  "items": [
    {
      "id": "item_001",
      "title": "Classic T-Shirt",
      "description": "A comfortable cotton t-shirt",
      "price": 2500
    }
  ],
  "shipping_options": [
    {
      "id": "standard",
      "title": "Standard Shipping",
      "price": 500,
      "estimated_days": "5-7 business days"
    }
  ],
  "payment_handlers": [
    {
      "namespace": "com.stripe",
      "id": "stripe_handler",
      "config": { "publishable_key": "pk_test_..." }
    }
  ]
}
```

## UCP Endpoints

Once running, your server exposes these UCP-compliant endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/.well-known/ucp` | GET | UCP Profile (discovery) |
| `/ucp/v1/checkout-sessions` | POST | Create checkout session |
| `/ucp/v1/checkout-sessions/:id` | GET | Get checkout session |
| `/ucp/v1/checkout-sessions/:id` | PUT | Update checkout session |
| `/ucp/v1/checkout-sessions/:id/complete` | POST | Complete checkout (create order) |
| `/ucp/v1/checkout-sessions/:id/cancel` | POST | Cancel checkout |
| `/ucp/v1/orders` | GET | List orders |
| `/ucp/v1/orders/:id` | GET | Get order |
| `/ucp/v1/items` | GET | Product catalog |

## Example Usage

### 1. Create a checkout session

```bash
curl -X POST http://localhost:3000/ucp/v1/checkout-sessions \
  -H "Content-Type: application/json" \
  -d '{
    "line_items": [
      {
        "item": { "id": "item_001" },
        "quantity": 2
      }
    ]
  }'
```

### 2. Update with buyer info and shipping

```bash
curl -X PUT http://localhost:3000/ucp/v1/checkout-sessions/chk_xxx \
  -H "Content-Type: application/json" \
  -d '{
    "buyer": {
      "email": "customer@example.com",
      "first_name": "Jane",
      "last_name": "Doe"
    },
    "line_items": [{ "item": { "id": "item_001" }, "quantity": 2 }],
    "fulfillment": {
      "methods": [{
        "type": "shipping",
        "destinations": [{
          "street_address": "123 Main St",
          "address_locality": "Springfield",
          "address_region": "IL",
          "postal_code": "62701",
          "address_country": "US"
        }]
      }]
    }
  }'
```

### 3. Complete checkout

```bash
curl -X POST http://localhost:3000/ucp/v1/checkout-sessions/chk_xxx/complete
```

## CLI Commands

```bash
# Create sample configuration
ucpify init --output my-store.json

# Validate configuration
ucpify validate my-store.json

# Start server
ucpify serve my-store.json --port 8080
```

## Programmatic Usage

```typescript
import { createExpressApp, MerchantConfigSchema } from 'ucpify';

const config = MerchantConfigSchema.parse({
  name: 'My Store',
  domain: 'http://localhost:3000',
  currency: 'USD',
  items: [
    { id: 'prod_1', title: 'Widget', price: 1999 }
  ],
  shipping_options: [
    { id: 'standard', title: 'Standard', price: 500 }
  ]
});

const app = createExpressApp(config);
app.listen(3000);
```

## License

MIT

---

## Identity Linking (Bring Your Own OAuth)

UCP uses OAuth 2.0 for identity linking between agents and merchants. ucpify does **not** include a built-in OAuth server—instead, integrate your existing OAuth provider.

### Required OAuth Endpoints

Your OAuth provider must expose these endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/oauth2/authorize` | GET | Authorization page (user consent) |
| `/oauth2/token` | POST | Token exchange |
| `/oauth2/revoke` | POST | Token revocation |

### OAuth Discovery Endpoint

Create `/.well-known/oauth-authorization-server` returning:

```json
{
  "issuer": "https://your-store.com",
  "authorization_endpoint": "https://your-store.com/oauth2/authorize",
  "token_endpoint": "https://your-store.com/oauth2/token",
  "revocation_endpoint": "https://your-store.com/oauth2/revoke",
  "scopes_supported": [
    "ucp:scopes:checkout_session",
    "ucp:scopes:order_read",
    "ucp:scopes:order_manage"
  ],
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code", "refresh_token"],
  "token_endpoint_auth_methods_supported": ["client_secret_basic"],
  "service_documentation": "https://your-store.com/docs/oauth"
}
```

### UCP OAuth Scopes

| Scope | Description |
|-------|-------------|
| `ucp:scopes:checkout_session` | Create and manage checkout sessions |
| `ucp:scopes:order_read` | Read order information |
| `ucp:scopes:order_manage` | Manage orders (cancel, refund) |

### Integration Examples

#### Using Auth0

```javascript
// Add to your Express app
app.get('/.well-known/oauth-authorization-server', (req, res) => {
  res.json({
    issuer: 'https://your-tenant.auth0.com',
    authorization_endpoint: 'https://your-tenant.auth0.com/authorize',
    token_endpoint: 'https://your-tenant.auth0.com/oauth/token',
    revocation_endpoint: 'https://your-tenant.auth0.com/oauth/revoke',
    scopes_supported: ['ucp:scopes:checkout_session'],
    response_types_supported: ['code'],
    grant_types_supported: ['authorization_code', 'refresh_token'],
    token_endpoint_auth_methods_supported: ['client_secret_basic'],
  });
});
```

#### Using Keycloak

```python
# Add to your Flask app
@app.route('/.well-known/oauth-authorization-server')
def oauth_discovery():
    return jsonify({
        "issuer": "https://keycloak.example.com/realms/merchant",
        "authorization_endpoint": "https://keycloak.example.com/realms/merchant/protocol/openid-connect/auth",
        "token_endpoint": "https://keycloak.example.com/realms/merchant/protocol/openid-connect/token",
        "revocation_endpoint": "https://keycloak.example.com/realms/merchant/protocol/openid-connect/revoke",
        "scopes_supported": ["ucp:scopes:checkout_session"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic"],
    })
```

#### Using AWS Cognito

```javascript
app.get('/.well-known/oauth-authorization-server', (req, res) => {
  const cognitoDomain = 'https://your-domain.auth.us-east-1.amazoncognito.com';
  res.json({
    issuer: cognitoDomain,
    authorization_endpoint: `${cognitoDomain}/oauth2/authorize`,
    token_endpoint: `${cognitoDomain}/oauth2/token`,
    revocation_endpoint: `${cognitoDomain}/oauth2/revoke`,
    scopes_supported: ['ucp:scopes:checkout_session'],
    response_types_supported: ['code'],
    grant_types_supported: ['authorization_code', 'refresh_token'],
    token_endpoint_auth_methods_supported: ['client_secret_basic'],
  });
});
```

### Protecting UCP Endpoints

Add middleware to validate tokens on protected endpoints:

```javascript
// JavaScript/Express
const validateToken = async (req, res, next) => {
  const authHeader = req.headers.authorization;
  if (!authHeader?.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'Missing token' });
  }
  
  const token = authHeader.slice(7);
  // Validate with your OAuth provider
  const valid = await verifyTokenWithProvider(token);
  if (!valid) {
    return res.status(401).json({ error: 'Invalid token' });
  }
  next();
};

// Apply to checkout endpoints
app.post('/ucp/v1/checkout-sessions', validateToken, createCheckout);
```

```python
# Python/Flask
from functools import wraps

def require_oauth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Missing token'}), 401
        
        token = auth_header[7:]
        # Validate with your OAuth provider
        if not verify_token_with_provider(token):
            return jsonify({'error': 'Invalid token'}), 401
        return f(*args, **kwargs)
    return decorated

@app.route('/ucp/v1/checkout-sessions', methods=['POST'])
@require_oauth
def create_checkout():
    ...
```

### Agent Platform Flow

1. Agent discovers OAuth endpoints via `/.well-known/oauth-authorization-server`
2. Agent redirects user to `authorization_endpoint` with scopes
3. User consents on merchant's OAuth page
4. Agent receives authorization code
5. Agent exchanges code for access token at `token_endpoint`
6. Agent uses access token in `Authorization: Bearer <token>` header
7. Agent refreshes token when expired using `refresh_token` grant
