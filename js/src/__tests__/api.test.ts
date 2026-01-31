import { describe, it, expect, beforeAll } from 'vitest';
import request from 'supertest';
import { createExpressApp } from '../express-app';
import { MerchantConfig } from '../schema';

const testConfig: MerchantConfig = {
  name: 'Test Store',
  domain: 'https://test.example.com',
  currency: 'USD',
  port: 3000,
  items: [
    { id: 'item-1', title: 'Test Item', price: 1000 },
    { id: 'item-2', title: 'Another Item', price: 2500 },
  ],
  shipping_options: [
    { id: 'standard', title: 'Standard Shipping', price: 500, estimated_days: '5-7 days' },
  ],
  payment_handlers: [
    { namespace: 'com.stripe', id: 'stripe-test' },
  ],
  tax_rate: 0.08,
};

describe('UCP Server API', () => {
  const app = createExpressApp(testConfig, { useDb: false });

  describe('GET /.well-known/ucp', () => {
    it('returns UCP profile', async () => {
      const res = await request(app).get('/.well-known/ucp');
      expect(res.status).toBe(200);
      expect(res.body.ucp.version).toBe('2026-01-11');
      expect(res.body.ucp.services['dev.ucp.shopping']).toBeDefined();
    });
  });

  describe('GET /health', () => {
    it('returns health status', async () => {
      const res = await request(app).get('/health');
      expect(res.status).toBe(200);
      expect(res.body.status).toBeDefined();
    });
  });

  describe('POST /ucp/v1/checkout-sessions', () => {
    it('creates a checkout session', async () => {
      const res = await request(app)
        .post('/ucp/v1/checkout-sessions')
        .send({
          line_items: [{ item: { id: 'item-1' }, quantity: 2 }],
        });
      expect(res.status).toBe(201);
      expect(res.body.id).toBeDefined();
      expect(res.body.status).toBe('incomplete');
      expect(res.body.line_items).toHaveLength(1);
      expect(res.body.line_items[0].quantity).toBe(2);
    });

    it('rejects invalid requests', async () => {
      const res = await request(app)
        .post('/ucp/v1/checkout-sessions')
        .send({ line_items: [] }); // Empty array
      expect(res.status).toBe(400);
      expect(res.body.error).toBe('Validation failed');
    });

    it('rejects excessive quantity', async () => {
      const res = await request(app)
        .post('/ucp/v1/checkout-sessions')
        .send({
          line_items: [{ item: { id: 'item-1' }, quantity: 999 }],
        });
      expect(res.status).toBe(400);
    });
  });

  describe('Checkout flow', () => {
    let checkoutId: string;

    it('creates checkout', async () => {
      const res = await request(app)
        .post('/ucp/v1/checkout-sessions')
        .send({
          line_items: [{ item: { id: 'item-1' }, quantity: 1 }],
        });
      checkoutId = res.body.id;
      expect(res.body.status).toBe('incomplete');
    });

    it('updates with buyer and fulfillment', async () => {
      const res = await request(app)
        .put(`/ucp/v1/checkout-sessions/${checkoutId}`)
        .send({
          buyer: { email: 'test@example.com' },
          fulfillment: {
            methods: [{
              type: 'shipping',
              destinations: [{
                street_address: '123 Main St',
                address_locality: 'Anytown',
                address_region: 'CA',
                postal_code: '12345',
                address_country: 'US',
              }],
            }],
          },
        });
      expect(res.status).toBe(200);
      expect(res.body.status).toBe('ready_for_complete');
    });

    it('completes checkout', async () => {
      const res = await request(app)
        .post(`/ucp/v1/checkout-sessions/${checkoutId}/complete`);
      expect(res.status).toBe(201);
      expect(res.body.status).toBe('completed');
      expect(res.body.order).toBeDefined();
      expect(res.body.order.id).toMatch(/^order_/);
    });
  });

  describe('GET /ucp/v1/items', () => {
    it('returns product catalog', async () => {
      const res = await request(app).get('/ucp/v1/items');
      expect(res.status).toBe(200);
      expect(res.body).toHaveLength(2);
      expect(res.body[0].id).toBe('item-1');
    });
  });
});
