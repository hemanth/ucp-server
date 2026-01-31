"""UCP Server implementation."""

import uuid
from datetime import datetime, timedelta
from typing import Optional, Any

from ucpify.schema import (
    MerchantConfig,
    Item,
    Buyer,
    UCP_VERSION,
)


class UCPServer:
    """UCP-compliant server for merchants."""

    def __init__(self, config: MerchantConfig):
        self.config = config
        self.items_map = {item.id: item for item in config.items}
        self.checkout_sessions: dict[str, dict] = {}
        self.orders: dict[str, dict] = {}

    def get_profile(self) -> dict:
        """Generate UCP profile for /.well-known/ucp."""
        payment_handlers = {}
        for handler in self.config.payment_handlers:
            payment_handlers[handler.namespace] = [{
                "id": handler.id,
                "version": UCP_VERSION,
                "config": handler.config,
            }]

        return {
            "ucp": {
                "version": UCP_VERSION,
                "services": {
                    "dev.ucp.shopping": [{
                        "version": UCP_VERSION,
                        "spec": "https://ucp.dev/specification/overview",
                        "transport": "rest",
                        "endpoint": f"{self.config.domain}/ucp/v1",
                        "schema": "https://ucp.dev/services/shopping/rest.openapi.json",
                    }]
                },
                "capabilities": {
                    "dev.ucp.shopping.checkout": [{
                        "version": UCP_VERSION,
                        "spec": "https://ucp.dev/specification/checkout",
                        "schema": "https://ucp.dev/schemas/shopping/checkout.json",
                    }],
                    "dev.ucp.shopping.fulfillment": [{
                        "version": UCP_VERSION,
                        "spec": "https://ucp.dev/specification/fulfillment",
                        "schema": "https://ucp.dev/schemas/shopping/fulfillment.json",
                        "extends": "dev.ucp.shopping.checkout",
                    }],
                    "dev.ucp.shopping.order": [{
                        "version": UCP_VERSION,
                        "spec": "https://ucp.dev/specification/order",
                        "schema": "https://ucp.dev/schemas/shopping/order.json",
                    }],
                },
                "payment_handlers": payment_handlers,
            }
        }

    def _calculate_totals(self, line_items: list[dict]) -> list[dict]:
        """Calculate totals for line items."""
        subtotal = sum(li["item"]["price"] * li["quantity"] for li in line_items)
        tax = round(subtotal * self.config.tax_rate)
        return [
            {"type": "subtotal", "amount": subtotal},
            {"type": "tax", "amount": tax},
            {"type": "total", "amount": subtotal + tax},
        ]

    def _validate_checkout(self, session: dict) -> list[dict]:
        """Validate checkout and generate messages."""
        messages = []

        if not session.get("buyer", {}).get("email"):
            messages.append({
                "type": "error",
                "code": "missing",
                "path": "$.buyer.email",
                "content": "Buyer email is required",
                "severity": "recoverable",
            })

        fulfillment = session.get("fulfillment", {})
        methods = fulfillment.get("methods", [])
        if not methods or not methods[0].get("selected_destination_id"):
            messages.append({
                "type": "error",
                "code": "missing",
                "path": "$.fulfillment.methods[0].selected_destination_id",
                "content": "Shipping address is required",
                "severity": "recoverable",
            })

        return messages

    def _resolve_item(self, item_input: dict) -> dict:
        """Resolve item from catalog or use provided."""
        item_id = item_input.get("id")
        if item_id and item_id in self.items_map:
            item = self.items_map[item_id]
            return item.model_dump()
        return {
            "id": item_id or "unknown",
            "title": item_input.get("title", "Unknown Item"),
            "price": item_input.get("price", 0),
        }

    def create_checkout(self, data: dict) -> dict:
        """Create a new checkout session."""
        checkout_id = f"chk_{uuid.uuid4().hex[:12]}"

        line_items = []
        for idx, li in enumerate(data.get("line_items", [])):
            item = self._resolve_item(li.get("item", {}))
            quantity = li.get("quantity", 1)
            subtotal = item["price"] * quantity
            line_items.append({
                "id": li.get("id") or f"li_{idx + 1}",
                "item": item,
                "quantity": quantity,
                "totals": [
                    {"type": "subtotal", "amount": subtotal},
                    {"type": "total", "amount": subtotal},
                ],
            })

        totals = self._calculate_totals(line_items)

        payment_handlers = {}
        for handler in self.config.payment_handlers:
            payment_handlers[handler.namespace] = [{
                "id": handler.id,
                "version": UCP_VERSION,
                "config": handler.config,
            }]

        # Build links
        links = []
        if self.config.terms_url:
            links.append({"type": "terms_of_service", "url": str(self.config.terms_url)})
        if self.config.privacy_url:
            links.append({"type": "privacy_policy", "url": str(self.config.privacy_url)})

        # Set expiry (1 hour from now)
        expires_at = (datetime.utcnow() + timedelta(hours=1)).isoformat() + "Z"

        session = {
            "ucp": {
                "version": UCP_VERSION,
                "capabilities": {
                    "dev.ucp.shopping.checkout": [{"version": UCP_VERSION}]
                },
                "payment_handlers": payment_handlers,
            },
            "id": checkout_id,
            "status": "incomplete",
            "currency": self.config.currency,
            "line_items": line_items,
            "totals": totals,
            "links": links,
            "continue_url": f"{self.config.domain}/checkout/{checkout_id}",
            "expires_at": expires_at,
        }

        messages = self._validate_checkout(session)
        if messages:
            session["messages"] = messages
            session["status"] = "incomplete"
        else:
            session["status"] = "ready_for_complete"

        self.checkout_sessions[checkout_id] = session
        return session

    def get_checkout(self, checkout_id: str) -> Optional[dict]:
        """Get a checkout session."""
        return self.checkout_sessions.get(checkout_id)

    def update_checkout(self, checkout_id: str, data: dict) -> Optional[dict]:
        """Update a checkout session."""
        session = self.checkout_sessions.get(checkout_id)
        if not session:
            return None

        # Update buyer
        if data.get("buyer"):
            session["buyer"] = {**session.get("buyer", {}), **data["buyer"]}

        # Update line items
        if data.get("line_items"):
            line_items = []
            for idx, li in enumerate(data["line_items"]):
                item = self._resolve_item(li.get("item", {}))
                quantity = li.get("quantity", 1)
                subtotal = item["price"] * quantity
                line_items.append({
                    "id": li.get("id") or f"li_{idx + 1}",
                    "item": item,
                    "quantity": quantity,
                    "totals": [
                        {"type": "subtotal", "amount": subtotal},
                        {"type": "total", "amount": subtotal},
                    ],
                })
            session["line_items"] = line_items

        # Update fulfillment
        if data.get("fulfillment"):
            methods = []
            for m_idx, method in enumerate(data["fulfillment"].get("methods", [])):
                destinations = []
                for d_idx, dest in enumerate(method.get("destinations", [])):
                    destinations.append({
                        **dest,
                        "id": dest.get("id") or f"dest_{d_idx + 1}",
                    })

                # Build shipping options from config
                groups = method.get("groups") or [{
                    "id": f"group_{m_idx + 1}",
                    "line_item_ids": [li["id"] for li in session["line_items"]],
                    "selected_option_id": self.config.shipping_options[0].id if self.config.shipping_options else None,
                    "options": [{
                        "id": opt.id,
                        "title": opt.title,
                        "description": opt.description or opt.estimated_days,
                        "totals": [{"type": "total", "amount": opt.price}],
                    } for opt in self.config.shipping_options],
                }]

                methods.append({
                    "id": method.get("id") or f"method_{m_idx + 1}",
                    "type": method.get("type", "shipping"),
                    "line_item_ids": method.get("line_item_ids") or [li["id"] for li in session["line_items"]],
                    "selected_destination_id": method.get("selected_destination_id") or (destinations[0]["id"] if destinations else None),
                    "destinations": destinations,
                    "groups": groups,
                })

            session["fulfillment"] = {"methods": methods}

        # Recalculate totals
        shipping_total = 0
        if session.get("fulfillment"):
            for method in session["fulfillment"]["methods"]:
                for group in method.get("groups", []):
                    selected_id = group.get("selected_option_id")
                    for opt in group.get("options", []):
                        if opt["id"] == selected_id:
                            for t in opt.get("totals", []):
                                if t["type"] == "total":
                                    shipping_total += t["amount"]

        subtotal = sum(li["item"]["price"] * li["quantity"] for li in session["line_items"])
        tax = round(subtotal * self.config.tax_rate)
        session["totals"] = [
            {"type": "subtotal", "amount": subtotal},
            {"type": "shipping", "amount": shipping_total},
            {"type": "tax", "amount": tax},
            {"type": "total", "amount": subtotal + shipping_total + tax},
        ]

        messages = self._validate_checkout(session)
        if messages:
            session["messages"] = messages
            session["status"] = "incomplete"
        else:
            session.pop("messages", None)
            session["status"] = "ready_for_complete"

        self.checkout_sessions[checkout_id] = session
        return session

    def complete_checkout(self, checkout_id: str) -> dict:
        """Complete checkout and create order."""
        session = self.checkout_sessions.get(checkout_id)
        if not session:
            return {"error": "Checkout session not found"}

        if session["status"] != "ready_for_complete":
            return {"error": "Checkout is not ready for completion", "messages": session.get("messages", [])}

        order_id = f"order_{uuid.uuid4().hex[:12]}"

        fulfillment = None
        if session.get("fulfillment"):
            expectations = []
            for idx, method in enumerate(session["fulfillment"]["methods"]):
                dest = next((d for d in method["destinations"] if d["id"] == method.get("selected_destination_id")), None)
                expectations.append({
                    "id": f"exp_{idx + 1}",
                    "line_items": [{"id": li_id, "quantity": next((li["quantity"] for li in session["line_items"] if li["id"] == li_id), 1)} for li_id in method["line_item_ids"]],
                    "method_type": method["type"],
                    "destination": dest,
                    "description": "Arrives in 5-7 business days",
                    "fulfillable_on": "now",
                })
            fulfillment = {"expectations": expectations, "events": []}

        order = {
            "ucp": {
                "version": UCP_VERSION,
                "capabilities": {
                    "dev.ucp.shopping.order": [{"version": UCP_VERSION}]
                },
            },
            "id": order_id,
            "checkout_id": checkout_id,
            "permalink_url": f"{self.config.domain}/orders/{order_id}",
            "line_items": session["line_items"],
            "buyer": session.get("buyer"),
            "totals": session["totals"],
            "fulfillment": fulfillment,
            "created_at": datetime.utcnow().isoformat() + "Z",
        }

        self.orders[order_id] = order
        
        # Update session to completed with order confirmation
        session["status"] = "completed"
        session["order"] = {
            "id": order_id,
            "permalink_url": order["permalink_url"],
        }
        session.pop("continue_url", None)
        session.pop("messages", None)
        self.checkout_sessions[checkout_id] = session

        return session

    def cancel_checkout(self, checkout_id: str) -> Optional[dict]:
        """Cancel a checkout session."""
        session = self.checkout_sessions.get(checkout_id)
        if not session:
            return None
        session["status"] = "canceled"
        session.pop("continue_url", None)
        self.checkout_sessions[checkout_id] = session
        return session

    def get_order(self, order_id: str) -> Optional[dict]:
        """Get an order."""
        return self.orders.get(order_id)

    def list_orders(self) -> list[dict]:
        """List all orders."""
        return list(self.orders.values())
