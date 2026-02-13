import express, { Request, Response, NextFunction } from 'express';
import rateLimit from 'express-rate-limit';
import { UCPServer } from './ucp-server';
import { UCPServerDB } from './ucp-server-db';
import { MerchantConfig, UCP_VERSION, CreateCheckoutRequestSchema, UpdateCheckoutRequestSchema } from './schema';
import { isDbHealthy, db } from './db';
import { logger } from './logger';
import { constructWebhookEvent, isStripeConfigured } from './stripe';
import { getPayPalOrder, capturePayPalOrder } from './paypal';

type UCPServerType = UCPServer | UCPServerDB;

// Rate limiters
const apiLimiter = rateLimit({
  windowMs: 60 * 1000, // 1 minute
  max: 100, // 100 requests per minute
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Too many requests, please try again later' },
});

const checkoutLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 20, // 20 checkout creates per minute
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Too many checkout requests' },
});

export function createExpressApp(config: MerchantConfig, options?: { useDb?: boolean }): express.Application {
  const app = express();
  const ucpServer: UCPServerType = options?.useDb
    ? new UCPServerDB(config)
    : new UCPServer(config);
  const useDb = options?.useDb ?? false;

  // Apply rate limiting to API routes
  app.use('/ucp/v1/', apiLimiter);

  // Stripe webhook needs raw body - must come before json parser
  app.post('/webhooks/stripe', express.raw({ type: 'application/json' }), (req: Request, res: Response) => {
    const signature = req.headers['stripe-signature'] as string;
    if (!signature) {
      res.status(400).json({ error: 'Missing stripe-signature header' });
      return;
    }

    const event = constructWebhookEvent(req.body, signature);
    if (!event) {
      res.status(400).json({ error: 'Invalid webhook signature' });
      return;
    }

    logger.info({ eventType: event.type, eventId: event.id }, 'Stripe webhook received');

    switch (event.type) {
      case 'payment_intent.succeeded': {
        const paymentIntent = event.data.object as { id: string; metadata?: { checkout_id?: string; order_id?: string } };
        const checkoutId = paymentIntent.metadata?.checkout_id;
        const orderId = paymentIntent.metadata?.order_id;

        if (checkoutId && useDb) {
          db.prepare(`UPDATE checkouts SET payment_status = 'succeeded', updated_at = ? WHERE id = ?`)
            .run(new Date().toISOString(), checkoutId);
          if (orderId) {
            db.prepare(`UPDATE orders SET payment_status = 'succeeded' WHERE id = ?`).run(orderId);
          }
          logger.info({ checkoutId, orderId, paymentIntentId: paymentIntent.id }, 'Payment succeeded');
        }
        break;
      }
      case 'payment_intent.payment_failed': {
        const paymentIntent = event.data.object as { id: string; metadata?: { checkout_id?: string; order_id?: string }; last_payment_error?: { message?: string } };
        const checkoutId = paymentIntent.metadata?.checkout_id;
        const orderId = paymentIntent.metadata?.order_id;

        if (checkoutId && useDb) {
          db.prepare(`UPDATE checkouts SET payment_status = 'failed', updated_at = ? WHERE id = ?`)
            .run(new Date().toISOString(), checkoutId);
          if (orderId) {
            db.prepare(`UPDATE orders SET payment_status = 'failed' WHERE id = ?`).run(orderId);
          }
          logger.error({
            checkoutId, orderId, paymentIntentId: paymentIntent.id,
            error: paymentIntent.last_payment_error?.message
          }, 'Payment failed');
        }
        break;
      }
    }

    res.json({ received: true });
  });

  // PayPal webhook - capture completed orders
  app.post('/webhooks/paypal', express.json(), async (req: Request, res: Response) => {
    const eventType = req.body.event_type;
    const resource = req.body.resource;

    logger.info({ eventType, resourceId: resource?.id }, 'PayPal webhook received');

    if (eventType === 'CHECKOUT.ORDER.APPROVED' && resource?.id) {
      try {
        // Capture the payment
        const capturedOrder = await capturePayPalOrder(resource.id);
        if (capturedOrder && capturedOrder.status === 'COMPLETED') {
          // Get the checkout ID from the order
          const orderDetails = await getPayPalOrder(resource.id);
          const checkoutId = orderDetails?.customId;

          if (checkoutId && useDb) {
            const now = new Date().toISOString();
            db.prepare(`UPDATE checkouts SET payment_status = 'succeeded', updated_at = ? WHERE paypal_order_id = ?`)
              .run(now, resource.id);
            db.prepare(`UPDATE orders SET payment_status = 'succeeded' WHERE paypal_order_id = ?`)
              .run(resource.id);
            logger.info({ checkoutId, paypalOrderId: resource.id }, 'PayPal payment captured and succeeded');
          }
        }
      } catch (error) {
        logger.error({ error, paypalOrderId: resource.id }, 'Failed to capture PayPal order');
      }
    } else if (eventType === 'PAYMENT.CAPTURE.DENIED' && resource?.id) {
      if (useDb) {
        const now = new Date().toISOString();
        db.prepare(`UPDATE checkouts SET payment_status = 'failed', updated_at = ? WHERE paypal_order_id = ?`)
          .run(now, resource.supplementary_data?.related_ids?.order_id);
        db.prepare(`UPDATE orders SET payment_status = 'failed' WHERE paypal_order_id = ?`)
          .run(resource.supplementary_data?.related_ids?.order_id);
        logger.error({ paypalOrderId: resource.id }, 'PayPal payment denied');
      }
    }

    res.json({ received: true });
  });

  app.use(express.json());

  // Request logging middleware
  app.use((req: Request, res: Response, next: NextFunction) => {
    const start = Date.now();
    res.on('finish', () => {
      logger.info({
        method: req.method,
        path: req.path,
        status: res.statusCode,
        duration: Date.now() - start,
      }, 'Request completed');
    });
    next();
  });

  // CORS middleware
  app.use((_req: Request, res: Response, next: NextFunction) => {
    res.header('Access-Control-Allow-Origin', '*');
    res.header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
    res.header('Access-Control-Allow-Headers', 'Content-Type, UCP-Agent, Authorization');
    next();
  });

  // UCP Discovery endpoint
  app.get('/.well-known/ucp', (_req: Request, res: Response) => {
    res.json(ucpServer.getProfile());
  });

  // Create Checkout Session
  app.post('/ucp/v1/checkout-sessions', checkoutLimiter, (req: Request, res: Response) => {
    const parsed = CreateCheckoutRequestSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'Validation failed', details: parsed.error.issues });
      return;
    }
    try {
      const session = ucpServer.createCheckout(parsed.data);
      res.status(201).json(session);
    } catch (error) {
      res.status(400).json({ error: 'Invalid request', details: String(error) });
    }
  });

  // Get Checkout Session
  app.get('/ucp/v1/checkout-sessions/:id', (req: Request, res: Response) => {
    const id = Array.isArray(req.params.id) ? req.params.id[0] : req.params.id;
    const session = ucpServer.getCheckout(id);
    if (!session) {
      res.status(404).json({ error: 'Checkout session not found' });
      return;
    }
    res.json(session);
  });

  // Update Checkout Session
  app.put('/ucp/v1/checkout-sessions/:id', (req: Request, res: Response) => {
    const parsed = UpdateCheckoutRequestSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'Validation failed', details: parsed.error.issues });
      return;
    }
    try {
      const id = Array.isArray(req.params.id) ? req.params.id[0] : req.params.id;
      const session = ucpServer.updateCheckout(id, parsed.data);
      if (!session) {
        res.status(404).json({ error: 'Checkout session not found' });
        return;
      }
      res.json(session);
    } catch (error) {
      res.status(400).json({ error: 'Invalid request', details: String(error) });
    }
  });

  // Complete Checkout
  app.post('/ucp/v1/checkout-sessions/:id/complete', async (req: Request, res: Response) => {
    const id = Array.isArray(req.params.id) ? req.params.id[0] : req.params.id;
    try {
      const result = await ucpServer.completeCheckout(id);
      if ('error' in result) {
        res.status(400).json(result);
        return;
      }
      res.status(201).json(result);
    } catch (error) {
      logger.error({ error, checkoutId: id }, 'Failed to complete checkout');
      res.status(500).json({ error: 'Internal server error' });
    }
  });

  // Cancel Checkout
  app.post('/ucp/v1/checkout-sessions/:id/cancel', (req: Request, res: Response) => {
    const id = Array.isArray(req.params.id) ? req.params.id[0] : req.params.id;
    const session = ucpServer.cancelCheckout(id);
    if (!session) {
      res.status(404).json({ error: 'Checkout session not found' });
      return;
    }
    res.json(session);
  });

  // Get Order
  app.get('/ucp/v1/orders/:id', (req: Request, res: Response) => {
    const id = Array.isArray(req.params.id) ? req.params.id[0] : req.params.id;
    const order = ucpServer.getOrder(id);
    if (!order) {
      res.status(404).json({ error: 'Order not found' });
      return;
    }
    res.json(order);
  });

  // List Orders
  app.get('/ucp/v1/orders', (_req: Request, res: Response) => {
    res.json(ucpServer.listOrders());
  });

  // Product catalog (convenience endpoint)
  app.get('/ucp/v1/items', (_req: Request, res: Response) => {
    res.json(ucpServer.getConfig().items);
  });

  // Admin stats endpoint
  app.get('/admin/stats', (_req: Request, res: Response) => {
    if (!useDb) {
      res.status(501).json({ error: 'Stats require database mode' });
      return;
    }
    try {
      const checkoutStats = db.prepare(`
        SELECT status, COUNT(*) as count FROM checkouts GROUP BY status
      `).all() as { status: string; count: number }[];

      const orderStats = db.prepare(`
        SELECT payment_status, payment_provider, COUNT(*) as count 
        FROM orders GROUP BY payment_status, payment_provider
      `).all() as { payment_status: string; payment_provider: string; count: number }[];

      const revenueResult = db.prepare(`
        SELECT SUM(json_extract(totals_json, '$[?(@.type=="total")].amount')) as total
        FROM orders WHERE payment_status = 'succeeded'
      `).get() as { total: number | null };

      const todayOrders = db.prepare(`
        SELECT COUNT(*) as count FROM orders 
        WHERE date(created_at) = date('now')
      `).get() as { count: number };

      res.json({
        checkouts: Object.fromEntries(checkoutStats.map(s => [s.status, s.count])),
        orders: {
          by_payment_status: Object.fromEntries(orderStats.map(s => [`${s.payment_provider || 'none'}_${s.payment_status}`, s.count])),
          today: todayOrders.count,
        },
        revenue: {
          total_succeeded: revenueResult.total || 0,
        },
        timestamp: new Date().toISOString(),
      });
    } catch (error) {
      logger.error({ error }, 'Failed to get admin stats');
      res.status(500).json({ error: 'Failed to get stats' });
    }
  });

  // Admin dashboard HTML
  app.get('/admin', (_req: Request, res: Response) => {
    const statsScript = useDb
      ? `fetch('/admin/stats').then(r=>r.json()).then(d=>{
  document.getElementById('stats').innerHTML = \`
    <div class="card"><div class="stat">\${Object.values(d.checkouts).reduce((a,b)=>a+b,0)}</div><div class="label">Total Checkouts</div></div>
    <div class="card"><div class="stat">\${d.checkouts.completed||0}</div><div class="label">Completed</div></div>
    <div class="card"><div class="stat">\${d.orders.today||0}</div><div class="label">Orders Today</div></div>
    <div class="card"><div class="stat">$\${(d.revenue.total_succeeded/100).toFixed(2)}</div><div class="label">Revenue</div></div>
  \`;
}).catch(e=>document.getElementById('stats').innerHTML='Error loading stats');`
      : `document.getElementById('stats').innerHTML = '<div class="card" style="grid-column:1/-1"><div class="label">Running in <strong>in-memory mode</strong>. Stats require <code>--db</code> mode.</div><div style="margin-top:8px;font-size:0.85em;color:#999">Restart with: <code>ucpify serve config.json</code> (DB is on by default)</div></div>';`;

    res.send(`<!DOCTYPE html>
<html><head><title>UCP Admin</title>
<style>
  body { font-family: system-ui; max-width: 800px; margin: 40px auto; padding: 20px; }
  .card { background: #f5f5f5; padding: 20px; border-radius: 8px; margin: 10px 0; }
  .stat { font-size: 2em; font-weight: bold; color: #333; }
  .label { color: #666; font-size: 0.9em; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; }
  h1 { color: #333; }
  code { background: #e5e5e5; padding: 2px 6px; border-radius: 3px; font-size: 0.85em; }
</style>
</head><body>
<h1>🛒 UCP Server Admin</h1>
<div class="grid" id="stats">Loading...</div>
<script>${statsScript}</script>
</body></html>`);
  });

  // Health check
  app.get('/health', (_req: Request, res: Response) => {
    const dbHealthy = isDbHealthy();
    const status = dbHealthy ? 'ok' : 'degraded';
    res.status(dbHealthy ? 200 : 503).json({
      status,
      ucp_version: UCP_VERSION,
      database: dbHealthy ? 'connected' : 'disconnected',
      timestamp: new Date().toISOString(),
    });
  });

  return app;
}
