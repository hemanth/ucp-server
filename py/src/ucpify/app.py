"""Flask application factory for UCP server."""

import base64
from datetime import datetime
from urllib.parse import urlencode
from flask import Flask, request, jsonify, g, redirect, make_response
from ucpify.schema import MerchantConfig, UCP_VERSION
from ucpify.server import UCPServer
from ucpify.server_db import UCPServerDB
from ucpify.db import is_db_healthy, get_db
from ucpify.logger import logger
from ucpify.stripe_payment import construct_webhook_event
from ucpify.paypal_payment import capture_paypal_order, get_paypal_order
from ucpify.oauth import OAuthManager


def create_flask_app(config: MerchantConfig, use_db: bool = True) -> Flask:
    """Create a Flask app with UCP endpoints."""
    app = Flask(__name__)
    ucp_server = UCPServerDB(config) if use_db else UCPServer(config)

    # OAuth setup (built-in or external)
    oauth_enabled = config.oauth is not None
    oauth_manager = OAuthManager() if oauth_enabled and config.oauth and config.oauth.provider == "built-in" else None

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

    # ─── OAuth 2.0 Identity Linking ─────────────────────────────────────

    @app.route("/.well-known/oauth-authorization-server", methods=["GET"])
    def oauth_metadata():
        if not oauth_enabled:
            return jsonify({"error": "OAuth not configured"}), 404
        if config.oauth and config.oauth.provider == "external":
            return jsonify({
                "issuer": str(config.oauth.issuer),
                "authorization_endpoint": str(config.oauth.authorization_endpoint),
                "token_endpoint": str(config.oauth.token_endpoint),
                "revocation_endpoint": str(config.oauth.revocation_endpoint) if config.oauth.revocation_endpoint else None,
                "jwks_uri": str(config.oauth.jwks_uri) if config.oauth.jwks_uri else None,
                "scopes_supported": ["ucp:scopes:checkout_session"],
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "token_endpoint_auth_methods_supported": ["client_secret_basic"],
            })
        elif oauth_manager:
            return jsonify(oauth_manager.get_server_metadata(str(config.domain)))
        return jsonify({"error": "OAuth misconfigured"}), 500

    if oauth_manager:
        @app.route("/oauth2/authorize", methods=["GET"])
        def oauth_authorize_get():
            client_id = request.args.get("client_id", "")
            redirect_uri = request.args.get("redirect_uri", "")
            scope = request.args.get("scope", "ucp:scopes:checkout_session")
            state = request.args.get("state", "")
            response_type = request.args.get("response_type", "")
            code_challenge = request.args.get("code_challenge", "")
            code_challenge_method = request.args.get("code_challenge_method", "")

            if response_type != "code":
                return jsonify({"error": "unsupported_response_type"}), 400

            client = oauth_manager.get_client(client_id)
            if not client:
                return jsonify({"error": "invalid_client"}), 400
            if redirect_uri not in client.redirect_uris:
                return jsonify({"error": "invalid_redirect_uri"}), 400

            return f"""<!DOCTYPE html>
<html><head><title>Authorize - {config.name}</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 400px; margin: 80px auto; padding: 0 20px; }}
h2 {{ margin-bottom: 4px; }}
p {{ color: #666; }}
.scope {{ background: #f0f0f0; padding: 8px 12px; border-radius: 6px; font-family: monospace; }}
button {{ padding: 10px 24px; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; margin-right: 8px; }}
.allow {{ background: #000; color: #fff; }}
.deny {{ background: #e5e5e5; }}
</style></head>
<body>
<h2>{config.name}</h2>
<p><strong>{client.name}</strong> wants to access your account.</p>
<div class="scope">{scope}</div>
<p>This will allow the app to manage checkout sessions on your behalf.</p>
<form method="POST" action="/oauth2/authorize">
  <input type="hidden" name="client_id" value="{client_id}">
  <input type="hidden" name="redirect_uri" value="{redirect_uri}">
  <input type="hidden" name="scope" value="{scope}">
  <input type="hidden" name="state" value="{state}">
  <input type="hidden" name="code_challenge" value="{code_challenge}">
  <input type="hidden" name="code_challenge_method" value="{code_challenge_method}">
  <input type="hidden" name="user_id" value="user_1">
  <button type="submit" name="action" value="allow" class="allow">Allow</button>
  <button type="submit" name="action" value="deny" class="deny">Deny</button>
</form>
</body></html>"""

        @app.route("/oauth2/authorize", methods=["POST"])
        def oauth_authorize_post():
            client_id = request.form.get("client_id", "")
            redirect_uri = request.form.get("redirect_uri", "")
            scope = request.form.get("scope", "")
            state = request.form.get("state", "")
            action = request.form.get("action", "")
            user_id = request.form.get("user_id", "")
            code_challenge = request.form.get("code_challenge") or None
            code_challenge_method = request.form.get("code_challenge_method") or None

            if action == "deny":
                params = {"error": "access_denied"}
                if state:
                    params["state"] = state
                return redirect(f"{redirect_uri}?{urlencode(params)}")

            code = oauth_manager.create_authorization_code(
                client_id, user_id, scope, redirect_uri,
                code_challenge, code_challenge_method,
            )
            params = {"code": code}
            if state:
                params["state"] = state
            return redirect(f"{redirect_uri}?{urlencode(params)}")

        @app.route("/oauth2/token", methods=["POST"])
        def oauth_token():
            # Client auth via HTTP Basic (RFC 7617)
            auth_header = request.headers.get("Authorization", "")
            client_id = request.form.get("client_id", "")
            client_secret = request.form.get("client_secret", "")

            if auth_header.startswith("Basic "):
                decoded = base64.b64decode(auth_header[6:]).decode()
                client_id, client_secret = decoded.split(":", 1)

            if not oauth_manager.authenticate_client(client_id, client_secret):
                return jsonify({"error": "invalid_client"}), 401

            grant_type = request.form.get("grant_type", "")

            if grant_type == "authorization_code":
                result = oauth_manager.exchange_code(
                    request.form.get("code", ""),
                    client_id,
                    request.form.get("redirect_uri", ""),
                    request.form.get("code_verifier"),
                )
                if "error" in result:
                    return jsonify(result), 400
                return jsonify({**result, "token_type": "Bearer"})

            elif grant_type == "refresh_token":
                result = oauth_manager.refresh_access_token(
                    request.form.get("refresh_token", ""), client_id
                )
                if "error" in result:
                    return jsonify(result), 400
                return jsonify({**result, "token_type": "Bearer"})

            return jsonify({"error": "unsupported_grant_type"}), 400

        @app.route("/oauth2/revoke", methods=["POST"])
        def oauth_revoke():
            auth_header = request.headers.get("Authorization", "")
            client_id = request.form.get("client_id", "")
            client_secret = request.form.get("client_secret", "")

            if auth_header.startswith("Basic "):
                decoded = base64.b64decode(auth_header[6:]).decode()
                client_id, client_secret = decoded.split(":", 1)

            if not oauth_manager.authenticate_client(client_id, client_secret):
                return jsonify({"error": "invalid_client"}), 401

            oauth_manager.revoke_token(request.form.get("token", ""))
            return jsonify({}), 200

    # Bearer token middleware for UCP routes
    if oauth_enabled and oauth_manager:
        @app.before_request
        def check_bearer_token():
            if not request.path.startswith("/ucp/v1"):
                return None

            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return jsonify({"error": "missing_token", "message": "Authorization: Bearer <token> required"}), 401

            token_str = auth_header[7:]
            valid_token = oauth_manager.validate_access_token(token_str)
            if not valid_token:
                return jsonify({"error": "invalid_token", "message": "Token is expired, revoked, or invalid"}), 401

            g.oauth_user_id = valid_token.user_id
            g.oauth_client_id = valid_token.client_id
            g.oauth_scope = valid_token.scope
            return None

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
