import 'dotenv/config';

export const config = {
  // Server
  port: parseInt(process.env.PORT || '3000', 10),
  nodeEnv: process.env.NODE_ENV || 'development',
  
  // Database
  databasePath: process.env.DATABASE_PATH || './data/ucp.db',
  
  // Logging
  logLevel: process.env.LOG_LEVEL || 'info',
  
  // UCP
  domain: process.env.UCP_DOMAIN || 'http://localhost:3000',
  
  // Stripe
  stripeSecretKey: process.env.STRIPE_SECRET_KEY || '',
  stripeWebhookSecret: process.env.STRIPE_WEBHOOK_SECRET || '',
  
  // PayPal
  paypalClientId: process.env.PAYPAL_CLIENT_ID || '',
  paypalClientSecret: process.env.PAYPAL_CLIENT_SECRET || '',
  paypalMode: (process.env.PAYPAL_MODE || 'sandbox') as 'sandbox' | 'live',
};

export type Config = typeof config;
