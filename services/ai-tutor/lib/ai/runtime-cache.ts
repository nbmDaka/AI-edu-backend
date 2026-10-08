import Redis from 'ioredis';

const REDIS_URL = (process.env.REDIS_URL ?? '').trim();
const REDIS_ENABLED = (process.env.AI_REDIS_ENABLED ?? '').trim().toLowerCase() === 'true' && Boolean(REDIS_URL);
const REDIS_PREFIX = (process.env.AI_REDIS_PREFIX ?? 'ai').trim() || 'ai';
const REDIS_CONNECT_TIMEOUT_MS = Number(process.env.AI_REDIS_CONNECT_TIMEOUT_MS ?? 3000);
const QUOTA_MINUTE_TTL_SEC = Number(process.env.AI_QUOTA_MINUTE_TTL_SEC ?? 120);
const QUOTA_DAY_TTL_SEC = Number(process.env.AI_QUOTA_DAY_TTL_SEC ?? 259200);
const QUOTA_MONTH_TTL_SEC = Number(process.env.AI_QUOTA_MONTH_TTL_SEC ?? 3888000);
const QUOTA_RESERVATION_TTL_SEC = Number(process.env.AI_QUOTA_RESERVATION_TTL_SEC ?? 600);
const JOB_STATUS_TTL_SEC = Number(process.env.AI_JOB_STATUS_TTL_SEC ?? 1209600);

type BucketType = 'minute' | 'day' | 'month';

let redisInstance: Redis | null = null;
let redisInitPromise: Promise<Redis | null> | null = null;

function buildKey(...parts: string[]) {
  return [REDIS_PREFIX, ...parts].join(':');
}

async function getRedis(): Promise<Redis | null> {
  if (!REDIS_ENABLED) {
    return null;
  }
  if (redisInstance) {
    return redisInstance;
  }
  if (!redisInitPromise) {
    redisInitPromise = (async () => {
      try {
        const client = new Redis(REDIS_URL, {
          lazyConnect: true,
          maxRetriesPerRequest: 1,
          enableReadyCheck: true,
          connectTimeout: REDIS_CONNECT_TIMEOUT_MS,
        });
        await client.connect();
        redisInstance = client;
        return client;
      } catch (error) {
        console.error('Redis init error:', error);
        redisInstance = null;
        return null;
      }
    })();
  }
  return redisInitPromise;
}

function ttlForBucket(bucketType: BucketType) {
  if (bucketType === 'minute') {
    return QUOTA_MINUTE_TTL_SEC;
  }
  if (bucketType === 'day') {
    return QUOTA_DAY_TTL_SEC;
  }
  return QUOTA_MONTH_TTL_SEC;
}

export async function getRuntimeCacheHealth() {
  const client = await getRedis();
  if (!REDIS_ENABLED) {
    return {
      status: 'disabled' as const,
      url: REDIS_URL ? 'configured-but-disabled' : 'not-configured',
    };
  }
  if (!client) {
    return {
      status: 'error' as const,
      url: REDIS_URL,
    };
  }
  try {
    await client.ping();
    return {
      status: 'ok' as const,
      url: REDIS_URL,
    };
  } catch (error) {
    return {
      status: 'error' as const,
      url: REDIS_URL,
      details: error instanceof Error ? error.message : 'unknown error',
    };
  }
}

export async function getCachedQuotaBucket(params: {
  apiKeyHash: string;
  bucketType: BucketType;
  bucketStart: string;
}) {
  const client = await getRedis();
  if (!client) {
    return null;
  }
  const key = buildKey('quota', params.apiKeyHash, params.bucketType, params.bucketStart);
  const data = await client.hgetall(key);
  if (!Object.keys(data).length) {
    return null;
  }
  return {
    requests: Number(data.requests ?? 0),
    tokens: Number(data.tokens ?? 0),
    cost_usd: Number(data.cost_usd ?? 0),
  };
}

export async function incrementCachedQuotaBucket(params: {
  apiKeyHash: string;
  bucketType: BucketType;
  bucketStart: string;
  requests: number;
  tokens: number;
  costUsd: number;
}) {
  const client = await getRedis();
  if (!client) {
    return false;
  }
  const key = buildKey('quota', params.apiKeyHash, params.bucketType, params.bucketStart);
  const multi = client.multi();
  if (params.requests) {
    multi.hincrby(key, 'requests', params.requests);
  }
  if (params.tokens) {
    multi.hincrby(key, 'tokens', params.tokens);
  }
  if (params.costUsd) {
    multi.hincrbyfloat(key, 'cost_usd', params.costUsd);
  }
  multi.expire(key, ttlForBucket(params.bucketType));
  await multi.exec();
  return true;
}

export async function reserveQuotaRequestCache(requestEventId: string, apiKeyHash: string, estimatedTokens: number) {
  const client = await getRedis();
  if (!client) {
    return false;
  }
  const key = buildKey('quota', 'reservation', requestEventId);
  await client.hset(key, {
    api_key_hash: apiKeyHash,
    reserved_at: new Date().toISOString(),
    estimated_tokens: String(estimatedTokens),
  });
  await client.expire(key, QUOTA_RESERVATION_TTL_SEC);
  return true;
}

export async function setJobState(jobId: string, payload: Record<string, string>) {
  const client = await getRedis();
  if (!client) {
    return false;
  }
  const key = buildKey('job', 'index', jobId);
  await client.hset(key, payload);
  await client.expire(key, JOB_STATUS_TTL_SEC);
  return true;
}

export async function getJobState(jobId: string) {
  const client = await getRedis();
  if (!client) {
    return null;
  }
  const key = buildKey('job', 'index', jobId);
  const payload = await client.hgetall(key);
  return Object.keys(payload).length ? payload : null;
}
