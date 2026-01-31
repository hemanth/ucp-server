import { Client, Environment, OrdersController, ApiError, CheckoutPaymentIntent } from '@paypal/paypal-server-sdk';
import { config } from './config';
import { logger } from './logger';

let paypalClient: Client | null = null;
let ordersController: OrdersController | null = null;

function getPayPalClient(): Client | null {
  if (!config.paypalClientId || !config.paypalClientSecret) {
    return null;
  }
  if (!paypalClient) {
    paypalClient = new Client({
      clientCredentialsAuthCredentials: {
        oAuthClientId: config.paypalClientId,
        oAuthClientSecret: config.paypalClientSecret,
      },
      environment: config.paypalMode === 'live' ? Environment.Production : Environment.Sandbox,
    });
    ordersController = new OrdersController(paypalClient);
    logger.info('PayPal client initialized');
  }
  return paypalClient;
}

export function isPayPalConfigured(): boolean {
  return !!(config.paypalClientId && config.paypalClientSecret);
}

export interface CreatePayPalOrderParams {
  amount: number; // in cents
  currency: string;
  checkoutId: string;
  description?: string;
}

export async function createPayPalOrder(params: CreatePayPalOrderParams): Promise<{ id: string; status: string } | null> {
  const client = getPayPalClient();
  if (!client || !ordersController) {
    logger.warn('PayPal not configured, skipping order creation');
    return null;
  }

  // Convert cents to dollars with 2 decimal places
  const amountValue = (params.amount / 100).toFixed(2);

  try {
    const response = await ordersController.createOrder({
      body: {
        intent: CheckoutPaymentIntent.Capture,
        purchaseUnits: [
          {
            amount: {
              currencyCode: params.currency.toUpperCase(),
              value: amountValue,
            },
            description: params.description || `Checkout ${params.checkoutId}`,
            customId: params.checkoutId,
          },
        ],
      },
      prefer: 'return=representation',
    });

    const order = response.result;
    logger.info({
      paypalOrderId: order.id,
      checkoutId: params.checkoutId,
      amount: params.amount,
    }, 'PayPal order created');

    return { id: order.id!, status: order.status! };
  } catch (error) {
    if (error instanceof ApiError) {
      logger.error({ error: error.message, checkoutId: params.checkoutId }, 'Failed to create PayPal order');
    }
    throw error;
  }
}

export async function capturePayPalOrder(orderId: string): Promise<{ id: string; status: string } | null> {
  const client = getPayPalClient();
  if (!client || !ordersController) return null;

  try {
    const response = await ordersController.captureOrder({
      id: orderId,
      prefer: 'return=representation',
    });

    const order = response.result;
    logger.info({ paypalOrderId: orderId, status: order.status }, 'PayPal order captured');
    return { id: order.id!, status: order.status! };
  } catch (error) {
    if (error instanceof ApiError) {
      logger.error({ error: error.message, paypalOrderId: orderId }, 'Failed to capture PayPal order');
    }
    throw error;
  }
}

export async function getPayPalOrder(orderId: string): Promise<{ id: string; status: string; customId?: string } | null> {
  const client = getPayPalClient();
  if (!client || !ordersController) return null;

  try {
    const response = await ordersController.getOrder({ id: orderId });
    const order = response.result;
    return {
      id: order.id!,
      status: order.status!,
      customId: order.purchaseUnits?.[0]?.customId,
    };
  } catch (error) {
    if (error instanceof ApiError) {
      logger.error({ error: error.message, paypalOrderId: orderId }, 'Failed to get PayPal order');
    }
    throw error;
  }
}
