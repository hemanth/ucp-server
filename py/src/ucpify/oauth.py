"""OAuth 2.0 Identity Linking for UCP.

Built-in OAuth authorization server implementing:
- Authorization Code flow (RFC 6749 §4.1)
- PKCE (RFC 7636)
- Token management (access + refresh)
- Client authentication via HTTP Basic (RFC 7617)
- Token revocation (RFC 7009)
- Server metadata (RFC 8414)
"""

import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Optional

from ucpify.logger import logger

# ── Constants ──────────────────────────────────────────────────────────

UCP_SCOPES = ("ucp:scopes:checkout_session",)
ACCESS_TOKEN_TTL = 60 * 60  # 1 hour
REFRESH_TOKEN_TTL = 60 * 60 * 24 * 30  # 30 days
AUTH_CODE_TTL = 60 * 10  # 10 minutes


# ── Types ──────────────────────────────────────────────────────────────

@dataclass
class OAuthClient:
    client_id: str
    client_secret: str
    client_secret_hash: str
    name: str
    redirect_uris: list[str]
    created_at: str


@dataclass
class AuthorizationCode:
    code: str
    client_id: str
    user_id: str
    scope: str
    redirect_uri: str
    expires_at: int
    used: bool = False
    code_challenge: Optional[str] = None
    code_challenge_method: Optional[str] = None


@dataclass
class OAuthToken:
    token: str
    token_type: str  # "access" | "refresh"
    client_id: str
    user_id: str
    scope: str
    expires_at: int
    revoked: bool = False
    parent_token: Optional[str] = None


# ── Helpers ────────────────────────────────────────────────────────────

def _generate_token(nbytes: int = 32) -> str:
    return secrets.token_hex(nbytes)


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _now_epoch() -> int:
    return int(time.time())


# ── OAuth Manager ──────────────────────────────────────────────────────

class OAuthManager:
    """In-memory OAuth 2.0 authorization server."""

    def __init__(self):
        self.clients: dict[str, OAuthClient] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.access_tokens: dict[str, OAuthToken] = {}
        self.refresh_tokens: dict[str, OAuthToken] = {}

    # ── Client Management ──

    def create_client(self, name: str, redirect_uris: list[str]) -> OAuthClient:
        client_id = f"ucp_{_generate_token(16)}"
        client_secret = f"ucp_secret_{_generate_token(24)}"
        client = OAuthClient(
            client_id=client_id,
            client_secret=client_secret,
            client_secret_hash=_hash_secret(client_secret),
            name=name,
            redirect_uris=redirect_uris,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self.clients[client_id] = client
        logger.info("oauth_client_created", client_id=client_id, name=name)
        return client

    def get_client(self, client_id: str) -> Optional[OAuthClient]:
        return self.clients.get(client_id)

    def authenticate_client(self, client_id: str, client_secret: str) -> bool:
        client = self.clients.get(client_id)
        if not client:
            return False
        return client.client_secret_hash == _hash_secret(client_secret)

    # ── Authorization Code ──

    def create_authorization_code(
        self,
        client_id: str,
        user_id: str,
        scope: str,
        redirect_uri: str,
        code_challenge: Optional[str] = None,
        code_challenge_method: Optional[str] = None,
    ) -> str:
        code = _generate_token(32)
        auth_code = AuthorizationCode(
            code=code,
            client_id=client_id,
            user_id=user_id,
            scope=scope,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            expires_at=_now_epoch() + AUTH_CODE_TTL,
        )
        self.codes[code] = auth_code
        logger.info("authorization_code_created", client_id=client_id, user_id=user_id, scope=scope)
        return code

    def exchange_code(
        self,
        code: str,
        client_id: str,
        redirect_uri: str,
        code_verifier: Optional[str] = None,
    ) -> dict:
        auth_code = self.codes.get(code)
        if not auth_code:
            return {"error": "invalid_grant"}
        if auth_code.used:
            return {"error": "invalid_grant"}
        if auth_code.client_id != client_id:
            return {"error": "invalid_grant"}
        if auth_code.redirect_uri != redirect_uri:
            return {"error": "invalid_grant"}
        if auth_code.expires_at < _now_epoch():
            return {"error": "invalid_grant"}

        # PKCE verification
        if auth_code.code_challenge:
            if not code_verifier:
                return {"error": "invalid_grant"}
            method = auth_code.code_challenge_method or "S256"
            if method == "S256":
                import base64
                computed = base64.urlsafe_b64encode(
                    hashlib.sha256(code_verifier.encode()).digest()
                ).rstrip(b"=").decode()
            else:
                computed = code_verifier
            if computed != auth_code.code_challenge:
                return {"error": "invalid_grant"}

        # Mark as used
        auth_code.used = True

        # Generate tokens
        access = self._create_access_token(client_id, auth_code.user_id, auth_code.scope)
        refresh = self._create_refresh_token(client_id, auth_code.user_id, auth_code.scope)

        logger.info("code_exchanged", client_id=client_id, user_id=auth_code.user_id)
        return {
            "access_token": access.token,
            "refresh_token": refresh.token,
            "expires_in": ACCESS_TOKEN_TTL,
            "scope": auth_code.scope,
        }

    # ── Token Management ──

    def _create_access_token(self, client_id: str, user_id: str, scope: str) -> OAuthToken:
        token = OAuthToken(
            token=_generate_token(32),
            token_type="access",
            client_id=client_id,
            user_id=user_id,
            scope=scope,
            expires_at=_now_epoch() + ACCESS_TOKEN_TTL,
        )
        self.access_tokens[token.token] = token
        return token

    def _create_refresh_token(self, client_id: str, user_id: str, scope: str) -> OAuthToken:
        token = OAuthToken(
            token=_generate_token(32),
            token_type="refresh",
            client_id=client_id,
            user_id=user_id,
            scope=scope,
            expires_at=_now_epoch() + REFRESH_TOKEN_TTL,
        )
        self.refresh_tokens[token.token] = token
        return token

    def validate_access_token(self, token_str: str) -> Optional[OAuthToken]:
        token = self.access_tokens.get(token_str)
        if not token:
            return None
        if token.revoked:
            return None
        if token.expires_at < _now_epoch():
            return None
        return token

    def refresh_access_token(self, refresh_token_str: str, client_id: str) -> dict:
        token = self.refresh_tokens.get(refresh_token_str)
        if not token:
            return {"error": "invalid_grant"}
        if token.revoked:
            return {"error": "invalid_grant"}
        if token.client_id != client_id:
            return {"error": "invalid_grant"}
        if token.expires_at < _now_epoch():
            return {"error": "invalid_grant"}

        new_access = self._create_access_token(client_id, token.user_id, token.scope)
        logger.info("token_refreshed", client_id=client_id, user_id=token.user_id)
        return {
            "access_token": new_access.token,
            "expires_in": ACCESS_TOKEN_TTL,
            "scope": token.scope,
        }

    def revoke_token(self, token_str: str) -> None:
        if token_str in self.access_tokens:
            self.access_tokens[token_str].revoked = True
        if token_str in self.refresh_tokens:
            self.refresh_tokens[token_str].revoked = True
        logger.info("token_revoked", token=token_str[:8] + "...")

    # ── Server Metadata (RFC 8414) ──

    def get_server_metadata(self, domain: str) -> dict:
        return {
            "issuer": domain,
            "authorization_endpoint": f"{domain}/oauth2/authorize",
            "token_endpoint": f"{domain}/oauth2/token",
            "revocation_endpoint": f"{domain}/oauth2/revoke",
            "scopes_supported": list(UCP_SCOPES),
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic"],
            "code_challenge_methods_supported": ["S256", "plain"],
        }
