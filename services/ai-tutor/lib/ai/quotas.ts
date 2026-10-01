import { getQuotaBucket, incrementQuotaBucket } from '@/lib/ai/db';
import {
  getCachedQuotaBucket,
  incrementCachedQuotaBucket,
  reserveQuotaRequestCache,
} from '@/lib/ai/runtime-cache';
import type { UsageSummary } from '@/lib/ai/types';

const AI_RPM_LIMIT = Number(process.env.AI_RPM_LIMIT ?? 30);
const AI_DAILY_REQUEST_LIMIT = Number(process.env.AI_DAILY_REQUEST_LIMIT ?? 500);
const AI_DAILY_TOKEN_LIMIT = Number(process.env.AI_DAILY_TOKEN_LIMIT ?? 750000);
const AI_MONTHLY_COST_USD_LIMIT = Number(process.env.AI_MONTHLY_COST_USD_LIMIT ?? 100);

const OPENAI_CHAT_PROMPT_COST_PER_1K = Number(process.env.OPENAI_CHAT_PROMPT_COST_PER_1K ?? 0.00015);
const OPENAI_CHAT_COMPLETION_COST_PER_1K = Number(
  process.env.OPENAI_CHAT_COMPLETION_COST_PER_1K ?? 0.0006,
);
const OPENAI_EMBED_COST_PER_1K = Number(process.env.OPENAI_EMBED_COST_PER_1K ?? 0.00002);

function minuteBucketStart(now = new Date()): string {
  return now.toISOString().slice(0, 16);
}

function dayBucketStart(now = new Date()): string {
  return now.toISOString().slice(0, 10);
}

function monthBucketStart(now = new Date()): string {
  return now.toISOString().slice(0, 7);
}

async function readQuotaBucket(params: {
  apiKeyHash: string;
  bucketType: 'minute' | 'day' | 'month';
  bucketStart: string;
}) {
  const cached = await getCachedQuotaBucket(params);
  if (cached) {
    return cached;
  }
  return getQuotaBucket(params);
}

async function writeQuotaBucket(params: {
  apiKeyHash: string;
  bucketType: 'minute' | 'day' | 'month';
  bucketStart: string;
  requests: number;
  tokens: number;
  costUsd: number;
}) {
  await incrementCachedQuotaBucket(params);
  await incrementQuotaBucket(params);
}

export function estimateUsageCost(usage: Omit<UsageSummary, 'estimated_cost_usd'>): UsageSummary {
  const estimated_cost_usd =
    (usage.prompt_tokens / 1000) * OPENAI_CHAT_PROMPT_COST_PER_1K +
    (usage.completion_tokens / 1000) * OPENAI_CHAT_COMPLETION_COST_PER_1K +
    (usage.embedding_tokens / 1000) * OPENAI_EMBED_COST_PER_1K;

  return {
    ...usage,
    estimated_cost_usd: Number(estimated_cost_usd.toFixed(6)),
  };
}

export async function checkQuotaBudget(apiKeyHash: string, estimatedPromptTokens: number) {
  const now = new Date();
  const minute = await readQuotaBucket({
    apiKeyHash,
    bucketType: 'minute',
    bucketStart: minuteBucketStart(now),
  });
  if (minute.requests >= AI_RPM_LIMIT) {
    return {
      allowed: false,
      status: 429,
      code: 'rpm_limit_exceeded',
      message: 'Превышен лимит запросов в минуту.',
    } as const;
  }

  const day = await readQuotaBucket({
    apiKeyHash,
    bucketType: 'day',
    bucketStart: dayBucketStart(now),
  });
  if (day.requests >= AI_DAILY_REQUEST_LIMIT) {
    return {
      allowed: false,
      status: 429,
      code: 'daily_request_limit_exceeded',
      message: 'Превышен дневной лимит запросов.',
    } as const;
  }

  if (day.tokens + estimatedPromptTokens >= AI_DAILY_TOKEN_LIMIT) {
    return {
      allowed: false,
      status: 429,
      code: 'daily_token_limit_exceeded',
      message: 'Превышен дневной лимит токенов.',
    } as const;
  }

  const month = await readQuotaBucket({
    apiKeyHash,
    bucketType: 'month',
    bucketStart: monthBucketStart(now),
  });
  if (month.cost_usd >= AI_MONTHLY_COST_USD_LIMIT) {
    return {
      allowed: false,
      status: 429,
      code: 'monthly_cost_limit_exceeded',
      message: 'Превышен месячный лимит стоимости запросов.',
    } as const;
  }

  return {
    allowed: true,
  } as const;
}

export async function consumeQuota(apiKeyHash: string, usage: UsageSummary) {
  const now = new Date();
  await writeQuotaBucket({
    apiKeyHash,
    bucketType: 'minute',
    bucketStart: minuteBucketStart(now),
    requests: 0,
    tokens: usage.total_tokens,
    costUsd: usage.estimated_cost_usd,
  });
  await writeQuotaBucket({
    apiKeyHash,
    bucketType: 'day',
    bucketStart: dayBucketStart(now),
    requests: 0,
    tokens: usage.total_tokens,
    costUsd: usage.estimated_cost_usd,
  });
  await writeQuotaBucket({
    apiKeyHash,
    bucketType: 'month',
    bucketStart: monthBucketStart(now),
    requests: 0,
    tokens: usage.total_tokens,
    costUsd: usage.estimated_cost_usd,
  });
}

export async function reserveQuotaRequest(apiKeyHash: string, requestEventId?: string, estimatedTokens = 0) {
  const now = new Date();
  await writeQuotaBucket({
    apiKeyHash,
    bucketType: 'minute',
    bucketStart: minuteBucketStart(now),
    requests: 1,
    tokens: 0,
    costUsd: 0,
  });
  await writeQuotaBucket({
    apiKeyHash,
    bucketType: 'day',
    bucketStart: dayBucketStart(now),
    requests: 1,
    tokens: 0,
    costUsd: 0,
  });
  await writeQuotaBucket({
    apiKeyHash,
    bucketType: 'month',
    bucketStart: monthBucketStart(now),
    requests: 1,
    tokens: 0,
    costUsd: 0,
  });
  if (requestEventId) {
    await reserveQuotaRequestCache(requestEventId, apiKeyHash, estimatedTokens);
  }
}

export function getQuotaConfig() {
  return {
    rpm_limit: AI_RPM_LIMIT,
    daily_request_limit: AI_DAILY_REQUEST_LIMIT,
    daily_token_limit: AI_DAILY_TOKEN_LIMIT,
    monthly_cost_usd_limit: AI_MONTHLY_COST_USD_LIMIT,
  };
}
