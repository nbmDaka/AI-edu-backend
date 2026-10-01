import type { NextRequest } from 'next/server';
import { NextResponse } from 'next/server';

const API_SHARED_KEY = (process.env.API_SHARED_KEY ?? '').trim();
const API_CORS_ORIGINS_RAW = (process.env.API_CORS_ORIGINS ?? '*').trim();
const API_CORS_ALLOW_CREDENTIALS =
  (process.env.API_CORS_ALLOW_CREDENTIALS ?? 'false').trim().toLowerCase() === 'true';

const PUBLIC_API_PATHS = new Set(['/api/health', '/api/openapi']);

function parseAllowedOrigins(raw: string): string[] {
  const origins = raw
    .split(',')
    .map((item) => item.trim())
    .filter((item) => item.length > 0);

  return origins.length ? origins : ['*'];
}

function buildCorsHeaders(req: NextRequest): Headers {
  const requestOrigin = req.headers.get('origin');
  const allowedOrigins = parseAllowedOrigins(API_CORS_ORIGINS_RAW);
  const allowAllOrigins = allowedOrigins.includes('*');
  const resolvedOrigin = allowAllOrigins
    ? '*'
    : requestOrigin && allowedOrigins.includes(requestOrigin)
      ? requestOrigin
      : '';

  const headers = new Headers();
  if (resolvedOrigin) {
    headers.set('Access-Control-Allow-Origin', resolvedOrigin);
  }
  headers.set('Access-Control-Allow-Methods', 'GET,POST,PUT,PATCH,DELETE,OPTIONS');
  headers.set(
    'Access-Control-Allow-Headers',
    'Content-Type, Authorization, X-API-Key, X-Requested-With',
  );
  headers.set('Access-Control-Max-Age', '86400');

  if (!allowAllOrigins && requestOrigin) {
    headers.set('Vary', 'Origin');
  }

  if (API_CORS_ALLOW_CREDENTIALS && !allowAllOrigins) {
    headers.set('Access-Control-Allow-Credentials', 'true');
  }

  return headers;
}

function readApiKey(req: NextRequest): string {
  const fromHeader = (req.headers.get('x-api-key') ?? '').trim();
  if (fromHeader) {
    return fromHeader;
  }

  const authHeader = (req.headers.get('authorization') ?? '').trim();
  if (authHeader.toLowerCase().startsWith('bearer ')) {
    return authHeader.slice(7).trim();
  }

  return '';
}

export function middleware(req: NextRequest) {
  const corsHeaders = buildCorsHeaders(req);

  if (req.method === 'OPTIONS') {
    return new NextResponse(null, {
      status: 204,
      headers: corsHeaders,
    });
  }

  if (API_SHARED_KEY && !PUBLIC_API_PATHS.has(req.nextUrl.pathname)) {
    const providedKey = readApiKey(req);
    if (providedKey !== API_SHARED_KEY) {
      return NextResponse.json(
        { error: 'Unauthorized API access. Provide X-API-Key or Bearer token.' },
        { status: 401, headers: corsHeaders },
      );
    }
  }

  const response = NextResponse.next();
  corsHeaders.forEach((value, key) => response.headers.set(key, value));
  return response;
}

export const config = {
  matcher: ['/api/:path*'],
};
