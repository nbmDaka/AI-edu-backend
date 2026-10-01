import { mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { Prisma, type PrismaClient } from '@prisma/client';
import { prisma } from '@/lib/ai/prisma';
import { redactSensitiveText } from '@/lib/ai/redaction';

const RETENTION_DAYS = Number(process.env.AI_TELEMETRY_RETENTION_DAYS ?? 30);
const CLEANUP_INTERVAL = Number(process.env.AI_DB_CLEANUP_EVERY_REQUESTS ?? 25);
const DB_PATH = resolve(process.cwd(), process.env.AI_DB_PATH ?? 'data/ai-module.db');
const VECTOR_DIMENSION = Number(process.env.AI_VECTOR_DIMENSION ?? 1536);
const VECTOR_CANDIDATE_MULTIPLIER = Math.max(2, Number(process.env.AI_VECTOR_CANDIDATE_MULTIPLIER ?? 4));

let requestCounter = 0;
let vectorReady = false;
let vectorError: string | null = null;

type Db = PrismaClient;

function isoNow(): string {
  return new Date().toISOString();
}

function redact(value: string | null | undefined): string {
  return redactSensitiveText(value ?? '');
}

function jsonValue(value: unknown): any {
  return JSON.parse(JSON.stringify(value ?? null));
}

function parseJsonArray<T>(value: any | null | undefined, fallback: T): T {
  if (value === null || value === undefined) {
    return fallback;
  }
  return value as T;
}

function toVectorLiteral(values: number[]): string {
  return `[${values.map((value) => Number(value).toString()).join(',')}]`;
}

async function ensurePgvectorSchema() {
  try {
    await prisma.$executeRawUnsafe('CREATE EXTENSION IF NOT EXISTS vector');
    await prisma.$executeRawUnsafe(
      `ALTER TABLE "course_chunks" ADD COLUMN IF NOT EXISTS "embedding_vector" vector(${VECTOR_DIMENSION})`,
    );
    await prisma.$executeRawUnsafe(
      'CREATE INDEX IF NOT EXISTS "idx_course_chunks_embedding_hnsw" ON "course_chunks" USING hnsw ("embedding_vector" vector_cosine_ops)',
    );
    vectorReady = true;
    vectorError = null;
  } catch (error) {
    vectorReady = false;
    vectorError = error instanceof Error ? error.message : 'unknown pgvector error';
  }
}

async function cleanupExpiredRows(db: Db) {
  const cutoff = new Date(Date.now() - RETENTION_DAYS * 24 * 60 * 60 * 1000);
  await db.requestEvent.deleteMany({ where: { startedAt: { lt: cutoff } } });
  await db.message.deleteMany({ where: { createdAt: { lt: cutoff } } });
  await db.session.deleteMany({ where: { lastActivityAt: { lt: cutoff } } });
  await db.quotaCounter.deleteMany({ where: { bucketStart: { lt: cutoff.toISOString() } } });
}

export async function initAiDatabase() {
  await prisma.$connect();
  mkdirSync(dirname(DB_PATH), { recursive: true });
  await ensurePgvectorSchema();
}

export async function maybeRunDbMaintenance() {
  requestCounter += 1;
  if (requestCounter % CLEANUP_INTERVAL === 0) {
    await cleanupExpiredRows(prisma);
  }
}

export async function getDbHealth() {
  try {
    const count = await prisma.session.count();
    return {
      status: 'ok' as const,
      path: process.env.DATABASE_URL ? 'postgresql' : DB_PATH,
      provider: 'postgresql' as const,
      sessions: count,
      vector: {
        status: vectorReady ? 'ok' : 'degraded',
        dimension: VECTOR_DIMENSION,
        details: vectorError,
      },
    };
  } catch (error) {
    return {
      status: 'error' as const,
      path: process.env.DATABASE_URL ? 'postgresql' : DB_PATH,
      provider: 'postgresql' as const,
      details: error instanceof Error ? error.message : 'unknown error',
      vector: {
        status: vectorReady ? 'ok' : 'degraded',
        dimension: VECTOR_DIMENSION,
        details: vectorError,
      },
    };
  }
}

export async function upsertSession(sessionId: string, apiKeyHash: string, title?: string | null, lastActivityAt = isoNow()) {
  const existing = await prisma.session.findUnique({ where: { sessionId }, select: { apiKeyHash: true } });
  if (existing?.apiKeyHash && existing.apiKeyHash !== apiKeyHash) {
    throw new Error('session ownership mismatch');
  }

  await prisma.session.upsert({
    where: { sessionId },
    update: {
      apiKeyHash,
      title: title ?? undefined,
      lastActivityAt: new Date(lastActivityAt),
    },
    create: {
      sessionId,
      apiKeyHash,
      title: title ?? null,
      createdAt: new Date(lastActivityAt),
      lastActivityAt: new Date(lastActivityAt),
    },
  });
}

export async function assertSessionOwnership(sessionId: string, apiKeyHash: string) {
  const existing = await prisma.session.findUnique({ where: { sessionId }, select: { apiKeyHash: true } });
  if (!existing?.apiKeyHash) {
    return { exists: false };
  }
  if (existing.apiKeyHash !== apiKeyHash) {
    throw new Error('session ownership mismatch');
  }
  return { exists: true };
}

export async function insertMessage(params: {
  id: string;
  sessionId: string;
  role: 'user' | 'assistant';
  content: string;
  language: string;
  createdAt?: string;
}) {
  await prisma.message.upsert({
    where: { id: params.id },
    update: {},
    create: {
      id: params.id,
      sessionId: params.sessionId,
      role: params.role,
      contentRaw: params.content,
      contentRedacted: redact(params.content),
      language: params.language,
      createdAt: new Date(params.createdAt ?? isoNow()),
    },
  });
}

export async function createRequestEvent(params: {
  id: string;
  sessionId: string;
  apiKeyHash: string;
  route: string;
  metadata?: unknown;
}) {
  const startedAt = isoNow();
  await prisma.requestEvent.create({
    data: {
      id: params.id,
      sessionId: params.sessionId,
      apiKeyHash: params.apiKeyHash,
      route: params.route,
      status: 102,
      startedAt: new Date(startedAt),
      metadataJson: jsonValue(params.metadata ?? {}),
    },
  });
  return { startedAt };
}

export async function finalizeRequestEvent(params: {
  id: string;
  status: number;
  guardrailBlocked: boolean;
  guardrailReasons: string[];
  injectionScore: number;
  blockedStage: string | null;
  retrievalQuery?: string | null;
  retrievedChunkIds?: number[];
  retrievalScores?: Array<{ chunk_id: number; label: string; score: number; rank: number }>;
  retrievalFallbackUsed?: boolean;
  prompt?: string | null;
  response?: string | null;
  usage?: {
    prompt_tokens: number;
    completion_tokens: number;
    embedding_tokens: number;
    total_tokens: number;
    estimated_cost_usd: number;
  } | null;
}) {
  const event = await prisma.requestEvent.findUnique({ where: { id: params.id }, select: { startedAt: true } });
  const finishedAt = new Date();
  const latencyMs = event?.startedAt ? Math.max(0, finishedAt.getTime() - event.startedAt.getTime()) : null;

  await prisma.requestEvent.update({
    where: { id: params.id },
    data: {
      status: params.status,
      finishedAt,
      latencyMs,
      guardrailBlocked: params.guardrailBlocked,
      guardrailReasons: jsonValue(params.guardrailReasons),
      injectionScore: params.injectionScore,
      blockedStage: params.blockedStage,
      retrievalQuery: params.retrievalQuery ?? null,
      retrievedChunkIds: jsonValue(params.retrievedChunkIds ?? []),
      retrievalScores: jsonValue(params.retrievalScores ?? []),
      retrievalFallbackUsed: Boolean(params.retrievalFallbackUsed),
      promptRaw: params.prompt ?? null,
      promptRedacted: params.prompt ? redact(params.prompt) : null,
      responseRaw: params.response ?? null,
      responseRedacted: params.response ? redact(params.response) : null,
      promptTokens: params.usage?.prompt_tokens ?? 0,
      completionTokens: params.usage?.completion_tokens ?? 0,
      embeddingTokens: params.usage?.embedding_tokens ?? 0,
      totalTokens: params.usage?.total_tokens ?? 0,
      estimatedCostUsd: params.usage?.estimated_cost_usd ?? 0,
    },
  });
}

export async function incrementQuotaBucket(params: {
  apiKeyHash: string;
  bucketType: 'minute' | 'day' | 'month';
  bucketStart: string;
  requests: number;
  tokens: number;
  costUsd: number;
}) {
  const existing = await prisma.quotaCounter.findUnique({
    where: {
      apiKeyHash_bucketType_bucketStart: {
        apiKeyHash: params.apiKeyHash,
        bucketType: params.bucketType,
        bucketStart: params.bucketStart,
      },
    },
  });

  if (!existing) {
    await prisma.quotaCounter.create({
      data: {
        apiKeyHash: params.apiKeyHash,
        bucketType: params.bucketType,
        bucketStart: params.bucketStart,
        requests: params.requests,
        tokens: params.tokens,
        costUsd: params.costUsd,
      },
    });
    return;
  }

  await prisma.quotaCounter.update({
    where: {
      apiKeyHash_bucketType_bucketStart: {
        apiKeyHash: params.apiKeyHash,
        bucketType: params.bucketType,
        bucketStart: params.bucketStart,
      },
    },
    data: {
      requests: { increment: params.requests },
      tokens: { increment: params.tokens },
      costUsd: { increment: params.costUsd },
    },
  });
}

export async function getQuotaBucket(params: {
  apiKeyHash: string;
  bucketType: 'minute' | 'day' | 'month';
  bucketStart: string;
}) {
  const row = await prisma.quotaCounter.findUnique({
    where: {
      apiKeyHash_bucketType_bucketStart: {
        apiKeyHash: params.apiKeyHash,
        bucketType: params.bucketType,
        bucketStart: params.bucketStart,
      },
    },
  });

  return {
    requests: row?.requests ?? 0,
    tokens: row?.tokens ?? 0,
    cost_usd: row?.costUsd ?? 0,
  };
}

export async function getUsageSummary(apiKeyHash: string, window: 'day' | 'month') {
  const prefix = window === 'day' ? new Date().toISOString().slice(0, 10) : new Date().toISOString().slice(0, 7);
  const rows = await prisma.quotaCounter.findMany({
    where: {
      apiKeyHash,
      bucketType: window === 'day' ? 'day' : 'month',
      bucketStart: { startsWith: prefix },
    },
    select: { requests: true, tokens: true, costUsd: true },
  });

  return rows.reduce(
    (acc: any, row: any) => {
      acc.requests += row.requests;
      acc.tokens += row.tokens;
      acc.cost_usd += row.costUsd;
      return acc;
    },
    { requests: 0, tokens: 0, cost_usd: 0 },
  );
}

export async function getHistory(sessionId: string, limit = 50, cursor?: string | null) {
  const effectiveLimit = Math.min(Math.max(limit, 1), 100);
  const rows = await prisma.message.findMany({
    where: {
      sessionId,
      ...(cursor ? { createdAt: { lt: new Date(cursor) } } : {}),
    },
    orderBy: { createdAt: 'desc' },
    take: effectiveLimit,
    select: {
      id: true,
      role: true,
      contentRedacted: true,
      language: true,
      createdAt: true,
    },
  });

  const ordered = [...rows].reverse().map((row: any) => ({
    id: row.id,
    role: row.role,
    content: row.contentRedacted,
    language: row.language,
    created_at: row.createdAt.toISOString(),
  }));

  return {
    items: ordered,
    next_cursor: rows.length === effectiveLimit ? rows[rows.length - 1]?.createdAt.toISOString() ?? null : null,
  };
}

export async function getTelemetry(sessionId: string, limit = 20) {
  const effectiveLimit = Math.min(Math.max(limit, 1), 100);
  const rows = await prisma.requestEvent.findMany({
    where: { sessionId },
    orderBy: { startedAt: 'desc' },
    take: effectiveLimit,
  });

  return rows.map((row: any) => ({
    id: row.id,
    route: row.route,
    status: row.status,
    started_at: row.startedAt.toISOString(),
    finished_at: row.finishedAt?.toISOString() ?? null,
    latency_ms: row.latencyMs,
    guardrail_blocked: row.guardrailBlocked,
    guardrail_reasons: parseJsonArray<string[]>(row.guardrailReasons, []),
    injection_score: row.injectionScore,
    blocked_stage: row.blockedStage,
    retrieval_query: row.retrievalQuery,
    retrieved_chunk_ids: parseJsonArray<number[]>(row.retrievedChunkIds, []),
    retrieval_scores: parseJsonArray<Array<Record<string, unknown>>>(row.retrievalScores, []),
    retrieval_fallback_used: row.retrievalFallbackUsed,
    prompt_redacted: row.promptRedacted,
    response_redacted: row.responseRedacted,
    prompt_tokens: row.promptTokens,
    completion_tokens: row.completionTokens,
    embedding_tokens: row.embeddingTokens,
    total_tokens: row.totalTokens,
    estimated_cost_usd: row.estimatedCostUsd,
    metadata_json: parseJsonArray<Record<string, unknown>>(row.metadataJson, {}),
  }));
}

export async function upsertCourse(params: { courseId: string; apiKeyHash: string; title?: string | null }) {
  const existing = await prisma.course.findUnique({ where: { courseId: params.courseId }, select: { apiKeyHash: true } });
  if (existing?.apiKeyHash && existing.apiKeyHash !== params.apiKeyHash) {
    throw new Error('course ownership mismatch');
  }
  const now = new Date();
  await prisma.course.upsert({
    where: { courseId: params.courseId },
    update: {
      apiKeyHash: params.apiKeyHash,
      title: params.title ?? undefined,
      updatedAt: now,
    },
    create: {
      courseId: params.courseId,
      apiKeyHash: params.apiKeyHash,
      title: params.title ?? null,
      createdAt: now,
      updatedAt: now,
    },
  });
}

export async function createCourseIndexVersion(params: {
  id: string;
  courseId: string;
  apiKeyHash: string;
  versionLabel?: string | null;
  embeddingModel?: string | null;
}) {
  const now = new Date();
  await prisma.courseIndexVersion.create({
    data: {
      id: params.id,
      courseId: params.courseId,
      apiKeyHash: params.apiKeyHash,
      versionLabel: params.versionLabel ?? null,
      status: 'building',
      sourceCount: 0,
      chunkCount: 0,
      embeddingModel: params.embeddingModel ?? null,
      createdAt: now,
      updatedAt: now,
    },
  });
}

export async function activateCourseIndexVersion(params: { courseId: string; versionId: string; apiKeyHash: string }) {
  const version = await prisma.courseIndexVersion.findFirst({
    where: {
      id: params.versionId,
      courseId: params.courseId,
      apiKeyHash: params.apiKeyHash,
    },
    select: { id: true, status: true },
  });

  if (!version?.id) {
    throw new Error('course version not found');
  }
  if (version.status !== 'ready') {
    throw new Error('course version is not ready');
  }

  await prisma.course.updateMany({
    where: {
      courseId: params.courseId,
      apiKeyHash: params.apiKeyHash,
    },
    data: {
      activeVersionId: params.versionId,
      updatedAt: new Date(),
    },
  });
}

export async function completeCourseIndexVersion(params: {
  id: string;
  sourceCount: number;
  chunkCount: number;
  status?: 'ready' | 'failed';
}) {
  await prisma.courseIndexVersion.update({
    where: { id: params.id },
    data: {
      sourceCount: params.sourceCount,
      chunkCount: params.chunkCount,
      status: params.status ?? 'ready',
      updatedAt: new Date(),
    },
  });
}

export async function insertCourseMaterial(params: {
  id: string;
  versionId: string;
  courseId: string;
  fileName: string;
  mediaType: string;
  sourceKind: string;
  sourceHash?: string | null;
  structure: unknown;
  extractedText: string;
}) {
  await prisma.courseMaterial.create({
    data: {
      id: params.id,
      versionId: params.versionId,
      courseId: params.courseId,
      fileName: params.fileName,
      mediaType: params.mediaType,
      sourceKind: params.sourceKind,
      sourceHash: params.sourceHash ?? null,
      structureJson: jsonValue(params.structure ?? {}),
      extractedText: params.extractedText,
      createdAt: new Date(),
    },
  });
}

export async function findExistingMaterialByHash(params: { courseId: string; versionId: string; sourceHash: string }) {
  const row = await prisma.courseMaterial.findFirst({
    where: {
      courseId: params.courseId,
      versionId: params.versionId,
      sourceHash: params.sourceHash,
    },
    select: { id: true, fileName: true },
  });

  return row ? { id: row.id, file_name: row.fileName } : null;
}

export async function insertCourseChunk(params: {
  id: string;
  versionId: string;
  courseId: string;
  materialId: string;
  chunkIndex: number;
  label: string;
  section?: string | null;
  text: string;
  tokenEstimate: number;
  embedding: number[];
}) {
  await prisma.$transaction(async (tx: any) => {
    await tx.courseChunk.create({
      data: {
        id: params.id,
        versionId: params.versionId,
        courseId: params.courseId,
        materialId: params.materialId,
        chunkIndex: params.chunkIndex,
        label: params.label,
        section: params.section ?? null,
        text: params.text,
        tokenEstimate: params.tokenEstimate,
        embeddingJson: jsonValue(params.embedding),
        createdAt: new Date(),
      },
    });

    if (vectorReady) {
      await tx.$executeRawUnsafe(
        'UPDATE "course_chunks" SET "embedding_vector" = $1::vector WHERE "id" = $2',
        toVectorLiteral(params.embedding),
        params.id,
      );
    }
  });
}

export async function listCourseIndexVersions(courseId: string, apiKeyHash: string) {
  const rows = await prisma.courseIndexVersion.findMany({
    where: { courseId, apiKeyHash },
    orderBy: { createdAt: 'desc' },
  });
  const course = await prisma.course.findUnique({ where: { courseId }, select: { activeVersionId: true } });

  return rows.map((row: any) => ({
    id: row.id,
    course_id: row.courseId,
    version_label: row.versionLabel,
    status: row.status,
    source_count: row.sourceCount,
    chunk_count: row.chunkCount,
    embedding_model: row.embeddingModel,
    created_at: row.createdAt.toISOString(),
    updated_at: row.updatedAt.toISOString(),
    is_active: course?.activeVersionId === row.id ? 1 : 0,
  }));
}

export async function resolveCourseIndexVersion(params: { courseId?: string | null; versionId?: string | null; apiKeyHash: string }) {
  if (params.versionId) {
    const row = await prisma.courseIndexVersion.findFirst({
      where: { id: params.versionId, apiKeyHash: params.apiKeyHash },
    });
    if (!row) {
      return null;
    }
    const course = await prisma.course.findUnique({ where: { courseId: row.courseId }, select: { activeVersionId: true } });
    return {
      id: row.id,
      course_id: row.courseId,
      version_label: row.versionLabel,
      status: row.status,
      source_count: row.sourceCount,
      chunk_count: row.chunkCount,
      embedding_model: row.embeddingModel,
      created_at: row.createdAt.toISOString(),
      updated_at: row.updatedAt.toISOString(),
      is_active: course?.activeVersionId === row.id ? 1 : 0,
    };
  }

  if (!params.courseId) {
    return null;
  }

  const course = await prisma.course.findUnique({
    where: { courseId: params.courseId },
    select: { activeVersionId: true, apiKeyHash: true },
  });
  if (!course || course.apiKeyHash !== params.apiKeyHash) {
    return null;
  }

  const rows = await prisma.courseIndexVersion.findMany({
    where: { courseId: params.courseId, apiKeyHash: params.apiKeyHash, status: 'ready' },
    orderBy: { createdAt: 'desc' },
  });
  if (!rows.length) {
    return null;
  }

  const selected = rows.find((row: any) => row.id === course.activeVersionId) ?? rows[0];

  return {
    id: selected.id,
    course_id: selected.courseId,
    version_label: selected.versionLabel,
    status: selected.status,
    source_count: selected.sourceCount,
    chunk_count: selected.chunkCount,
    embedding_model: selected.embeddingModel,
    created_at: selected.createdAt.toISOString(),
    updated_at: selected.updatedAt.toISOString(),
    is_active: course.activeVersionId === selected.id ? 1 : 0,
  };
}

export async function getCourseChunksByVersion(versionId: string) {
  const rows = await prisma.courseChunk.findMany({
    where: { versionId },
    orderBy: [{ materialId: 'asc' }, { chunkIndex: 'asc' }],
  });

  return rows.map((row: any) => ({
    id: row.id,
    material_id: row.materialId,
    chunk_index: row.chunkIndex,
    label: row.label,
    section: row.section,
    text: row.text,
    token_estimate: row.tokenEstimate,
    embedding_json: JSON.stringify(row.embeddingJson ?? []),
  }));
}

export async function searchCourseChunkCandidates(params: {
  versionId: string;
  queryEmbedding: number[];
  limit: number;
}) {
  if (!vectorReady) {
    return null;
  }

  const candidateLimit = Math.max(params.limit * VECTOR_CANDIDATE_MULTIPLIER, params.limit);

  let rows: Array<{
    id: string;
    material_id: string;
    chunk_index: number;
    label: string;
    section: string | null;
    text: string;
    token_estimate: number;
    vector_score: number;
  }>;

  try {
    rows = (await prisma.$queryRawUnsafe(
      `
        SELECT
          id,
          material_id,
          chunk_index,
          label,
          section,
          text,
          token_estimate,
          1 - ("embedding_vector" <=> $1::vector) AS vector_score
        FROM "course_chunks"
        WHERE "version_id" = $2
          AND "embedding_vector" IS NOT NULL
        ORDER BY "embedding_vector" <=> $1::vector
        LIMIT $3
      `,
      toVectorLiteral(params.queryEmbedding),
      params.versionId,
      candidateLimit,
    )) as Array<{
        id: string;
        material_id: string;
        chunk_index: number;
        label: string;
        section: string | null;
        text: string;
        token_estimate: number;
        vector_score: number;
      }>;
  } catch (error) {
    vectorReady = false;
    vectorError = error instanceof Error ? error.message : 'unknown pgvector query error';
    return null;
  }

  return rows.map((row: any) => ({
    id: row.id,
    material_id: row.material_id,
    chunk_index: row.chunk_index,
    label: row.label,
    section: row.section,
    text: row.text,
    token_estimate: row.token_estimate,
    vector_score: Number(row.vector_score ?? 0),
  }));
}
