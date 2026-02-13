import crypto from 'crypto';
import { logger } from './logger';

// ─── Types ───────────────────────────────────────────────────────────

export interface OAuthClient {
    client_id: string;
    client_secret: string;
    client_secret_hash: string;
    name: string;
    redirect_uris: string[];
    created_at: string;
}

export interface AuthorizationCode {
    code: string;
    client_id: string;
    user_id: string;
    scope: string;
    redirect_uri: string;
    code_challenge?: string;
    code_challenge_method?: string;
    expires_at: number;
    used: boolean;
}

export interface OAuthToken {
    token: string;
    token_type: 'access' | 'refresh';
    client_id: string;
    user_id: string;
    scope: string;
    parent_token?: string;
    expires_at: number;
    revoked: boolean;
}

// ─── Constants ───────────────────────────────────────────────────────

export const UCP_SCOPES = ['ucp:scopes:checkout_session'] as const;
const ACCESS_TOKEN_TTL = 60 * 60;         // 1 hour
const REFRESH_TOKEN_TTL = 60 * 60 * 24 * 30; // 30 days
const AUTH_CODE_TTL = 60 * 10;            // 10 minutes

// ─── Helpers ─────────────────────────────────────────────────────────

function generateToken(bytes = 32): string {
    return crypto.randomBytes(bytes).toString('hex');
}

function hashSecret(secret: string): string {
    return crypto.createHash('sha256').update(secret).digest('hex');
}

function nowEpoch(): number {
    return Math.floor(Date.now() / 1000);
}

// ─── OAuth Manager ───────────────────────────────────────────────────

export class OAuthManager {
    private clients: Map<string, OAuthClient> = new Map();
    private codes: Map<string, AuthorizationCode> = new Map();
    private accessTokens: Map<string, OAuthToken> = new Map();
    private refreshTokens: Map<string, OAuthToken> = new Map();

    // ── Client Management ──

    createClient(name: string, redirectUris: string[]): OAuthClient {
        const clientId = `ucp_${generateToken(16)}`;
        const clientSecret = `ucp_secret_${generateToken(24)}`;
        const client: OAuthClient = {
            client_id: clientId,
            client_secret: clientSecret,
            client_secret_hash: hashSecret(clientSecret),
            name,
            redirect_uris: redirectUris,
            created_at: new Date().toISOString(),
        };
        this.clients.set(clientId, client);
        logger.info({ clientId, name }, 'OAuth client created');
        return client;
    }

    getClient(clientId: string): OAuthClient | undefined {
        return this.clients.get(clientId);
    }

    authenticateClient(clientId: string, clientSecret: string): boolean {
        const client = this.clients.get(clientId);
        if (!client) return false;
        return client.client_secret_hash === hashSecret(clientSecret);
    }

    // ── Authorization Code ──

    createAuthorizationCode(
        clientId: string,
        userId: string,
        scope: string,
        redirectUri: string,
        codeChallenge?: string,
        codeChallengeMethod?: string,
    ): string {
        const code = generateToken(32);
        const authCode: AuthorizationCode = {
            code,
            client_id: clientId,
            user_id: userId,
            scope,
            redirect_uri: redirectUri,
            code_challenge: codeChallenge,
            code_challenge_method: codeChallengeMethod,
            expires_at: nowEpoch() + AUTH_CODE_TTL,
            used: false,
        };
        this.codes.set(code, authCode);
        logger.info({ clientId, userId, scope }, 'Authorization code created');
        return code;
    }

    exchangeCode(
        code: string,
        clientId: string,
        redirectUri: string,
        codeVerifier?: string,
    ): { access_token: string; refresh_token: string; expires_in: number; scope: string } | { error: string } {
        const authCode = this.codes.get(code);

        if (!authCode) return { error: 'invalid_grant' };
        if (authCode.used) return { error: 'invalid_grant' };
        if (authCode.client_id !== clientId) return { error: 'invalid_grant' };
        if (authCode.redirect_uri !== redirectUri) return { error: 'invalid_grant' };
        if (authCode.expires_at < nowEpoch()) return { error: 'invalid_grant' };

        // PKCE verification
        if (authCode.code_challenge) {
            if (!codeVerifier) return { error: 'invalid_grant' };
            const method = authCode.code_challenge_method || 'S256';
            let computed: string;
            if (method === 'S256') {
                computed = crypto.createHash('sha256').update(codeVerifier).digest('base64url');
            } else {
                computed = codeVerifier;
            }
            if (computed !== authCode.code_challenge) return { error: 'invalid_grant' };
        }

        // Mark code as used
        authCode.used = true;
        this.codes.set(code, authCode);

        // Generate tokens
        const accessToken = this.createAccessToken(clientId, authCode.user_id, authCode.scope);
        const refreshToken = this.createRefreshToken(clientId, authCode.user_id, authCode.scope);

        logger.info({ clientId, userId: authCode.user_id }, 'Authorization code exchanged for tokens');

        return {
            access_token: accessToken,
            refresh_token: refreshToken,
            expires_in: ACCESS_TOKEN_TTL,
            scope: authCode.scope,
        };
    }

    // ── Token Management ──

    private createAccessToken(clientId: string, userId: string, scope: string): string {
        const token = generateToken(32);
        this.accessTokens.set(token, {
            token,
            token_type: 'access',
            client_id: clientId,
            user_id: userId,
            scope,
            expires_at: nowEpoch() + ACCESS_TOKEN_TTL,
            revoked: false,
        });
        return token;
    }

    private createRefreshToken(clientId: string, userId: string, scope: string): string {
        const token = generateToken(32);
        this.refreshTokens.set(token, {
            token,
            token_type: 'refresh',
            client_id: clientId,
            user_id: userId,
            scope,
            expires_at: nowEpoch() + REFRESH_TOKEN_TTL,
            revoked: false,
        });
        return token;
    }

    refreshAccessToken(
        refreshToken: string,
        clientId: string,
    ): { access_token: string; expires_in: number; scope: string } | { error: string } {
        const rt = this.refreshTokens.get(refreshToken);
        if (!rt) return { error: 'invalid_grant' };
        if (rt.revoked) return { error: 'invalid_grant' };
        if (rt.client_id !== clientId) return { error: 'invalid_grant' };
        if (rt.expires_at < nowEpoch()) return { error: 'invalid_grant' };

        const accessToken = this.createAccessToken(clientId, rt.user_id, rt.scope);
        logger.info({ clientId, userId: rt.user_id }, 'Access token refreshed');

        return {
            access_token: accessToken,
            expires_in: ACCESS_TOKEN_TTL,
            scope: rt.scope,
        };
    }

    validateAccessToken(token: string): OAuthToken | null {
        const t = this.accessTokens.get(token);
        if (!t) return null;
        if (t.revoked) return null;
        if (t.expires_at < nowEpoch()) return null;
        return t;
    }

    revokeToken(token: string): boolean {
        // Check access tokens
        const at = this.accessTokens.get(token);
        if (at) {
            at.revoked = true;
            this.accessTokens.set(token, at);
            logger.info({ tokenType: 'access', clientId: at.client_id }, 'Token revoked');
            return true;
        }

        // Check refresh tokens - also revoke all associated access tokens
        const rt = this.refreshTokens.get(token);
        if (rt) {
            rt.revoked = true;
            this.refreshTokens.set(token, rt);
            // Revoke all access tokens for same client+user
            for (const [key, accessToken] of this.accessTokens) {
                if (accessToken.client_id === rt.client_id && accessToken.user_id === rt.user_id) {
                    accessToken.revoked = true;
                    this.accessTokens.set(key, accessToken);
                }
            }
            logger.info({ tokenType: 'refresh', clientId: rt.client_id }, 'Refresh token and associated access tokens revoked');
            return true;
        }

        return false;
    }

    // ── Metadata ──

    getServerMetadata(issuer: string): Record<string, unknown> {
        return {
            issuer,
            authorization_endpoint: `${issuer}/oauth2/authorize`,
            token_endpoint: `${issuer}/oauth2/token`,
            revocation_endpoint: `${issuer}/oauth2/revoke`,
            scopes_supported: [...UCP_SCOPES],
            response_types_supported: ['code'],
            grant_types_supported: ['authorization_code', 'refresh_token'],
            token_endpoint_auth_methods_supported: ['client_secret_basic'],
            code_challenge_methods_supported: ['S256'],
            service_documentation: `${issuer}/docs/oauth2`,
        };
    }
}
