import { v4 as uuidv4 } from 'uuid';
import { db } from './db';
import { logger } from './logger';
import { createPaymentIntent, isStripeConfigured } from './stripe';
import { createPayPalOrder, isPayPalConfigured } from './paypal';
import {
  MerchantConfig,
  CheckoutSession,
  Order,
  LineItem,
  Buyer,
  Fulfillment,
  CheckoutMessage,
  UCP_VERSION,
  Item,
  OrderConfirmation,
} from './schema';

export class UCPServerDB {
  private config: MerchantConfig;
  private itemsMap: Map<string, Item>;

  constructor(config: MerchantConfig) {
    this.config = config;
    this.itemsMap = new Map(config.items.map((item) => [item.id, item]));
  }

  getProfile() {
    const paymentHandlers: Record<string, { id: string; version: string; config?: Record<string, unknown> }[]> = {};
    for (const handler of this.config.payment_handlers) {
      paymentHandlers[handler.namespace] = [
        { id: handler.id, version: UCP_VERSION, config: handler.config },
      ];
    }

    return {
      ucp: {
        version: UCP_VERSION,
        services: {
          'dev.ucp.shopping': [
            {
              version: UCP_VERSION,
              spec: 'https://ucp.dev/specification/overview',
              transport: 'rest',
              endpoint: `${this.config.domain}/ucp/v1`,
              schema: 'https://ucp.dev/services/shopping/rest.openapi.json',
            },
          ],
        },
        capabilities: {
          'dev.ucp.shopping.checkout': [
            { version: UCP_VERSION, spec: 'https://ucp.dev/specification/checkout', schema: 'https://ucp.dev/schemas/shopping/checkout.json' },
          ],
          'dev.ucp.shopping.fulfillment': [
            { version: UCP_VERSION, spec: 'https://ucp.dev/specification/fulfillment', schema: 'https://ucp.dev/schemas/shopping/fulfillment.json', extends: 'dev.ucp.shopping.checkout' },
          ],
          'dev.ucp.shopping.order': [
            { version: UCP_VERSION, spec: 'https://ucp.dev/specification/order', schema: 'https://ucp.dev/schemas/shopping/order.json' },
          ],
          ...(this.config.oauth ? {
            'dev.ucp.identity_linking': [
              { version: UCP_VERSION, spec: 'https://ucp.dev/specification/identity-linking' },
            ],
          } : {}),
        },
        payment_handlers: paymentHandlers,
      },
    };
  }

  private buildLinks(): { type: string; url: string }[] {
    const links: { type: string; url: string }[] = [];
    if (this.config.terms_url) links.push({ type: 'terms_of_service', url: this.config.terms_url });
    if (this.config.privacy_url) links.push({ type: 'privacy_policy', url: this.config.privacy_url });
    return links;
  }

  private buildPaymentHandlers(): Record<string, { id: string; version: string; config?: Record<string, unknown> }[]> {
    const paymentHandlers: Record<string, { id: string; version: string; config?: Record<string, unknown> }[]> = {};
    for (const handler of this.config.payment_handlers) {
      paymentHandlers[handler.namespace] = [{ id: handler.id, version: UCP_VERSION, config: handler.config }];
    }
    return paymentHandlers;
  }

  private validateCheckout(session: { buyer?: Buyer; fulfillment?: Fulfillment }): CheckoutMessage[] {
    const messages: CheckoutMessage[] = [];
    if (!session.buyer?.email) {
      messages.push({ type: 'error', code: 'missing', path: '$.buyer.email', content: 'Buyer email is required', severity: 'recoverable' });
    }
    if (!session.fulfillment?.methods?.[0]?.selected_destination_id) {
      messages.push({ type: 'error', code: 'missing', path: '$.fulfillment.methods[0].selected_destination_id', content: 'Shipping address is required', severity: 'recoverable' });
    }
    return messages;
  }

  private calculateTotals(lineItems: LineItem[], shippingTotal = 0): { type: string; amount: number }[] {
    const subtotal = lineItems.reduce((sum, li) => sum + li.item.price * li.quantity, 0);
    const tax = Math.round(subtotal * this.config.tax_rate);
    const totals = [
      { type: 'subtotal', amount: subtotal },
      { type: 'tax', amount: tax },
      { type: 'total', amount: subtotal + shippingTotal + tax },
    ];
    if (shippingTotal > 0) {
      totals.splice(1, 0, { type: 'shipping', amount: shippingTotal });
    }
    return totals;
  }

  private sessionFromRow(row: any): CheckoutSession | undefined {
    if (!row) return undefined;

    const lineItems = db.prepare('SELECT * FROM line_items WHERE checkout_id = ?').all(row.id) as any[];

    return {
      ucp: {
        version: UCP_VERSION,
        capabilities: { 'dev.ucp.shopping.checkout': [{ version: UCP_VERSION }] },
        payment_handlers: this.buildPaymentHandlers(),
      },
      id: row.id,
      status: row.status,
      currency: row.currency,
      buyer: row.buyer_json ? JSON.parse(row.buyer_json) : undefined,
      line_items: lineItems.map((li) => ({
        id: li.id,
        item: JSON.parse(li.item_json),
        quantity: li.quantity,
        totals: li.totals_json ? JSON.parse(li.totals_json) : undefined,
      })),
      totals: JSON.parse(row.totals_json),
      links: JSON.parse(row.links_json),
      fulfillment: row.fulfillment_json ? JSON.parse(row.fulfillment_json) : undefined,
      payment: row.payment_json ? JSON.parse(row.payment_json) : undefined,
      messages: row.messages_json ? JSON.parse(row.messages_json) : undefined,
      continue_url: row.continue_url || undefined,
      expires_at: row.expires_at || undefined,
      order: row.order_json ? JSON.parse(row.order_json) : undefined,
    };
  }

  createCheckout(data: { line_items: { id?: string; item: { id: string; title?: string; price?: number }; quantity: number }[] }): CheckoutSession {
    const id = `chk_${uuidv4().replace(/-/g, '').slice(0, 12)}`;
    const now = new Date().toISOString();
    const expiresAt = new Date(Date.now() + 60 * 60 * 1000).toISOString();

    const lineItems: LineItem[] = data.line_items.map((li, idx) => {
      const catalogItem = this.itemsMap.get(li.item.id);
      const item: Item = catalogItem || { id: li.item.id, title: li.item.title || 'Unknown Item', price: li.item.price || 0 };
      const subtotal = item.price * li.quantity;
      return {
        id: li.id || `li_${idx + 1}`,
        item,
        quantity: li.quantity,
        totals: [{ type: 'subtotal', amount: subtotal }, { type: 'total', amount: subtotal }],
      };
    });

    const totals = this.calculateTotals(lineItems);
    const links = this.buildLinks();
    const messages = this.validateCheckout({});
    const status = messages.length > 0 ? 'incomplete' : 'ready_for_complete';
    const continueUrl = `${this.config.domain}/checkout/${id}`;

    const insertCheckout = db.prepare(`
      INSERT INTO checkouts (id, status, currency, totals_json, links_json, messages_json, continue_url, expires_at, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);

    const insertLineItem = db.prepare(`
      INSERT INTO line_items (id, checkout_id, item_json, quantity, totals_json)
      VALUES (?, ?, ?, ?, ?)
    `);

    db.transaction(() => {
      insertCheckout.run(id, status, this.config.currency, JSON.stringify(totals), JSON.stringify(links),
        messages.length > 0 ? JSON.stringify(messages) : null, continueUrl, expiresAt, now, now);
      for (const li of lineItems) {
        insertLineItem.run(li.id, id, JSON.stringify(li.item), li.quantity, JSON.stringify(li.totals));
      }
    })();

    logger.info({ checkoutId: id, lineItems: lineItems.length }, 'Checkout created');
    return this.getCheckout(id)!;
  }

  getCheckout(id: string): CheckoutSession | undefined {
    const row = db.prepare('SELECT * FROM checkouts WHERE id = ?').get(id);
    return this.sessionFromRow(row);
  }

  updateCheckout(id: string, data: { buyer?: Buyer; line_items?: any[]; fulfillment?: Fulfillment }): CheckoutSession | undefined {
    const session = this.getCheckout(id);
    if (!session) return undefined;

    const now = new Date().toISOString();
    let buyer = session.buyer;
    let lineItems = session.line_items;
    let fulfillment = session.fulfillment;

    if (data.buyer) {
      buyer = { ...buyer, ...data.buyer };
    }

    if (data.line_items) {
      lineItems = data.line_items.map((li, idx) => {
        const catalogItem = this.itemsMap.get(li.item.id);
        const item: Item = catalogItem || { id: li.item.id, title: li.item.title || 'Unknown Item', price: li.item.price || 0 };
        const subtotal = item.price * li.quantity;
        return {
          id: li.id || `li_${idx + 1}`,
          item,
          quantity: li.quantity,
          totals: [{ type: 'subtotal', amount: subtotal }, { type: 'total', amount: subtotal }],
        };
      });
    }

    if (data.fulfillment) {
      const methods = data.fulfillment.methods.map((method, mIdx) => {
        const destinations = method.destinations.map((dest, dIdx) => ({ ...dest, id: dest.id || `dest_${dIdx + 1}` }));
        const groups = method.groups?.length ? method.groups : [{
          id: `group_${mIdx + 1}`,
          line_item_ids: lineItems.map((li) => li.id),
          selected_option_id: this.config.shipping_options[0]?.id,
          options: this.config.shipping_options.map((opt) => ({
            id: opt.id, title: opt.title, description: opt.description || opt.estimated_days,
            totals: [{ type: 'total', amount: opt.price }],
          })),
        }];
        return {
          id: method.id || `method_${mIdx + 1}`,
          type: method.type,
          line_item_ids: method.line_item_ids || lineItems.map((li) => li.id),
          selected_destination_id: method.selected_destination_id || destinations[0]?.id,
          destinations,
          groups,
        };
      });
      fulfillment = { methods };
    }

    // Calculate shipping total
    let shippingTotal = 0;
    if (fulfillment) {
      for (const method of fulfillment.methods) {
        for (const group of method.groups) {
          const selectedOption = group.options.find((o) => o.id === group.selected_option_id);
          if (selectedOption) {
            const total = selectedOption.totals.find((t) => t.type === 'total');
            shippingTotal += total?.amount || 0;
          }
        }
      }
    }

    const totals = this.calculateTotals(lineItems, shippingTotal);
    const messages = this.validateCheckout({ buyer, fulfillment });
    const status = messages.length > 0 ? 'incomplete' : 'ready_for_complete';

    db.transaction(() => {
      db.prepare(`
        UPDATE checkouts SET status = ?, buyer_json = ?, fulfillment_json = ?, totals_json = ?, messages_json = ?, updated_at = ?
        WHERE id = ?
      `).run(status, buyer ? JSON.stringify(buyer) : null, fulfillment ? JSON.stringify(fulfillment) : null,
        JSON.stringify(totals), messages.length > 0 ? JSON.stringify(messages) : null, now, id);

      db.prepare('DELETE FROM line_items WHERE checkout_id = ?').run(id);
      const insertLineItem = db.prepare('INSERT INTO line_items (id, checkout_id, item_json, quantity, totals_json) VALUES (?, ?, ?, ?, ?)');
      for (const li of lineItems) {
        insertLineItem.run(li.id, id, JSON.stringify(li.item), li.quantity, JSON.stringify(li.totals));
      }
    })();

    logger.info({ checkoutId: id, status }, 'Checkout updated');
    return this.getCheckout(id);
  }

  async completeCheckout(id: string): Promise<CheckoutSession | { error: string }> {
    const session = this.getCheckout(id);
    if (!session) return { error: 'Checkout session not found' };
    if (session.status !== 'ready_for_complete') {
      return { error: 'Checkout is not ready for completion', messages: session.messages } as any;
    }

    const orderId = `order_${uuidv4().replace(/-/g, '').slice(0, 12)}`;
    const now = new Date().toISOString();

    // Get total amount for payment
    const totalAmount = session.totals.find(t => t.type === 'total')?.amount || 0;

    // Payment processing - try Stripe first, then PayPal
    let paymentIntentId: string | null = null;
    let paypalOrderId: string | null = null;
    let paymentProvider: string | null = null;
    let paymentStatus = 'none';

    if (totalAmount > 0) {
      // Try Stripe first
      if (isStripeConfigured()) {
        try {
          const paymentIntent = await createPaymentIntent({
            amount: totalAmount,
            currency: session.currency,
            checkoutId: id,
            customerEmail: session.buyer?.email,
            metadata: { order_id: orderId },
          });
          if (paymentIntent) {
            paymentIntentId = paymentIntent.id;
            paymentProvider = 'stripe';
            paymentStatus = 'pending';
            logger.info({ checkoutId: id, paymentIntentId, amount: totalAmount }, 'Stripe payment intent created');
          }
        } catch (error) {
          logger.error({ error, checkoutId: id }, 'Failed to create Stripe payment intent');
          return { error: 'Payment processing failed' };
        }
      }
      // Fall back to PayPal if Stripe not configured
      else if (isPayPalConfigured()) {
        try {
          const ppOrder = await createPayPalOrder({
            amount: totalAmount,
            currency: session.currency,
            checkoutId: id,
            description: `Order ${orderId}`,
          });
          if (ppOrder) {
            paypalOrderId = ppOrder.id;
            paymentProvider = 'paypal';
            paymentStatus = 'pending';
            logger.info({ checkoutId: id, paypalOrderId, amount: totalAmount }, 'PayPal order created');
          }
        } catch (error) {
          logger.error({ error, checkoutId: id }, 'Failed to create PayPal order');
          return { error: 'Payment processing failed' };
        }
      }
    }

    const orderFulfillment = session.fulfillment ? {
      expectations: session.fulfillment.methods.map((m, idx) => ({
        id: `exp_${idx + 1}`,
        line_items: m.line_item_ids.map((liId) => {
          const li = session.line_items.find((l) => l.id === liId);
          return { id: liId, quantity: li?.quantity || 1 };
        }),
        method_type: m.type,
        destination: m.destinations.find((d) => d.id === m.selected_destination_id),
        description: 'Arrives in 5-7 business days',
        fulfillable_on: 'now',
      })),
      events: [],
    } : undefined;

    const orderConfirmation: OrderConfirmation = {
      id: orderId,
      permalink_url: `${this.config.domain}/orders/${orderId}`,
    };

    db.transaction(() => {
      db.prepare(`
        INSERT INTO orders (id, checkout_id, permalink_url, buyer_json, line_items_json, totals_json, fulfillment_json, payment_intent_id, paypal_order_id, payment_provider, payment_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).run(orderId, id, orderConfirmation.permalink_url, session.buyer ? JSON.stringify(session.buyer) : null,
        JSON.stringify(session.line_items), JSON.stringify(session.totals),
        orderFulfillment ? JSON.stringify(orderFulfillment) : null, paymentIntentId, paypalOrderId, paymentProvider, paymentStatus, now);

      db.prepare(`
        UPDATE checkouts SET status = 'completed', order_json = ?, continue_url = NULL, messages_json = NULL, payment_intent_id = ?, paypal_order_id = ?, payment_provider = ?, payment_status = ?, updated_at = ?
        WHERE id = ?
      `).run(JSON.stringify(orderConfirmation), paymentIntentId, paypalOrderId, paymentProvider, paymentStatus, now, id);
    })();

    logger.info({ checkoutId: id, orderId, paymentProvider, paymentIntentId, paypalOrderId }, 'Checkout completed, order created');
    return this.getCheckout(id)!;
  }

  cancelCheckout(id: string): CheckoutSession | undefined {
    const session = this.getCheckout(id);
    if (!session) return undefined;

    db.prepare(`UPDATE checkouts SET status = 'canceled', continue_url = NULL, updated_at = ? WHERE id = ?`)
      .run(new Date().toISOString(), id);

    logger.info({ checkoutId: id }, 'Checkout canceled');
    return this.getCheckout(id);
  }

  getOrder(id: string): Order | undefined {
    const row = db.prepare('SELECT * FROM orders WHERE id = ?').get(id) as any;
    if (!row) return undefined;

    return {
      ucp: { version: UCP_VERSION, capabilities: { 'dev.ucp.shopping.order': [{ version: UCP_VERSION }] } },
      id: row.id,
      checkout_id: row.checkout_id,
      permalink_url: row.permalink_url,
      line_items: JSON.parse(row.line_items_json),
      buyer: row.buyer_json ? JSON.parse(row.buyer_json) : undefined,
      totals: JSON.parse(row.totals_json),
      fulfillment: row.fulfillment_json ? JSON.parse(row.fulfillment_json) : undefined,
      created_at: row.created_at,
    };
  }

  listOrders(): Order[] {
    const rows = db.prepare('SELECT * FROM orders ORDER BY created_at DESC').all() as any[];
    return rows.map((row) => this.getOrder(row.id)!);
  }

  getConfig(): MerchantConfig {
    return this.config;
  }
}
