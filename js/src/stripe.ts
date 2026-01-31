import Stripe from 'stripe';
import { config } from './config';
import { logger } from './logger';

let stripeClient: Stripe | null = null;

export function getStripe(): Stripe | null {
  if (!config.stripeSecretKey) {
    return null;
  }
  if (!stripeClient) {
    stripeClient = new Stripe(config.stripeSecretKey, {
      apiVersion: '2026-01-28.clover',
    });
    logger.info('Stripe client initialized');
  }
  return stripeClient;
}

export function isStripeConfigured(): boolean {
  return !!config.stripeSecretKey;
}

export interface CreatePaymentIntentParams {
  amount: number; // in cents
  currency: string;
  checkoutId: string;
  customerEmail?: string;
  metadata?: Record<string, string>;
}

export async function createPaymentIntent(params: CreatePaymentIntentParams): Promise<Stripe.PaymentIntent | null> {
  const stripe = getStripe();
  if (!stripe) {
    logger.warn('Stripe not configured, skipping payment intent creation');
    return null;
  }

  try {
    const paymentIntent = await stripe.paymentIntents.create({
      amount: params.amount,
      currency: params.currency.toLowerCase(),
      metadata: {
        checkout_id: params.checkoutId,
        ...params.metadata,
      },
      receipt_email: params.customerEmail,
      automatic_payment_methods: {
        enabled: true,
      },
    }, {
      idempotencyKey: `checkout_${params.checkoutId}`,
    });

    logger.info({ 
      paymentIntentId: paymentIntent.id, 
      checkoutId: params.checkoutId,
      amount: params.amount,
    }, 'Payment intent created');

    return paymentIntent;
  } catch (error) {
    logger.error({ error, checkoutId: params.checkoutId }, 'Failed to create payment intent');
    throw error;
  }
}

export async function confirmPaymentIntent(paymentIntentId: string): Promise<Stripe.PaymentIntent | null> {
  const stripe = getStripe();
  if (!stripe) return null;

  try {
    const paymentIntent = await stripe.paymentIntents.retrieve(paymentIntentId);
    logger.info({ paymentIntentId, status: paymentIntent.status }, 'Payment intent retrieved');
    return paymentIntent;
  } catch (error) {
    logger.error({ error, paymentIntentId }, 'Failed to retrieve payment intent');
    throw error;
  }
}

export async function cancelPaymentIntent(paymentIntentId: string): Promise<Stripe.PaymentIntent | null> {
  const stripe = getStripe();
  if (!stripe) return null;

  try {
    const paymentIntent = await stripe.paymentIntents.cancel(paymentIntentId);
    logger.info({ paymentIntentId }, 'Payment intent canceled');
    return paymentIntent;
  } catch (error) {
    logger.error({ error, paymentIntentId }, 'Failed to cancel payment intent');
    throw error;
  }
}

export function constructWebhookEvent(payload: string | Buffer, signature: string): Stripe.Event | null {
  const stripe = getStripe();
  if (!stripe || !config.stripeWebhookSecret) return null;

  try {
    return stripe.webhooks.constructEvent(payload, signature, config.stripeWebhookSecret);
  } catch (error) {
    logger.error({ error }, 'Webhook signature verification failed');
    return null;
  }
}
