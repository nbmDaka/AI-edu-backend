import { createHash } from 'node:crypto';

export function readApiKeyFromHeaders(headers: Headers): string {
  const fromHeader = (headers.get('x-api-key') ?? '').trim();
  if (fromHeader) {
    return fromHeader;
  }

  const authHeader = (headers.get('authorization') ?? '').trim();
  if (authHeader.toLowerCase().startsWith('bearer ')) {
    return authHeader.slice(7).trim();
  }

  return '';
}

export function hashApiKey(value: string): string {
  const normalized = value.trim() || 'anonymous';
  return createHash('sha256').update(normalized).digest('hex');
}

export function jsonError(
  error: string,
  status: number,
  extra?: Record<string, unknown>,
): Response {
  return Response.json({ error, ...extra }, { status });
}

export function estimateTokens(text: string): number {
  if (!text.trim()) {
    return 0;
  }
  return Math.ceil(text.length / 4);
}
