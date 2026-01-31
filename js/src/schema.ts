import { z } from 'zod';

// UCP Version
export const UCP_VERSION = '2026-01-11';

// Product/Item Schema
export const ItemSchema = z.object({
  id: z.string(),
  title: z.string(),
  description: z.string().optional(),
  price: z.number().int().min(0), // Minor units (cents)
  image_url: z.string().url().optional(),
  sku: z.string().optional(),
  stock: z.number().int().min(0).optional(), // Inventory tracking
});

// Shipping Option Schema
export const ShippingOptionSchema = z.object({
  id: z.string(),
  title: z.string(),
  description: z.string().optional(),
  price: z.number().int().min(0), // Minor units
  estimated_days: z.string().optional(),
});

// Payment Handler Schema
export const PaymentHandlerSchema = z.object({
  namespace: z.string(), // e.g., "com.stripe", "com.google.pay"
  id: z.string(),
  config: z.record(z.string(), z.unknown()).optional(),
});

// Merchant Configuration Schema
export const MerchantConfigSchema = z.object({
  name: z.string(),
  domain: z.string().url(),
  currency: z.string().default('USD'),
  terms_url: z.string().url().optional(),
  privacy_url: z.string().url().optional(),
  items: z.array(ItemSchema),
  shipping_options: z.array(ShippingOptionSchema).default([]),
  payment_handlers: z.array(PaymentHandlerSchema).default([]),
  tax_rate: z.number().min(0).max(1).default(0), // e.g., 0.08 for 8%
  port: z.number().default(3000),
});

export type Item = z.infer<typeof ItemSchema>;
export type ShippingOption = z.infer<typeof ShippingOptionSchema>;
export type PaymentHandler = z.infer<typeof PaymentHandlerSchema>;
export type MerchantConfig = z.infer<typeof MerchantConfigSchema>;

// Checkout Session Types
export type CheckoutStatus = 'incomplete' | 'requires_escalation' | 'ready_for_complete' | 'complete_in_progress' | 'completed' | 'canceled';

export interface Buyer {
  email?: string;
  first_name?: string;
  last_name?: string;
  phone?: string;
}

export interface Address {
  id?: string;
  first_name?: string;
  last_name?: string;
  street_address: string;
  address_locality: string;
  address_region: string;
  postal_code: string;
  address_country: string;
}

export interface LineItem {
  id: string;
  item: Item;
  quantity: number;
  totals?: { type: string; amount: number }[];
}

export interface FulfillmentOption {
  id: string;
  title: string;
  description?: string;
  totals: { type: string; amount: number }[];
}

export interface FulfillmentGroup {
  id: string;
  line_item_ids: string[];
  selected_option_id?: string;
  options: FulfillmentOption[];
}

export interface FulfillmentMethod {
  id: string;
  type: 'shipping' | 'pickup' | 'digital';
  line_item_ids: string[];
  selected_destination_id?: string;
  destinations: Address[];
  groups: FulfillmentGroup[];
}

export interface Fulfillment {
  methods: FulfillmentMethod[];
}

export interface PaymentInstrument {
  id: string;
  handler_id: string;
  type: string;
  selected?: boolean;
  display?: Record<string, unknown>;
}

export interface Payment {
  instruments: PaymentInstrument[];
}

export interface CheckoutMessage {
  type: 'error' | 'warning' | 'info';
  code: string;
  path?: string;
  content: string;
  severity: 'recoverable' | 'requires_buyer_input' | 'requires_buyer_review';
}

export interface OrderConfirmation {
  id: string;
  permalink_url?: string;
}

export interface CheckoutSession {
  ucp: {
    version: string;
    capabilities: Record<string, { version: string }[]>;
    payment_handlers?: Record<string, { id: string; version: string; config?: Record<string, unknown> }[]>;
  };
  id: string;
  status: CheckoutStatus;
  messages?: CheckoutMessage[];
  currency: string;
  buyer?: Buyer;
  line_items: LineItem[];
  totals: { type: string; amount: number }[];
  links: { type: string; url: string; title?: string }[];
  fulfillment?: Fulfillment;
  payment?: Payment;
  continue_url?: string;
  expires_at?: string;
  order?: OrderConfirmation;
}

export interface Order {
  ucp: {
    version: string;
    capabilities: Record<string, { version: string }[]>;
  };
  id: string;
  checkout_id: string;
  permalink_url?: string;
  line_items: LineItem[];
  buyer?: Buyer;
  totals: { type: string; amount: number }[];
  fulfillment?: {
    expectations: {
      id: string;
      line_items: { id: string; quantity: number }[];
      method_type: string;
      destination?: Address;
      description?: string;
      fulfillable_on?: string;
    }[];
    events: {
      id: string;
      occurred_at: string;
      type: string;
      line_items: { id: string; quantity: number }[];
      tracking_number?: string;
      tracking_url?: string;
      description?: string;
    }[];
  };
  created_at: string;
}

// Request validation schemas
export const CreateCheckoutRequestSchema = z.object({
  line_items: z.array(z.object({
    id: z.string().optional(),
    item: z.object({
      id: z.string(),
      title: z.string().optional(),
      price: z.number().int().min(0).optional(),
    }),
    quantity: z.number().int().min(1).max(100),
  })).min(1).max(50),
  buyer: z.object({
    email: z.string().email().optional(),
    first_name: z.string().max(100).optional(),
    last_name: z.string().max(100).optional(),
    phone: z.string().max(30).optional(),
  }).optional(),
  fulfillment: z.object({
    methods: z.array(z.object({
      id: z.string().optional(),
      type: z.enum(['shipping', 'pickup', 'digital']),
      line_item_ids: z.array(z.string()).optional(),
      selected_destination_id: z.string().optional(),
      destinations: z.array(z.object({
        id: z.string().optional(),
        first_name: z.string().max(100).optional(),
        last_name: z.string().max(100).optional(),
        street_address: z.string().max(200),
        address_locality: z.string().max(100),
        address_region: z.string().max(100),
        postal_code: z.string().max(20),
        address_country: z.string().length(2),
      })),
      groups: z.array(z.any()).optional(),
    })).optional(),
  }).optional(),
});

export const UpdateCheckoutRequestSchema = z.object({
  buyer: z.object({
    email: z.string().email().optional(),
    first_name: z.string().max(100).optional(),
    last_name: z.string().max(100).optional(),
    phone: z.string().max(30).optional(),
  }).optional(),
  line_items: z.array(z.object({
    id: z.string().optional(),
    item: z.object({
      id: z.string(),
      title: z.string().optional(),
      price: z.number().int().min(0).optional(),
    }),
    quantity: z.number().int().min(1).max(100),
  })).optional(),
  fulfillment: z.any().optional(),
});

export type CreateCheckoutRequest = z.infer<typeof CreateCheckoutRequestSchema>;
export type UpdateCheckoutRequest = z.infer<typeof UpdateCheckoutRequestSchema>;
