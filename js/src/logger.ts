import pino from 'pino';
import { config } from './config';

export const logger = pino({
  level: config.logLevel,
  transport: config.nodeEnv === 'development' 
    ? { target: 'pino-pretty', options: { colorize: true } }
    : undefined,
  base: { service: 'ucp-server' },
});

export type Logger = typeof logger;
