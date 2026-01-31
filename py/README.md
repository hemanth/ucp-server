# ucpify (Python)

Generate UCP-compliant (Universal Commerce Protocol) servers for merchants from a simple schema.

## Installation

```bash
pip install -e .
```

## Quick Start

```bash
# Initialize a sample merchant config
ucpify init

# Edit merchant-config.json to add your products, shipping, payments

# Start the UCP server
ucpify serve merchant-config.json
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

```python
from ucp_server import create_flask_app, MerchantConfig, Item, ShippingOption

config = MerchantConfig(
    name="My Store",
    domain="http://localhost:3000",
    currency="USD",
    items=[
        Item(id="prod_1", title="Widget", price=1999)
    ],
    shipping_options=[
        ShippingOption(id="standard", title="Standard", price=500)
    ]
)

app = create_flask_app(config)
app.run(port=3000)
```

## License

MIT
