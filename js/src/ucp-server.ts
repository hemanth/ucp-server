import { v4 as uuidv4 } from 'uuid';
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

// In-memory stores
const checkoutSessions: Map<string, CheckoutSession> = new Map();
const orders: Map<string, Order> = new Map();

export class UCPServer {
  private config: MerchantConfig;
  private itemsMap: Map<string, Item>;

  constructor(config: MerchantConfig) {
    this.config = config;
    this.itemsMap = new Map(config.items.map((item) => [item.id, item]));
  }

  // Generate UCP profile for /.well-known/ucp
  getProfile() {
    const paymentHandlers: Record<string, { id: string; version: string; config?: Record<string, unknown> }[]> = {};
    for (const handler of this.config.payment_handlers) {
      paymentHandlers[handler.namespace] = [
        {
          id: handler.id,
          version: UCP_VERSION,
          config: handler.config,
        },
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
            {
              version: UCP_VERSION,
              spec: 'https://ucp.dev/specification/checkout',
              schema: 'https://ucp.dev/schemas/shopping/checkout.json',
            },
          ],
          'dev.ucp.shopping.fulfillment': [
            {
              version: UCP_VERSION,
              spec: 'https://ucp.dev/specification/fulfillment',
              schema: 'https://ucp.dev/schemas/shopping/fulfillment.json',
              extends: 'dev.ucp.shopping.checkout',
            },
          ],
          'dev.ucp.shopping.order': [
            {
              version: UCP_VERSION,
              spec: 'https://ucp.dev/specification/order',
              schema: 'https://ucp.dev/schemas/shopping/order.json',
            },
          ],
        },
        payment_handlers: paymentHandlers,
      },
    };
  }

  // Calculate totals for line items
  private calculateLineItemTotals(lineItems: LineItem[]): { type: string; amount: number }[] {
    const subtotal = lineItems.reduce((sum, li) => sum + li.item.price * li.quantity, 0);
    const tax = Math.round(subtotal * this.config.tax_rate);
    return [
      { type: 'subtotal', amount: subtotal },
      { type: 'tax', amount: tax },
      { type: 'total', amount: subtotal + tax },
    ];
  }

  // Validate checkout and generate messages
  private validateCheckout(session: Partial<CheckoutSession>): CheckoutMessage[] {
    const messages: CheckoutMessage[] = [];

    if (!session.buyer?.email) {
      messages.push({
        type: 'error',
        code: 'missing',
        path: '$.buyer.email',
        content: 'Buyer email is required',
        severity: 'recoverable',
      });
    }

    if (!session.fulfillment?.methods?.[0]?.selected_destination_id) {
      messages.push({
        type: 'error',
        code: 'missing',
        path: '$.fulfillment.methods[0].selected_destination_id',
        content: 'Shipping address is required',
        severity: 'recoverable',
      });
    }

    return messages;
  }

  // Create checkout session
  createCheckout(data: { line_items: { id?: string; item: { id: string; title?: string; price?: number }; quantity: number }[] }): CheckoutSession {
    const id = `chk_${uuidv4().replace(/-/g, '').slice(0, 12)}`;
    
    // Resolve items from catalog or use provided
    const lineItems: LineItem[] = data.line_items.map((li, idx) => {
      const catalogItem = this.itemsMap.get(li.item.id);
      const item: Item = catalogItem || {
        id: li.item.id,
        title: li.item.title || 'Unknown Item',
        price: li.item.price || 0,
      };
      
      const subtotal = item.price * li.quantity;
      return {
        id: li.id || `li_${idx + 1}`,
        item,
        quantity: li.quantity,
        totals: [
          { type: 'subtotal', amount: subtotal },
          { type: 'total', amount: subtotal },
        ],
      };
    });

    const totals = this.calculateLineItemTotals(lineItems);
    const messages = this.validateCheckout({ line_items: lineItems });

    const paymentHandlers: Record<string, { id: string; version: string; config?: Record<string, unknown> }[]> = {};
    for (const handler of this.config.payment_handlers) {
      paymentHandlers[handler.namespace] = [{ id: handler.id, version: UCP_VERSION, config: handler.config }];
    }

    // Build links
    const links: { type: string; url: string }[] = [];
    if (this.config.terms_url) {
      links.push({ type: 'terms_of_service', url: this.config.terms_url });
    }
    if (this.config.privacy_url) {
      links.push({ type: 'privacy_policy', url: this.config.privacy_url });
    }

    // Set expiry (1 hour from now)
    const expiresAt = new Date(Date.now() + 60 * 60 * 1000).toISOString();

    const session: CheckoutSession = {
      ucp: {
        version: UCP_VERSION,
        capabilities: {
          'dev.ucp.shopping.checkout': [{ version: UCP_VERSION }],
        },
        payment_handlers: paymentHandlers,
      },
      id,
      status: messages.length > 0 ? 'incomplete' : 'ready_for_complete',
      messages: messages.length > 0 ? messages : undefined,
      currency: this.config.currency,
      line_items: lineItems,
      totals,
      links,
      continue_url: `${this.config.domain}/checkout/${id}`,
      expires_at: expiresAt,
    };

    checkoutSessions.set(id, session);
    return session;
  }

  // Get checkout session
  getCheckout(id: string): CheckoutSession | undefined {
    return checkoutSessions.get(id);
  }

  // Update checkout session
  updateCheckout(id: string, data: {
    buyer?: Buyer;
    line_items?: { id?: string; item: { id: string; title?: string; price?: number }; quantity: number }[];
    fulfillment?: Fulfillment;
  }): CheckoutSession | undefined {
    const session = checkoutSessions.get(id);
    if (!session) return undefined;

    // Update buyer
    if (data.buyer) {
      session.buyer = { ...session.buyer, ...data.buyer };
    }

    // Update line items
    if (data.line_items) {
      session.line_items = data.line_items.map((li, idx) => {
        const catalogItem = this.itemsMap.get(li.item.id);
        const item: Item = catalogItem || {
          id: li.item.id,
          title: li.item.title || 'Unknown Item',
          price: li.item.price || 0,
        };
        const subtotal = item.price * li.quantity;
        return {
          id: li.id || `li_${idx + 1}`,
          item,
          quantity: li.quantity,
          totals: [
            { type: 'subtotal', amount: subtotal },
            { type: 'total', amount: subtotal },
          ],
        };
      });
    }

    // Update fulfillment with shipping options
    if (data.fulfillment) {
      const methods = data.fulfillment.methods.map((method, mIdx) => {
        const destinations = method.destinations.map((dest, dIdx) => ({
          ...dest,
          id: dest.id || `dest_${dIdx + 1}`,
        }));

        const groups = method.groups?.length
          ? method.groups
          : [
              {
                id: `group_${mIdx + 1}`,
                line_item_ids: session.line_items.map((li) => li.id),
                selected_option_id: this.config.shipping_options[0]?.id,
                options: this.config.shipping_options.map((opt) => ({
                  id: opt.id,
                  title: opt.title,
                  description: opt.description || opt.estimated_days,
                  totals: [{ type: 'total', amount: opt.price }],
                })),
              },
            ];

        return {
          id: method.id || `method_${mIdx + 1}`,
          type: method.type,
          line_item_ids: method.line_item_ids || session.line_items.map((li) => li.id),
          selected_destination_id: method.selected_destination_id || destinations[0]?.id,
          destinations,
          groups,
        };
      });

      session.fulfillment = { methods };
    }

    // Recalculate totals including shipping
    let shippingTotal = 0;
    if (session.fulfillment) {
      for (const method of session.fulfillment.methods) {
        for (const group of method.groups) {
          const selectedOption = group.options.find((o) => o.id === group.selected_option_id);
          if (selectedOption) {
            const total = selectedOption.totals.find((t) => t.type === 'total');
            shippingTotal += total?.amount || 0;
          }
        }
      }
    }

    const subtotal = session.line_items.reduce((sum, li) => sum + li.item.price * li.quantity, 0);
    const tax = Math.round(subtotal * this.config.tax_rate);
    session.totals = [
      { type: 'subtotal', amount: subtotal },
      { type: 'shipping', amount: shippingTotal },
      { type: 'tax', amount: tax },
      { type: 'total', amount: subtotal + shippingTotal + tax },
    ];

    // Revalidate
    const newMessages = this.validateCheckout(session);
    session.messages = newMessages.length > 0 ? newMessages : undefined;
    session.status = newMessages.length > 0 ? 'incomplete' : 'ready_for_complete';

    checkoutSessions.set(id, session);
    return session;
  }

  // Complete checkout (create order)
  async completeCheckout(id: string): Promise<CheckoutSession | { error: string }> {
    const session = checkoutSessions.get(id);
    if (!session) return { error: 'Checkout session not found' };
    if (session.status !== 'ready_for_complete') {
      return { error: 'Checkout is not ready for completion', messages: session.messages };
    }

    const orderId = `order_${uuidv4().replace(/-/g, '').slice(0, 12)}`;
    
    const order: Order = {
      ucp: {
        version: UCP_VERSION,
        capabilities: {
          'dev.ucp.shopping.order': [{ version: UCP_VERSION }],
        },
      },
      id: orderId,
      checkout_id: id,
      permalink_url: `${this.config.domain}/orders/${orderId}`,
      line_items: session.line_items,
      buyer: session.buyer,
      totals: session.totals,
      fulfillment: session.fulfillment
        ? {
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
          }
        : undefined,
      created_at: new Date().toISOString(),
    };

    orders.set(orderId, order);
    
    // Update session to completed state with order confirmation
    session.status = 'completed';
    session.order = {
      id: orderId,
      permalink_url: order.permalink_url,
    };
    delete session.continue_url;
    delete session.messages;
    checkoutSessions.set(id, session);

    return session;
  }

  // Cancel checkout
  cancelCheckout(id: string): CheckoutSession | undefined {
    const session = checkoutSessions.get(id);
    if (!session) return undefined;
    session.status = 'canceled';
    delete session.continue_url;
    checkoutSessions.set(id, session);
    return session;
  }

  // Get order
  getOrder(id: string): Order | undefined {
    return orders.get(id);
  }

  // List orders
  listOrders(): Order[] {
    return Array.from(orders.values());
  }

  getConfig(): MerchantConfig {
    return this.config;
  }
}
