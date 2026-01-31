#!/usr/bin/env node

import { Command } from 'commander';
import * as fs from 'fs';
import * as path from 'path';
import { MerchantConfigSchema, MerchantConfig } from './schema';
import { createExpressApp } from './express-app';

const program = new Command();

program
  .name('ucpify')
  .description('Generate and run a UCP-compliant server for merchants')
  .version('1.0.0');

program
  .command('init')
  .description('Create a sample merchant configuration file')
  .option('-o, --output <file>', 'Output file path', 'merchant-config.json')
  .action((options) => {
    const sampleConfig: MerchantConfig = {
      name: 'My Store',
      domain: 'http://localhost:3000',
      currency: 'USD',
      terms_url: 'https://example.com/terms',
      privacy_url: 'https://example.com/privacy',
      tax_rate: 0.08,
      port: 3000,
      items: [
        {
          id: 'item_001',
          title: 'Classic T-Shirt',
          description: 'A comfortable cotton t-shirt',
          price: 2500, // $25.00 in cents
          sku: 'TSH-001',
        },
        {
          id: 'item_002',
          title: 'Premium Hoodie',
          description: 'Warm and stylish hoodie',
          price: 5999, // $59.99 in cents
          sku: 'HOO-001',
        },
      ],
      shipping_options: [
        {
          id: 'standard',
          title: 'Standard Shipping',
          description: 'Arrives in 5-7 business days',
          price: 500, // $5.00
          estimated_days: '5-7 business days',
        },
        {
          id: 'express',
          title: 'Express Shipping',
          description: 'Arrives in 2-3 business days',
          price: 1500, // $15.00
          estimated_days: '2-3 business days',
        },
        {
          id: 'overnight',
          title: 'Overnight Shipping',
          description: 'Arrives next business day',
          price: 2999, // $29.99
          estimated_days: '1 business day',
        },
      ],
      payment_handlers: [
        {
          namespace: 'com.stripe',
          id: 'stripe_handler',
          config: {
            publishable_key: 'pk_test_...',
          },
        },
        {
          namespace: 'com.paypal',
          id: 'paypal_handler',
          config: {
            client_id: 'your_paypal_client_id',
          },
        },
      ],
    };

    fs.writeFileSync(options.output, JSON.stringify(sampleConfig, null, 2));
    console.log(`✅ Created sample config at: ${options.output}`);
    console.log('\n📝 Edit this file to configure your products, shipping, and payment handlers.');
    console.log(`\n🚀 Run: npx ts-node src/cli.ts serve ${options.output}`);
  });

program
  .command('serve')
  .description('Start the UCP server from a configuration file')
  .argument('<config>', 'Path to merchant configuration JSON file')
  .option('-p, --port <number>', 'Port to run on (overrides config)')
  .option('--no-db', 'Use in-memory storage instead of SQLite')
  .action((configPath, options) => {
    try {
      const configFile = path.resolve(configPath);
      if (!fs.existsSync(configFile)) {
        console.error(`❌ Config file not found: ${configFile}`);
        process.exit(1);
      }

      const rawConfig = JSON.parse(fs.readFileSync(configFile, 'utf-8'));
      const config = MerchantConfigSchema.parse(rawConfig);

      if (options.port) {
        config.port = parseInt(options.port, 10);
      }

      const useDb = options.db !== false;
      const app = createExpressApp(config, { useDb });
      const port = config.port;
      const storageType = useDb ? 'SQLite' : 'In-memory';

      app.listen(port, () => {
        console.log(`
╔══════════════════════════════════════════════════════════════╗
║                    🛒 UCP Server Running                     ║
╠══════════════════════════════════════════════════════════════╣
║  Merchant: ${config.name.padEnd(49)}║
║  Domain:   ${config.domain.padEnd(49)}║
║  Port:     ${String(port).padEnd(49)}║
║  Storage:  ${storageType.padEnd(49)}║
╠══════════════════════════════════════════════════════════════╣
║  Endpoints:                                                  ║
║  • GET  /.well-known/ucp              - UCP Profile          ║
║  • POST /ucp/v1/checkout-sessions     - Create Checkout      ║
║  • GET  /ucp/v1/checkout-sessions/:id - Get Checkout         ║
║  • PUT  /ucp/v1/checkout-sessions/:id - Update Checkout      ║
║  • POST /ucp/v1/checkout-sessions/:id/complete - Complete    ║
║  • POST /ucp/v1/checkout-sessions/:id/cancel   - Cancel      ║
║  • GET  /ucp/v1/orders                - List Orders          ║
║  • GET  /ucp/v1/orders/:id            - Get Order            ║
║  • GET  /ucp/v1/items                 - Product Catalog      ║
║  • GET  /health                       - Health Check         ║
╚══════════════════════════════════════════════════════════════╝

🔗 UCP Profile: http://localhost:${port}/.well-known/ucp
📦 Products: ${config.items.length} items loaded
🚚 Shipping: ${config.shipping_options.length} options available
💳 Payment Handlers: ${config.payment_handlers.length} configured
💾 Storage: ${storageType}${useDb ? ' (./data/ucp.db)' : ''}
        `);
      });
    } catch (error) {
      console.error('❌ Error:', error);
      process.exit(1);
    }
  });

program
  .command('validate')
  .description('Validate a merchant configuration file')
  .argument('<config>', 'Path to merchant configuration JSON file')
  .action((configPath) => {
    try {
      const configFile = path.resolve(configPath);
      if (!fs.existsSync(configFile)) {
        console.error(`❌ Config file not found: ${configFile}`);
        process.exit(1);
      }

      const rawConfig = JSON.parse(fs.readFileSync(configFile, 'utf-8'));
      const config = MerchantConfigSchema.parse(rawConfig);

      console.log('✅ Configuration is valid!');
      console.log(`\n📊 Summary:`);
      console.log(`   • Name: ${config.name}`);
      console.log(`   • Domain: ${config.domain}`);
      console.log(`   • Currency: ${config.currency}`);
      console.log(`   • Tax Rate: ${(config.tax_rate * 100).toFixed(2)}%`);
      console.log(`   • Products: ${config.items.length}`);
      console.log(`   • Shipping Options: ${config.shipping_options.length}`);
      console.log(`   • Payment Handlers: ${config.payment_handlers.length}`);
    } catch (error) {
      console.error('❌ Validation failed:', error);
      process.exit(1);
    }
  });

program.parse();
