"""Flask application factory for UCP server."""

from datetime import datetime
from flask import Flask, request, jsonify, g
from ucp_server.schema import MerchantConfig, UCP_VERSION
from ucp_server.server import UCPServer
from ucp_server.server_db import UCPServerDB
from ucp_server.db import is_db_healthy, get_db
from ucp_server.logger import logger
from ucp_server.stripe_payment import construct_webhook_event
from ucp_server.paypal_payment import capture_paypal_order, get_paypal_order


def create_flask_app(config: MerchantConfig, use_db: bool = True) -> Flask:
    """Create a Flask app with UCP endpoints."""
    app = Flask(__name__)
    ucp_server = UCPServerDB(config) if use_db else UCPServer(config)

    @app.route("/webhooks/stripe", methods=["POST"])
    def stripe_webhook():
        """Handle Stripe webhook events."""
        signature = request.headers.get("Stripe-Signature")
        if not signature:
            return jsonify({"error": "Missing stripe-signature header"}), 400

        event = construct_webhook_event(request.data, signature)
        if not event:
            return jsonify({"error": "Invalid webhook signature"}), 400

        logger.info("stripe_webhook_received", event_type=event.type, event_id=event.id)

        if event.type == "payment_intent.succeeded":
            payment_intent = event.data.object
            checkout_id = payment_intent.metadata.get("checkout_id")
            order_id = payment_intent.metadata.get("order_id")
            
            if checkout_id and use_db:
                conn = get_db()
                now = datetime.utcnow().isoformat() + "Z"
                conn.execute("UPDATE checkouts SET payment_status = 'succeeded', updated_at = ? WHERE id = ?",
                             (now, checkout_id))
                if order_id:
                    conn.execute("UPDATE orders SET payment_status = 'succeeded' WHERE id = ?", (order_id,))
                conn.commit()
                logger.info("payment_succeeded", checkout_id=checkout_id, order_id=order_id,
                            payment_intent_id=payment_intent.id)

        elif event.type == "payment_intent.payment_failed":
            payment_intent = event.data.object
            checkout_id = payment_intent.metadata.get("checkout_id")
            order_id = payment_intent.metadata.get("order_id")
            
            if checkout_id and use_db:
                conn = get_db()
                now = datetime.utcnow().isoformat() + "Z"
                conn.execute("UPDATE checkouts SET payment_status = 'failed', updated_at = ? WHERE id = ?",
                             (now, checkout_id))
                if order_id:
                    conn.execute("UPDATE orders SET payment_status = 'failed' WHERE id = ?", (order_id,))
                conn.commit()
                error_msg = getattr(payment_intent.last_payment_error, "message", None)
                logger.error("payment_failed", checkout_id=checkout_id, order_id=order_id,
                             payment_intent_id=payment_intent.id, error=error_msg)

        return jsonify({"received": True})

    @app.route("/webhooks/paypal", methods=["POST"])
    def paypal_webhook():
        """Handle PayPal webhook events."""
        data = request.get_json()
        event_type = data.get("event_type")
        resource = data.get("resource", {})

        logger.info("paypal_webhook_received", event_type=event_type, resource_id=resource.get("id"))

        if event_type == "CHECKOUT.ORDER.APPROVED" and resource.get("id"):
            try:
                # Capture the payment
                captured = capture_paypal_order(resource["id"])
                if captured and captured["status"] == "COMPLETED":
                    order_details = get_paypal_order(resource["id"])
                    checkout_id = order_details.get("custom_id") if order_details else None
                    
                    if checkout_id and use_db:
                        conn = get_db()
                        now = datetime.utcnow().isoformat() + "Z"
                        conn.execute("UPDATE checkouts SET payment_status = 'succeeded', updated_at = ? WHERE paypal_order_id = ?",
                                     (now, resource["id"]))
                        conn.execute("UPDATE orders SET payment_status = 'succeeded' WHERE paypal_order_id = ?",
                                     (resource["id"],))
                        conn.commit()
                        logger.info("paypal_payment_captured", checkout_id=checkout_id, paypal_order_id=resource["id"])
            except Exception as e:
                logger.error("paypal_capture_failed", paypal_order_id=resource.get("id"), error=str(e))

        elif event_type == "PAYMENT.CAPTURE.DENIED" and resource.get("id"):
            if use_db:
                conn = get_db()
                now = datetime.utcnow().isoformat() + "Z"
                pp_order_id = resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id")
                if pp_order_id:
                    conn.execute("UPDATE checkouts SET payment_status = 'failed', updated_at = ? WHERE paypal_order_id = ?",
                                 (now, pp_order_id))
                    conn.execute("UPDATE orders SET payment_status = 'failed' WHERE paypal_order_id = ?",
                                 (pp_order_id,))
                    conn.commit()
                logger.error("paypal_payment_denied", paypal_order_id=resource.get("id"))

        return jsonify({"received": True})

    @app.before_request
    def log_request():
        g.start_time = datetime.utcnow()

    @app.after_request
    def log_response(response):
        duration = (datetime.utcnow() - g.start_time).total_seconds() * 1000
        logger.info("request_completed", method=request.method, path=request.path, 
                    status=response.status_code, duration_ms=round(duration, 2))
        return response

    @app.after_request
    def add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, UCP-Agent, Authorization"
        return response

    @app.route("/.well-known/ucp", methods=["GET"])
    def get_ucp_profile():
        return jsonify(ucp_server.get_profile())

    @app.route("/ucp/v1/checkout-sessions", methods=["POST"])
    def create_checkout():
        try:
            data = request.get_json()
            session = ucp_server.create_checkout(data)
            return jsonify(session), 201
        except Exception as e:
            return jsonify({"error": "Invalid request", "details": str(e)}), 400

    @app.route("/ucp/v1/checkout-sessions/<checkout_id>", methods=["GET"])
    def get_checkout(checkout_id: str):
        session = ucp_server.get_checkout(checkout_id)
        if not session:
            return jsonify({"error": "Checkout session not found"}), 404
        return jsonify(session)

    @app.route("/ucp/v1/checkout-sessions/<checkout_id>", methods=["PUT"])
    def update_checkout(checkout_id: str):
        try:
            data = request.get_json()
            session = ucp_server.update_checkout(checkout_id, data)
            if not session:
                return jsonify({"error": "Checkout session not found"}), 404
            return jsonify(session)
        except Exception as e:
            return jsonify({"error": "Invalid request", "details": str(e)}), 400

    @app.route("/ucp/v1/checkout-sessions/<checkout_id>/complete", methods=["POST"])
    def complete_checkout(checkout_id: str):
        result = ucp_server.complete_checkout(checkout_id)
        if "error" in result:
            return jsonify(result), 400
        return jsonify(result), 201

    @app.route("/ucp/v1/checkout-sessions/<checkout_id>/cancel", methods=["POST"])
    def cancel_checkout(checkout_id: str):
        session = ucp_server.cancel_checkout(checkout_id)
        if not session:
            return jsonify({"error": "Checkout session not found"}), 404
        return jsonify(session)

    @app.route("/ucp/v1/orders", methods=["GET"])
    def list_orders():
        return jsonify(ucp_server.list_orders())

    @app.route("/ucp/v1/orders/<order_id>", methods=["GET"])
    def get_order(order_id: str):
        order = ucp_server.get_order(order_id)
        if not order:
            return jsonify({"error": "Order not found"}), 404
        return jsonify(order)

    @app.route("/ucp/v1/items", methods=["GET"])
    def list_items():
        return jsonify([item.model_dump() for item in config.items])

    @app.route("/admin/stats", methods=["GET"])
    def admin_stats():
        if not use_db:
            return jsonify({"error": "Stats require database mode"}), 501
        try:
            conn = get_db()
            checkout_stats = conn.execute(
                "SELECT status, COUNT(*) as count FROM checkouts GROUP BY status"
            ).fetchall()
            order_stats = conn.execute(
                "SELECT payment_status, payment_provider, COUNT(*) as count FROM orders GROUP BY payment_status, payment_provider"
            ).fetchall()
            today_orders = conn.execute(
                "SELECT COUNT(*) as count FROM orders WHERE date(created_at) = date('now')"
            ).fetchone()
            return jsonify({
                "checkouts": {row["status"]: row["count"] for row in checkout_stats},
                "orders": {
                    "by_payment_status": {f"{row['payment_provider'] or 'none'}_{row['payment_status']}": row["count"] for row in order_stats},
                    "today": today_orders["count"] if today_orders else 0,
                },
                "timestamp": datetime.utcnow().isoformat() + "Z",
            })
        except Exception as e:
            logger.error("admin_stats_failed", error=str(e))
            return jsonify({"error": "Failed to get stats"}), 500

    @app.route("/admin", methods=["GET"])
    def admin_dashboard():
        return """<!DOCTYPE html>
<html><head><title>UCP Admin</title>
<style>
  body { font-family: system-ui; max-width: 800px; margin: 40px auto; padding: 20px; }
  .card { background: #f5f5f5; padding: 20px; border-radius: 8px; margin: 10px 0; }
  .stat { font-size: 2em; font-weight: bold; color: #333; }
  .label { color: #666; font-size: 0.9em; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; }
</style>
</head><body>
<h1>🛒 UCP Server Admin</h1>
<div class="grid" id="stats">Loading...</div>
<script>
fetch('/admin/stats').then(r=>r.json()).then(d=>{
  document.getElementById('stats').innerHTML = `
    <div class="card"><div class="stat">${Object.values(d.checkouts||{}).reduce((a,b)=>a+b,0)}</div><div class="label">Total Checkouts</div></div>
    <div class="card"><div class="stat">${d.checkouts?.completed||0}</div><div class="label">Completed</div></div>
    <div class="card"><div class="stat">${d.orders?.today||0}</div><div class="label">Orders Today</div></div>
  `;
}).catch(e=>document.getElementById('stats').innerHTML='Error loading stats');
</script>
</body></html>"""

    @app.route("/health", methods=["GET"])
    def health():
        db_healthy = is_db_healthy()
        status = "ok" if db_healthy else "degraded"
        return jsonify({
            "status": status,
            "ucp_version": UCP_VERSION,
            "database": "connected" if db_healthy else "disconnected",
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }), 200 if db_healthy else 503

    return app
