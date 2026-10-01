const DB_PROVIDER =
  (process.env.AI_DB_PROVIDER ?? '').trim().toLowerCase() ||
  ((process.env.DATABASE_URL ?? '').trim() ? 'postgres' : 'sqlite');

const backendPromise = DB_PROVIDER === 'postgres' ? import('@/lib/ai/db-postgres') : import('@/lib/ai/db-sqlite');

async function getBackend() {
  return backendPromise;
}

export async function maybeRunDbMaintenance() {
  const backend = await getBackend();
  return backend.maybeRunDbMaintenance();
}

export async function getDbHealth() {
  const backend = await getBackend();
  return backend.getDbHealth();
}

export async function upsertSession(sessionId: string, apiKeyHash: string, title?: string | null, lastActivityAt?: string) {
  const backend = await getBackend();
  return backend.upsertSession(sessionId, apiKeyHash, title, lastActivityAt);
}

export async function assertSessionOwnership(sessionId: string, apiKeyHash: string) {
  const backend = await getBackend();
  return backend.assertSessionOwnership(sessionId, apiKeyHash);
}

export async function insertMessage(params: {
  id: string;
  sessionId: string;
  role: 'user' | 'assistant';
  content: string;
  language: string;
  createdAt?: string;
}) {
  const backend = await getBackend();
  return backend.insertMessage(params);
}

export async function createRequestEvent(params: {
  id: string;
  sessionId: string;
  apiKeyHash: string;
  route: string;
  metadata?: unknown;
}) {
  const backend = await getBackend();
  return backend.createRequestEvent(params);
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
  const backend = await getBackend();
  return backend.finalizeRequestEvent(params);
}

export async function incrementQuotaBucket(params: {
  apiKeyHash: string;
  bucketType: 'minute' | 'day' | 'month';
  bucketStart: string;
  requests: number;
  tokens: number;
  costUsd: number;
}) {
  const backend = await getBackend();
  return backend.incrementQuotaBucket(params);
}

export async function getQuotaBucket(params: {
  apiKeyHash: string;
  bucketType: 'minute' | 'day' | 'month';
  bucketStart: string;
}) {
  const backend = await getBackend();
  return backend.getQuotaBucket(params);
}

export async function getUsageSummary(apiKeyHash: string, window: 'day' | 'month') {
  const backend = await getBackend();
  return backend.getUsageSummary(apiKeyHash, window);
}

export async function getHistory(sessionId: string, limit = 50, cursor?: string | null) {
  const backend = await getBackend();
  return backend.getHistory(sessionId, limit, cursor);
}

export async function getTelemetry(sessionId: string, limit = 20) {
  const backend = await getBackend();
  return backend.getTelemetry(sessionId, limit);
}

export async function initAiDatabase() {
  const backend = await getBackend();
  return backend.initAiDatabase();
}

export async function upsertCourse(params: { courseId: string; apiKeyHash: string; title?: string | null }) {
  const backend = await getBackend();
  return backend.upsertCourse(params);
}

export async function createCourseIndexVersion(params: {
  id: string;
  courseId: string;
  apiKeyHash: string;
  versionLabel?: string | null;
  embeddingModel?: string | null;
}) {
  const backend = await getBackend();
  return backend.createCourseIndexVersion(params);
}

export async function activateCourseIndexVersion(params: { courseId: string; versionId: string; apiKeyHash: string }) {
  const backend = await getBackend();
  return backend.activateCourseIndexVersion(params);
}

export async function completeCourseIndexVersion(params: { id: string; sourceCount: number; chunkCount: number; status?: 'ready' | 'failed' }) {
  const backend = await getBackend();
  return backend.completeCourseIndexVersion(params);
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
  const backend = await getBackend();
  return backend.insertCourseMaterial(params);
}

export async function findExistingMaterialByHash(params: { courseId: string; versionId: string; sourceHash: string }) {
  const backend = await getBackend();
  return backend.findExistingMaterialByHash(params);
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
  const backend = await getBackend();
  return backend.insertCourseChunk(params);
}

export async function listCourseIndexVersions(courseId: string, apiKeyHash: string) {
  const backend = await getBackend();
  return backend.listCourseIndexVersions(courseId, apiKeyHash);
}

export async function resolveCourseIndexVersion(params: { courseId?: string | null; versionId?: string | null; apiKeyHash: string }) {
  const backend = await getBackend();
  return backend.resolveCourseIndexVersion(params);
}

export async function getCourseChunksByVersion(versionId: string) {
  const backend = await getBackend();
  return backend.getCourseChunksByVersion(versionId);
}

export async function searchCourseChunkCandidates(params: { versionId: string; queryEmbedding: number[]; limit: number }) {
  const backend = await getBackend();
  if (typeof backend.searchCourseChunkCandidates !== 'function') {
    return null;
  }
  return backend.searchCourseChunkCandidates(params);
}
