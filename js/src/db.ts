import Database, { Database as DatabaseType } from 'better-sqlite3';
import { config } from './config';
import { logger } from './logger';
import * as fs from 'fs';
import * as path from 'path';

// Ensure data directory exists
const dataDir = path.dirname(config.databasePath);
if (!fs.existsSync(dataDir)) {
  fs.mkdirSync(dataDir, { recursive: true });
}

export const db: DatabaseType = new Database(config.databasePath);

// Enable WAL mode for better concurrent access
db.pragma('journal_mode = WAL');

// Initialize schema
const schema = `
CREATE TABLE IF NOT EXISTS checkouts (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  currency TEXT NOT NULL,
  buyer_json TEXT,
  fulfillment_json TEXT,
  payment_json TEXT,
  totals_json TEXT NOT NULL,
  links_json TEXT NOT NULL,
  messages_json TEXT,
  continue_url TEXT,
  expires_at TEXT,
  order_json TEXT,
  payment_intent_id TEXT,
  paypal_order_id TEXT,
  payment_provider TEXT,
  payment_status TEXT DEFAULT 'none',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS line_items (
  id TEXT NOT NULL,
  checkout_id TEXT NOT NULL,
  item_json TEXT NOT NULL,
  quantity INTEGER NOT NULL,
  totals_json TEXT,
  PRIMARY KEY (id, checkout_id),
  FOREIGN KEY (checkout_id) REFERENCES checkouts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY,
  checkout_id TEXT NOT NULL,
  permalink_url TEXT,
  buyer_json TEXT,
  line_items_json TEXT NOT NULL,
  totals_json TEXT NOT NULL,
  fulfillment_json TEXT,
  payment_intent_id TEXT,
  paypal_order_id TEXT,
  payment_provider TEXT,
  payment_status TEXT DEFAULT 'pending',
  created_at TEXT NOT NULL,
  FOREIGN KEY (checkout_id) REFERENCES checkouts(id)
);

CREATE TABLE IF NOT EXISTS inventory (
  item_id TEXT PRIMARY KEY,
  stock INTEGER NOT NULL DEFAULT 0,
  reserved INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_clients (
  client_id TEXT PRIMARY KEY,
  client_secret_hash TEXT NOT NULL,
  name TEXT NOT NULL,
  redirect_uris_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_codes (
  code TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  scope TEXT NOT NULL,
  redirect_uri TEXT NOT NULL,
  code_challenge TEXT,
  code_challenge_method TEXT,
  expires_at INTEGER NOT NULL,
  used INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (client_id) REFERENCES oauth_clients(client_id)
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
  token TEXT PRIMARY KEY,
  token_type TEXT NOT NULL,
  client_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  scope TEXT NOT NULL,
  parent_token TEXT,
  expires_at INTEGER NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (client_id) REFERENCES oauth_clients(client_id)
);

CREATE INDEX IF NOT EXISTS idx_line_items_checkout ON line_items(checkout_id);
CREATE INDEX IF NOT EXISTS idx_orders_checkout ON orders(checkout_id);
CREATE INDEX IF NOT EXISTS idx_checkouts_status ON checkouts(status);
CREATE INDEX IF NOT EXISTS idx_oauth_tokens_client ON oauth_tokens(client_id);
CREATE INDEX IF NOT EXISTS idx_oauth_codes_client ON oauth_codes(client_id);
`;

db.exec(schema);
logger.info({ path: config.databasePath }, 'Database initialized');

// Helper functions
export function getDb(): Database.Database {
  return db;
}

export function isDbHealthy(): boolean {
  try {
    db.prepare('SELECT 1').get();
    return true;
  } catch {
    return false;
  }
}
