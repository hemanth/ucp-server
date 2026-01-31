"""SQLite-backed UCP Server implementation."""

import json
import uuid
from datetime import datetime, timedelta
from typing import Optional, Any

from ucp_server.db import get_db
from ucp_server.logger import logger
from ucp_server.schema import MerchantConfig, UCP_VERSION
from ucp_server.stripe_payment import create_payment_intent, is_stripe_configured
from ucp_server.paypal_payment import create_paypal_order, is_paypal_configured


class UCPServerDB:
    """UCP-compliant server with SQLite persistence."""

    def __init__(self, config: MerchantConfig):
        self.config = config
        self.items_map = {item.id: item for item in config.items}

    def _build_links(self) -> list[dict]:
        links = []
        if self.config.terms_url:
            links.append({"type": "terms_of_service", "url": str(self.config.terms_url)})
        if self.config.privacy_url:
            links.append({"type": "privacy_policy", "url": str(self.config.privacy_url)})
        return links

    def _build_payment_handlers(self) -> dict:
        handlers = {}
        for h in self.config.payment_handlers:
            handlers[h.namespace] = [{"id": h.id, "version": UCP_VERSION, "config": h.config}]
        return handlers

    def _validate_checkout(self, session: dict) -> list[dict]:
        messages = []
        if not session.get("buyer", {}).get("email"):
            messages.append({
                "type": "error", "code": "missing", "path": "$.buyer.email",
                "content": "Buyer email is required", "severity": "recoverable"
            })
        fulfillment = session.get("fulfillment", {})
        methods = fulfillment.get("methods", [])
        if not methods or not methods[0].get("selected_destination_id"):
            messages.append({
                "type": "error", "code": "missing", "path": "$.fulfillment.methods[0].selected_destination_id",
                "content": "Shipping address is required", "severity": "recoverable"
            })
        return messages

    def _resolve_item(self, item_input: dict) -> dict:
        item_id = item_input.get("id")
        if item_id and item_id in self.items_map:
            return self.items_map[item_id].model_dump()
        return {"id": item_id or "unknown", "title": item_input.get("title", "Unknown"), "price": item_input.get("price", 0)}

    def _calculate_totals(self, line_items: list[dict], shipping: int = 0) -> list[dict]:
        subtotal = sum(li["item"]["price"] * li["quantity"] for li in line_items)
        tax = round(subtotal * self.config.tax_rate)
        totals = [{"type": "subtotal", "amount": subtotal}]
        if shipping > 0:
            totals.append({"type": "shipping", "amount": shipping})
        totals.append({"type": "tax", "amount": tax})
        totals.append({"type": "total", "amount": subtotal + shipping + tax})
        return totals

    def _session_from_row(self, row) -> Optional[dict]:
        if not row:
            return None
        conn = get_db()
        line_items_rows = conn.execute("SELECT * FROM line_items WHERE checkout_id = ?", (row["id"],)).fetchall()
        line_items = [{
            "id": li["id"],
            "item": json.loads(li["item_json"]),
            "quantity": li["quantity"],
            "totals": json.loads(li["totals_json"]) if li["totals_json"] else None,
        } for li in line_items_rows]

        session = {
            "ucp": {
                "version": UCP_VERSION,
                "capabilities": {"dev.ucp.shopping.checkout": [{"version": UCP_VERSION}]},
                "payment_handlers": self._build_payment_handlers(),
            },
            "id": row["id"],
            "status": row["status"],
            "currency": row["currency"],
            "line_items": line_items,
            "totals": json.loads(row["totals_json"]),
            "links": json.loads(row["links_json"]),
        }
        if row["buyer_json"]:
            session["buyer"] = json.loads(row["buyer_json"])
        if row["fulfillment_json"]:
            session["fulfillment"] = json.loads(row["fulfillment_json"])
        if row["payment_json"]:
            session["payment"] = json.loads(row["payment_json"])
        if row["messages_json"]:
            session["messages"] = json.loads(row["messages_json"])
        if row["continue_url"]:
            session["continue_url"] = row["continue_url"]
        if row["expires_at"]:
            session["expires_at"] = row["expires_at"]
        if row["order_json"]:
            session["order"] = json.loads(row["order_json"])
        return session

    def get_profile(self) -> dict:
        return {
            "ucp": {
                "version": UCP_VERSION,
                "services": {"dev.ucp.shopping": [{
                    "version": UCP_VERSION, "spec": "https://ucp.dev/specification/overview",
                    "transport": "rest", "endpoint": f"{self.config.domain}/ucp/v1",
                    "schema": "https://ucp.dev/services/shopping/rest.openapi.json",
                }]},
                "capabilities": {
                    "dev.ucp.shopping.checkout": [{"version": UCP_VERSION, "spec": "https://ucp.dev/specification/checkout", "schema": "https://ucp.dev/schemas/shopping/checkout.json"}],
                    "dev.ucp.shopping.fulfillment": [{"version": UCP_VERSION, "spec": "https://ucp.dev/specification/fulfillment", "schema": "https://ucp.dev/schemas/shopping/fulfillment.json", "extends": "dev.ucp.shopping.checkout"}],
                    "dev.ucp.shopping.order": [{"version": UCP_VERSION, "spec": "https://ucp.dev/specification/order", "schema": "https://ucp.dev/schemas/shopping/order.json"}],
                },
                "payment_handlers": self._build_payment_handlers(),
            }
        }

    def create_checkout(self, data: dict) -> dict:
        conn = get_db()
        checkout_id = f"chk_{uuid.uuid4().hex[:12]}"
        now = datetime.utcnow().isoformat() + "Z"
        expires_at = (datetime.utcnow() + timedelta(hours=1)).isoformat() + "Z"

        line_items = []
        for idx, li in enumerate(data.get("line_items", [])):
            item = self._resolve_item(li.get("item", {}))
            quantity = li.get("quantity", 1)
            subtotal = item["price"] * quantity
            line_items.append({
                "id": li.get("id") or f"li_{idx + 1}",
                "item": item, "quantity": quantity,
                "totals": [{"type": "subtotal", "amount": subtotal}, {"type": "total", "amount": subtotal}],
            })

        totals = self._calculate_totals(line_items)
        links = self._build_links()
        messages = self._validate_checkout({})
        status = "incomplete" if messages else "ready_for_complete"
        continue_url = f"{self.config.domain}/checkout/{checkout_id}"

        conn.execute("""
            INSERT INTO checkouts (id, status, currency, totals_json, links_json, messages_json, continue_url, expires_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (checkout_id, status, self.config.currency, json.dumps(totals), json.dumps(links),
              json.dumps(messages) if messages else None, continue_url, expires_at, now, now))

        for li in line_items:
            conn.execute("INSERT INTO line_items (id, checkout_id, item_json, quantity, totals_json) VALUES (?, ?, ?, ?, ?)",
                         (li["id"], checkout_id, json.dumps(li["item"]), li["quantity"], json.dumps(li["totals"])))
        conn.commit()

        logger.info("checkout_created", checkout_id=checkout_id, line_items=len(line_items))
        return self.get_checkout(checkout_id)

    def get_checkout(self, checkout_id: str) -> Optional[dict]:
        conn = get_db()
        row = conn.execute("SELECT * FROM checkouts WHERE id = ?", (checkout_id,)).fetchone()
        return self._session_from_row(row)

    def update_checkout(self, checkout_id: str, data: dict) -> Optional[dict]:
        session = self.get_checkout(checkout_id)
        if not session:
            return None

        conn = get_db()
        now = datetime.utcnow().isoformat() + "Z"
        buyer = session.get("buyer", {})
        line_items = session["line_items"]
        fulfillment = session.get("fulfillment")

        if data.get("buyer"):
            buyer = {**buyer, **data["buyer"]}

        if data.get("line_items"):
            line_items = []
            for idx, li in enumerate(data["line_items"]):
                item = self._resolve_item(li.get("item", {}))
                quantity = li.get("quantity", 1)
                subtotal = item["price"] * quantity
                line_items.append({
                    "id": li.get("id") or f"li_{idx + 1}",
                    "item": item, "quantity": quantity,
                    "totals": [{"type": "subtotal", "amount": subtotal}, {"type": "total", "amount": subtotal}],
                })

        if data.get("fulfillment"):
            methods = []
            for m_idx, method in enumerate(data["fulfillment"].get("methods", [])):
                destinations = [{"id": d.get("id") or f"dest_{i+1}", **d} for i, d in enumerate(method.get("destinations", []))]
                groups = method.get("groups") or [{
                    "id": f"group_{m_idx + 1}",
                    "line_item_ids": [li["id"] for li in line_items],
                    "selected_option_id": self.config.shipping_options[0].id if self.config.shipping_options else None,
                    "options": [{"id": opt.id, "title": opt.title, "description": opt.description or opt.estimated_days,
                                 "totals": [{"type": "total", "amount": opt.price}]} for opt in self.config.shipping_options],
                }]
                methods.append({
                    "id": method.get("id") or f"method_{m_idx + 1}",
                    "type": method.get("type", "shipping"),
                    "line_item_ids": method.get("line_item_ids") or [li["id"] for li in line_items],
                    "selected_destination_id": method.get("selected_destination_id") or (destinations[0]["id"] if destinations else None),
                    "destinations": destinations, "groups": groups,
                })
            fulfillment = {"methods": methods}

        # Calculate shipping
        shipping_total = 0
        if fulfillment:
            for method in fulfillment["methods"]:
                for group in method.get("groups", []):
                    selected_id = group.get("selected_option_id")
                    for opt in group.get("options", []):
                        if opt["id"] == selected_id:
                            for t in opt.get("totals", []):
                                if t["type"] == "total":
                                    shipping_total += t["amount"]

        totals = self._calculate_totals(line_items, shipping_total)
        messages = self._validate_checkout({"buyer": buyer, "fulfillment": fulfillment})
        status = "incomplete" if messages else "ready_for_complete"

        conn.execute("""
            UPDATE checkouts SET status = ?, buyer_json = ?, fulfillment_json = ?, totals_json = ?, messages_json = ?, updated_at = ?
            WHERE id = ?
        """, (status, json.dumps(buyer) if buyer else None, json.dumps(fulfillment) if fulfillment else None,
              json.dumps(totals), json.dumps(messages) if messages else None, now, checkout_id))

        conn.execute("DELETE FROM line_items WHERE checkout_id = ?", (checkout_id,))
        for li in line_items:
            conn.execute("INSERT INTO line_items (id, checkout_id, item_json, quantity, totals_json) VALUES (?, ?, ?, ?, ?)",
                         (li["id"], checkout_id, json.dumps(li["item"]), li["quantity"], json.dumps(li["totals"])))
        conn.commit()

        logger.info("checkout_updated", checkout_id=checkout_id, status=status)
        return self.get_checkout(checkout_id)

    def complete_checkout(self, checkout_id: str) -> dict:
        session = self.get_checkout(checkout_id)
        if not session:
            return {"error": "Checkout session not found"}
        if session["status"] != "ready_for_complete":
            return {"error": "Checkout is not ready for completion", "messages": session.get("messages", [])}

        conn = get_db()
        order_id = f"order_{uuid.uuid4().hex[:12]}"
        now = datetime.utcnow().isoformat() + "Z"
        permalink_url = f"{self.config.domain}/orders/{order_id}"

        # Get total amount for payment
        total_amount = next((t["amount"] for t in session["totals"] if t["type"] == "total"), 0)
        
        # Payment processing - try Stripe first, then PayPal
        payment_intent_id = None
        paypal_order_id = None
        payment_provider = None
        payment_status = "none"
        
        if total_amount > 0:
            # Try Stripe first
            if is_stripe_configured():
                try:
                    payment_intent = create_payment_intent(
                        amount=total_amount,
                        currency=session["currency"],
                        checkout_id=checkout_id,
                        customer_email=session.get("buyer", {}).get("email"),
                        metadata={"order_id": order_id},
                    )
                    if payment_intent:
                        payment_intent_id = payment_intent.id
                        payment_provider = "stripe"
                        payment_status = "pending"
                        logger.info("stripe_payment_intent_created",
                                    checkout_id=checkout_id, payment_intent_id=payment_intent_id, amount=total_amount)
                except Exception as e:
                    logger.error("stripe_payment_intent_failed", checkout_id=checkout_id, error=str(e))
                    return {"error": "Payment processing failed"}
            # Fall back to PayPal if Stripe not configured
            elif is_paypal_configured():
                try:
                    pp_order = create_paypal_order(
                        amount=total_amount,
                        currency=session["currency"],
                        checkout_id=checkout_id,
                        description=f"Order {order_id}",
                    )
                    if pp_order:
                        paypal_order_id = pp_order["id"]
                        payment_provider = "paypal"
                        payment_status = "pending"
                        logger.info("paypal_order_created",
                                    checkout_id=checkout_id, paypal_order_id=paypal_order_id, amount=total_amount)
                except Exception as e:
                    logger.error("paypal_order_creation_failed", checkout_id=checkout_id, error=str(e))
                    return {"error": "Payment processing failed"}

        order_fulfillment = None
        if session.get("fulfillment"):
            expectations = []
            for idx, method in enumerate(session["fulfillment"]["methods"]):
                dest = next((d for d in method["destinations"] if d["id"] == method.get("selected_destination_id")), None)
                expectations.append({
                    "id": f"exp_{idx + 1}",
                    "line_items": [{"id": li_id, "quantity": next((li["quantity"] for li in session["line_items"] if li["id"] == li_id), 1)} for li_id in method["line_item_ids"]],
                    "method_type": method["type"], "destination": dest,
                    "description": "Arrives in 5-7 business days", "fulfillable_on": "now",
                })
            order_fulfillment = {"expectations": expectations, "events": []}

        order_confirmation = {"id": order_id, "permalink_url": permalink_url}

        conn.execute("""
            INSERT INTO orders (id, checkout_id, permalink_url, buyer_json, line_items_json, totals_json, fulfillment_json, payment_intent_id, paypal_order_id, payment_provider, payment_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (order_id, checkout_id, permalink_url, json.dumps(session.get("buyer")),
              json.dumps(session["line_items"]), json.dumps(session["totals"]),
              json.dumps(order_fulfillment) if order_fulfillment else None, payment_intent_id, paypal_order_id, payment_provider, payment_status, now))

        conn.execute("""
            UPDATE checkouts SET status = 'completed', order_json = ?, continue_url = NULL, messages_json = NULL, payment_intent_id = ?, paypal_order_id = ?, payment_provider = ?, payment_status = ?, updated_at = ?
            WHERE id = ?
        """, (json.dumps(order_confirmation), payment_intent_id, paypal_order_id, payment_provider, payment_status, now, checkout_id))
        conn.commit()

        logger.info("checkout_completed", checkout_id=checkout_id, order_id=order_id, payment_intent_id=payment_intent_id)
        return self.get_checkout(checkout_id)

    def cancel_checkout(self, checkout_id: str) -> Optional[dict]:
        session = self.get_checkout(checkout_id)
        if not session:
            return None

        conn = get_db()
        conn.execute("UPDATE checkouts SET status = 'canceled', continue_url = NULL, updated_at = ? WHERE id = ?",
                     (datetime.utcnow().isoformat() + "Z", checkout_id))
        conn.commit()

        logger.info("checkout_canceled", checkout_id=checkout_id)
        return self.get_checkout(checkout_id)

    def get_order(self, order_id: str) -> Optional[dict]:
        conn = get_db()
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not row:
            return None
        return {
            "ucp": {"version": UCP_VERSION, "capabilities": {"dev.ucp.shopping.order": [{"version": UCP_VERSION}]}},
            "id": row["id"], "checkout_id": row["checkout_id"], "permalink_url": row["permalink_url"],
            "line_items": json.loads(row["line_items_json"]),
            "buyer": json.loads(row["buyer_json"]) if row["buyer_json"] else None,
            "totals": json.loads(row["totals_json"]),
            "fulfillment": json.loads(row["fulfillment_json"]) if row["fulfillment_json"] else None,
            "created_at": row["created_at"],
        }

    def list_orders(self) -> list[dict]:
        conn = get_db()
        rows = conn.execute("SELECT id FROM orders ORDER BY created_at DESC").fetchall()
        return [self.get_order(row["id"]) for row in rows]
