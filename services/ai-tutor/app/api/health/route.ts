export const dynamic = 'force-dynamic';
import { NextResponse } from 'next/server';
import { getDbHealth, initAiDatabase, maybeRunDbMaintenance } from '@/lib/ai/db';
import { getQuotaConfig } from '@/lib/ai/quotas';
import { getRuntimeCacheHealth } from '@/lib/ai/runtime-cache';

export const runtime = 'nodejs';

const CHECK_TIMEOUT_MS = Number(process.env.API_HEALTHCHECK_TIMEOUT_MS ?? 3000);

type ServiceCheck = {
  name: 'stt' | 'tts' | 'avatar';
  target: string;
  configured: boolean;
  required: boolean;
  status: 'ok' | 'error' | 'skipped';
  httpStatus?: number;
  details?: string;
};

function withTimeout<T>(task: Promise<T>, timeoutMs: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('timeout')), timeoutMs);
    task
      .then((result) => {
        clearTimeout(timer);
        resolve(result);
      })
      .catch((error) => {
        clearTimeout(timer);
        reject(error);
      });
  });
}

function deriveHealthUrl(rawUrl: string | undefined, fallback: string): string {
  try {
    if (!rawUrl) {
      return fallback;
    }
    const parsed = new URL(rawUrl);
    parsed.pathname = '/health';
    parsed.search = '';
    parsed.hash = '';
    return parsed.toString();
  } catch {
    return fallback;
  }
}

async function checkService(
  name: ServiceCheck['name'],
  target: string,
  configured: boolean,
  required: boolean,
): Promise<ServiceCheck> {
  if (!configured) {
    return {
      name,
      target,
      configured,
      required,
      status: required ? 'error' : 'skipped',
      details: required
        ? 'local service URL is not configured'
        : 'local service URL is not configured; optional dependency skipped',
    };
  }

  try {
    const response = await withTimeout(fetch(target, { method: 'GET' }), CHECK_TIMEOUT_MS);
    if (!response.ok) {
      return {
        name,
        target,
        configured,
        required,
        status: 'error',
        httpStatus: response.status,
        details: `health endpoint returned ${response.status}`,
      };
    }

    return {
      name,
      target,
      configured,
      required,
      status: 'ok',
      httpStatus: response.status,
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : 'unknown error';
    return {
      name,
      target,
      configured,
      required,
      status: 'error',
      details: message,
    };
  }
}

function isVectorHealthy(db: Awaited<ReturnType<typeof getDbHealth>>) {
  if (!('vector' in db)) {
    return true;
  }

  return db.vector?.status === 'ok';
}

export async function GET() {
  await initAiDatabase();
  await maybeRunDbMaintenance();

  const localServicesRequired = process.env.API_HEALTH_REQUIRE_LOCAL_SERVICES === 'true';
  const sttConfigured = Boolean((process.env.LOCAL_STT_URL ?? '').trim());
  const ttsConfigured = Boolean((process.env.LOCAL_TTS_URL ?? '').trim());
  const avatarConfigured = Boolean((process.env.LOCAL_AVATAR_URL ?? '').trim());
  const sttHealthUrl = deriveHealthUrl(
    process.env.LOCAL_STT_URL,
    'http://127.0.0.1:8001/health',
  );
  const ttsHealthUrl = deriveHealthUrl(
    process.env.LOCAL_TTS_URL,
    'http://127.0.0.1:8002/health',
  );
  const avatarHealthUrl = deriveHealthUrl(
    process.env.LOCAL_AVATAR_URL,
    'http://127.0.0.1:8003/health',
  );

  const [stt, tts, avatar] = await Promise.all([
    checkService('stt', sttHealthUrl, sttConfigured, localServicesRequired),
    checkService('tts', ttsHealthUrl, ttsConfigured, localServicesRequired),
    checkService('avatar', avatarHealthUrl, avatarConfigured, localServicesRequired),
  ]);

  const openAiConfigured = Boolean((process.env.OPENAI_API_KEY ?? '').trim());
  const serviceResults = [stt, tts, avatar];
  const requiredServicesOk =
    !localServicesRequired || serviceResults.every((item) => item.status === 'ok');
  const db = await getDbHealth();
  const vectorOk = isVectorHealthy(db);
  const runtimeCache = await getRuntimeCacheHealth();
  const quota = {
    status: db.status === 'ok' ? ('ok' as const) : ('error' as const),
    config: getQuotaConfig(),
    cache: runtimeCache,
  };
  const overallStatus =
    requiredServicesOk && openAiConfigured && db.status === 'ok' && vectorOk ? 'ok' : 'degraded';

  return NextResponse.json(
    {
      status: overallStatus,
      timestamp: new Date().toISOString(),
      gateway: {
        status: 'ok',
        chatModel: process.env.OPENAI_MODEL ?? 'gpt-4o-mini',
      },
      openai: {
        configured: openAiConfigured,
      },
      database: db,
      quota,
      services: {
        required: localServicesRequired,
        stt,
        tts,
        avatar,
      },
    },
    { status: overallStatus === 'ok' ? 200 : 503 },
  );
}
