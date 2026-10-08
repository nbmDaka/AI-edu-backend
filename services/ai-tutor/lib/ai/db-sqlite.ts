import { mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { redactSensitiveText } from '@/lib/ai/redaction';

const DB_PATH = resolve(process.cwd(), process.env.AI_DB_PATH ?? 'data/ai-module.db');
const RETENTION_DAYS = Number(process.env.AI_TELEMETRY_RETENTION_DAYS ?? 30);
const CLEANUP_INTERVAL = Number(process.env.AI_DB_CLEANUP_EVERY_REQUESTS ?? 25);

let dbInstance: DatabaseSync | null = null;
let requestCounter = 0;

function ensureDb(): DatabaseSync {
  if (dbInstance) {
    return dbInstance;
  }

  mkdirSync(dirname(DB_PATH), { recursive: true });
  const db = new DatabaseSync(DB_PATH);
  db.exec(`
    PRAGMA journal_mode = WAL;
    PRAGMA synchronous = NORMAL;

    CREATE TABLE IF NOT EXISTS sessions (
      session_id TEXT PRIMARY KEY,
      api_key_hash TEXT NOT NULL,
      title TEXT,
      created_at TEXT NOT NULL,
      last_activity_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS messages (
      id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL,
      role TEXT NOT NULL,
      content_raw TEXT NOT NULL,
      content_redacted TEXT NOT NULL,
      language TEXT NOT NULL,
      created_at TEXT NOT NULL,
      FOREIGN KEY(session_id) REFERENCES sessions(session_id)
    );

    CREATE TABLE IF NOT EXISTS request_events (
      id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL,
      api_key_hash TEXT NOT NULL,
      route TEXT NOT NULL,
      status INTEGER NOT NULL,
      started_at TEXT NOT NULL,
      finished_at TEXT,
      latency_ms INTEGER,
      guardrail_blocked INTEGER NOT NULL DEFAULT 0,
      guardrail_reasons TEXT NOT NULL DEFAULT '[]',
      injection_score REAL NOT NULL DEFAULT 0,
      blocked_stage TEXT,
      retrieval_query TEXT,
      retrieved_chunk_ids TEXT,
      retrieval_scores TEXT,
      retrieval_fallback_used INTEGER NOT NULL DEFAULT 0,
      prompt_raw TEXT,
      prompt_redacted TEXT,
      response_raw TEXT,
      response_redacted TEXT,
      prompt_tokens INTEGER NOT NULL DEFAULT 0,
      completion_tokens INTEGER NOT NULL DEFAULT 0,
      embedding_tokens INTEGER NOT NULL DEFAULT 0,
      total_tokens INTEGER NOT NULL DEFAULT 0,
      estimated_cost_usd REAL NOT NULL DEFAULT 0,
      metadata_json TEXT NOT NULL DEFAULT '{}',
      FOREIGN KEY(session_id) REFERENCES sessions(session_id)
    );

    CREATE TABLE IF NOT EXISTS quota_counters (
      api_key_hash TEXT NOT NULL,
      bucket_type TEXT NOT NULL,
      bucket_start TEXT NOT NULL,
      requests INTEGER NOT NULL DEFAULT 0,
      tokens INTEGER NOT NULL DEFAULT 0,
      cost_usd REAL NOT NULL DEFAULT 0,
      PRIMARY KEY(api_key_hash, bucket_type, bucket_start)
    );

    CREATE TABLE IF NOT EXISTS courses (
      course_id TEXT PRIMARY KEY,
      api_key_hash TEXT NOT NULL,
      title TEXT,
      active_version_id TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS course_index_versions (
      id TEXT PRIMARY KEY,
      course_id TEXT NOT NULL,
      api_key_hash TEXT NOT NULL,
      version_label TEXT,
      status TEXT NOT NULL,
      source_count INTEGER NOT NULL DEFAULT 0,
      chunk_count INTEGER NOT NULL DEFAULT 0,
      embedding_model TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      FOREIGN KEY(course_id) REFERENCES courses(course_id)
    );

    CREATE TABLE IF NOT EXISTS course_materials (
      id TEXT PRIMARY KEY,
      version_id TEXT NOT NULL,
      course_id TEXT NOT NULL,
      file_name TEXT NOT NULL,
      media_type TEXT NOT NULL,
      source_kind TEXT NOT NULL,
      source_hash TEXT,
      structure_json TEXT NOT NULL DEFAULT '{}',
      extracted_text TEXT NOT NULL,
      created_at TEXT NOT NULL,
      FOREIGN KEY(version_id) REFERENCES course_index_versions(id),
      FOREIGN KEY(course_id) REFERENCES courses(course_id)
    );

    CREATE TABLE IF NOT EXISTS course_chunks (
      id TEXT PRIMARY KEY,
      version_id TEXT NOT NULL,
      course_id TEXT NOT NULL,
      material_id TEXT NOT NULL,
      chunk_index INTEGER NOT NULL,
      label TEXT NOT NULL,
      section TEXT,
      text TEXT NOT NULL,
      token_estimate INTEGER NOT NULL DEFAULT 0,
      embedding_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      FOREIGN KEY(version_id) REFERENCES course_index_versions(id),
      FOREIGN KEY(course_id) REFERENCES courses(course_id),
      FOREIGN KEY(material_id) REFERENCES course_materials(id)
    );

    CREATE INDEX IF NOT EXISTS idx_messages_session_created ON messages(session_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_request_events_session_started ON request_events(session_id, started_at DESC);
    CREATE INDEX IF NOT EXISTS idx_quota_counters_type_start ON quota_counters(bucket_type, bucket_start);
    CREATE INDEX IF NOT EXISTS idx_course_versions_course_created ON course_index_versions(course_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_course_chunks_version_material ON course_chunks(version_id, material_id, chunk_index);
    CREATE INDEX IF NOT EXISTS idx_course_materials_hash ON course_materials(course_id, source_hash);
  `);

  try {
    db.exec('ALTER TABLE courses ADD COLUMN active_version_id TEXT');
  } catch {}
  try {
    db.exec('ALTER TABLE course_materials ADD COLUMN source_hash TEXT');
  } catch {}
  try {
    db.exec('ALTER TABLE course_chunks ADD COLUMN section TEXT');
  } catch {}

  dbInstance = db;
  cleanupExpiredRows();
  return db;
}

function isoNow(): string {
  return new Date().toISOString();
}

function jsonStringify(value: unknown): string {
  return JSON.stringify(value ?? null);
}

function redact(value: string | null | undefined): string {
  return redactSensitiveText(value ?? '');
}

function cleanupExpiredRows() {
  const db = ensureDb();
  const cutoff = new Date(Date.now() - RETENTION_DAYS * 24 * 60 * 60 * 1000).toISOString();
  db.prepare('DELETE FROM request_events WHERE started_at < ?').run(cutoff);
  db.prepare('DELETE FROM messages WHERE created_at < ?').run(cutoff);
  db.prepare('DELETE FROM sessions WHERE last_activity_at < ?').run(cutoff);
  db.prepare('DELETE FROM quota_counters WHERE bucket_start < ?').run(cutoff);
}

export function maybeRunDbMaintenance() {
  requestCounter += 1;
  if (requestCounter % CLEANUP_INTERVAL === 0) {
    cleanupExpiredRows();
  }
}

export function getDbHealth() {
  try {
    const db = ensureDb();
    const row = db.prepare('SELECT COUNT(*) as count FROM sessions').get() as { count: number };
    return {
      status: 'ok' as const,
      path: DB_PATH,
      sessions: row.count,
    };
  } catch (error) {
    return {
      status: 'error' as const,
      path: DB_PATH,
      details: error instanceof Error ? error.message : 'unknown error',
    };
  }
}

export function upsertSession(
  sessionId: string,
  apiKeyHash: string,
  title?: string | null,
  lastActivityAt = isoNow(),
) {
  const db = ensureDb();
  const existing = db
    .prepare('SELECT api_key_hash FROM sessions WHERE session_id = ?')
    .get(sessionId) as { api_key_hash?: string } | undefined;
  if (existing?.api_key_hash && existing.api_key_hash !== apiKeyHash) {
    throw new Error('session ownership mismatch');
  }
  db.prepare(
    `
      INSERT INTO sessions (session_id, api_key_hash, title, created_at, last_activity_at)
      VALUES (?, ?, ?, ?, ?)
      ON CONFLICT(session_id) DO UPDATE SET
        api_key_hash = excluded.api_key_hash,
        title = COALESCE(excluded.title, sessions.title),
        last_activity_at = excluded.last_activity_at
    `,
  ).run(sessionId, apiKeyHash, title ?? null, lastActivityAt, lastActivityAt);
}

export function assertSessionOwnership(sessionId: string, apiKeyHash: string) {
  const db = ensureDb();
  const existing = db
    .prepare('SELECT api_key_hash FROM sessions WHERE session_id = ?')
    .get(sessionId) as { api_key_hash?: string } | undefined;
  if (!existing?.api_key_hash) {
    return { exists: false };
  }
  if (existing.api_key_hash !== apiKeyHash) {
    throw new Error('session ownership mismatch');
  }
  return { exists: true };
}

export function insertMessage(params: {
  id: string;
  sessionId: string;
  role: 'user' | 'assistant';
  content: string;
  language: string;
  createdAt?: string;
}) {
  const db = ensureDb();
  const createdAt = params.createdAt ?? isoNow();
  db.prepare(
    `
      INSERT OR IGNORE INTO messages (id, session_id, role, content_raw, content_redacted, language, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?)
    `,
  ).run(
    params.id,
    params.sessionId,
    params.role,
    params.content,
    redact(params.content),
    params.language,
    createdAt,
  );
}

export function createRequestEvent(params: {
  id: string;
  sessionId: string;
  apiKeyHash: string;
  route: string;
  metadata?: unknown;
}) {
  const db = ensureDb();
  const startedAt = isoNow();
  db.prepare(
    `
      INSERT INTO request_events (
        id, session_id, api_key_hash, route, status, started_at, metadata_json
      ) VALUES (?, ?, ?, ?, ?, ?, ?)
    `,
  ).run(params.id, params.sessionId, params.apiKeyHash, params.route, 102, startedAt, jsonStringify(params.metadata ?? {}));
  return { startedAt };
}

export function finalizeRequestEvent(params: {
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
  const db = ensureDb();
  const finishedAt = isoNow();
  const started = db
    .prepare('SELECT started_at FROM request_events WHERE id = ?')
    .get(params.id) as { started_at?: string } | undefined;
  const latencyMs = started?.started_at ? Math.max(0, Date.parse(finishedAt) - Date.parse(started.started_at)) : null;

  db.prepare(
    `
      UPDATE request_events
      SET
        status = ?,
        finished_at = ?,
        latency_ms = ?,
        guardrail_blocked = ?,
        guardrail_reasons = ?,
        injection_score = ?,
        blocked_stage = ?,
        retrieval_query = ?,
        retrieved_chunk_ids = ?,
        retrieval_scores = ?,
        retrieval_fallback_used = ?,
        prompt_raw = ?,
        prompt_redacted = ?,
        response_raw = ?,
        response_redacted = ?,
        prompt_tokens = ?,
        completion_tokens = ?,
        embedding_tokens = ?,
        total_tokens = ?,
        estimated_cost_usd = ?
      WHERE id = ?
    `,
  ).run(
    params.status,
    finishedAt,
    latencyMs,
    params.guardrailBlocked ? 1 : 0,
    jsonStringify(params.guardrailReasons),
    params.injectionScore,
    params.blockedStage,
    params.retrievalQuery ?? null,
    jsonStringify(params.retrievedChunkIds ?? []),
    jsonStringify(params.retrievalScores ?? []),
    params.retrievalFallbackUsed ? 1 : 0,
    params.prompt ?? null,
    params.prompt ? redact(params.prompt) : null,
    params.response ?? null,
    params.response ? redact(params.response) : null,
    params.usage?.prompt_tokens ?? 0,
    params.usage?.completion_tokens ?? 0,
    params.usage?.embedding_tokens ?? 0,
    params.usage?.total_tokens ?? 0,
    params.usage?.estimated_cost_usd ?? 0,
    params.id,
  );
}

export function incrementQuotaBucket(params: {
  apiKeyHash: string;
  bucketType: 'minute' | 'day' | 'month';
  bucketStart: string;
  requests: number;
  tokens: number;
  costUsd: number;
}) {
  const db = ensureDb();
  db.prepare(
    `
      INSERT INTO quota_counters (api_key_hash, bucket_type, bucket_start, requests, tokens, cost_usd)
      VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(api_key_hash, bucket_type, bucket_start) DO UPDATE SET
        requests = quota_counters.requests + excluded.requests,
        tokens = quota_counters.tokens + excluded.tokens,
        cost_usd = quota_counters.cost_usd + excluded.cost_usd
    `,
  ).run(
    params.apiKeyHash,
    params.bucketType,
    params.bucketStart,
    params.requests,
    params.tokens,
    params.costUsd,
  );
}

export function getQuotaBucket(params: {
  apiKeyHash: string;
  bucketType: 'minute' | 'day' | 'month';
  bucketStart: string;
}) {
  const db = ensureDb();
  const row = db
    .prepare(
      'SELECT requests, tokens, cost_usd FROM quota_counters WHERE api_key_hash = ? AND bucket_type = ? AND bucket_start = ?',
    )
    .get(params.apiKeyHash, params.bucketType, params.bucketStart) as
    | { requests: number; tokens: number; cost_usd: number }
    | undefined;

  return {
    requests: row?.requests ?? 0,
    tokens: row?.tokens ?? 0,
    cost_usd: row?.cost_usd ?? 0,
  };
}

export function getUsageSummary(apiKeyHash: string, window: 'day' | 'month') {
  const db = ensureDb();
  const bucketType = window === 'day' ? 'day' : 'month';
  const prefix =
    window === 'day'
      ? new Date().toISOString().slice(0, 10)
      : new Date().toISOString().slice(0, 7);

  const row = db
    .prepare(
      `
        SELECT
          COALESCE(SUM(requests), 0) as requests,
          COALESCE(SUM(tokens), 0) as tokens,
          COALESCE(SUM(cost_usd), 0) as cost_usd
        FROM quota_counters
        WHERE api_key_hash = ? AND bucket_type = ? AND bucket_start LIKE ?
      `,
    )
    .get(apiKeyHash, bucketType, `${prefix}%`) as {
    requests: number;
    tokens: number;
    cost_usd: number;
  };

  return row;
}

export function getHistory(sessionId: string, limit = 50, cursor?: string | null) {
  const db = ensureDb();
  const effectiveLimit = Math.min(Math.max(limit, 1), 100);
  const rows = cursor
    ? (db
        .prepare(
          `
            SELECT id, role, content_redacted, language, created_at
            FROM messages
            WHERE session_id = ? AND created_at < ?
            ORDER BY created_at DESC
            LIMIT ?
          `,
        )
        .all(sessionId, cursor, effectiveLimit) as Array<{
        id: string;
        role: string;
        content_redacted: string;
        language: string;
        created_at: string;
      }>)
    : (db
        .prepare(
          `
            SELECT id, role, content_redacted, language, created_at
            FROM messages
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
          `,
        )
        .all(sessionId, effectiveLimit) as Array<{
        id: string;
        role: string;
        content_redacted: string;
        language: string;
        created_at: string;
      }>);

  const ordered = [...rows].reverse().map((row: any) => ({
    id: row.id,
    role: row.role,
    content: row.content_redacted,
    language: row.language,
    created_at: row.created_at,
  }));

  return {
    items: ordered,
    next_cursor: rows.length === effectiveLimit ? rows[rows.length - 1]?.created_at ?? null : null,
  };
}

export function getTelemetry(sessionId: string, limit = 20) {
  const db = ensureDb();
  const effectiveLimit = Math.min(Math.max(limit, 1), 100);
  const rows = db
    .prepare(
      `
        SELECT
          id,
          route,
          status,
          started_at,
          finished_at,
          latency_ms,
          guardrail_blocked,
          guardrail_reasons,
          injection_score,
          blocked_stage,
          retrieval_query,
          retrieved_chunk_ids,
          retrieval_scores,
          retrieval_fallback_used,
          prompt_redacted,
          response_redacted,
          prompt_tokens,
          completion_tokens,
          embedding_tokens,
          total_tokens,
          estimated_cost_usd,
          metadata_json
        FROM request_events
        WHERE session_id = ?
        ORDER BY started_at DESC
        LIMIT ?
      `,
    )
    .all(sessionId, effectiveLimit) as Array<Record<string, unknown>>;

  return rows.map((row: any) => ({
    ...row,
    guardrail_reasons: JSON.parse(String(row.guardrail_reasons ?? '[]')),
    retrieved_chunk_ids: JSON.parse(String(row.retrieved_chunk_ids ?? '[]')),
    retrieval_scores: JSON.parse(String(row.retrieval_scores ?? '[]')),
    metadata_json: JSON.parse(String(row.metadata_json ?? '{}')),
    guardrail_blocked: Boolean(row.guardrail_blocked),
    retrieval_fallback_used: Boolean(row.retrieval_fallback_used),
  }));
}

export function initAiDatabase() {
  ensureDb();
}

export function upsertCourse(params: { courseId: string; apiKeyHash: string; title?: string | null }) {
  const db = ensureDb();
  const now = isoNow();
  const existing = db
    .prepare('SELECT api_key_hash FROM courses WHERE course_id = ?')
    .get(params.courseId) as { api_key_hash?: string } | undefined;
  if (existing?.api_key_hash && existing.api_key_hash !== params.apiKeyHash) {
    throw new Error('course ownership mismatch');
  }

  db.prepare(
    `
      INSERT INTO courses (course_id, api_key_hash, title, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?)
      ON CONFLICT(course_id) DO UPDATE SET
        api_key_hash = excluded.api_key_hash,
        title = COALESCE(excluded.title, courses.title),
        updated_at = excluded.updated_at
    `,
  ).run(params.courseId, params.apiKeyHash, params.title ?? null, now, now);
}

export function createCourseIndexVersion(params: {
  id: string;
  courseId: string;
  apiKeyHash: string;
  versionLabel?: string | null;
  embeddingModel?: string | null;
}) {
  const db = ensureDb();
  const now = isoNow();
  db.prepare(
    `
      INSERT INTO course_index_versions (
        id, course_id, api_key_hash, version_label, status, source_count, chunk_count, embedding_model, created_at, updated_at
      ) VALUES (?, ?, ?, ?, 'building', 0, 0, ?, ?, ?)
    `,
  ).run(params.id, params.courseId, params.apiKeyHash, params.versionLabel ?? null, params.embeddingModel ?? null, now, now);
}

export function activateCourseIndexVersion(params: {
  courseId: string;
  versionId: string;
  apiKeyHash: string;
}) {
  const db = ensureDb();
  const version = db
    .prepare(
      `
        SELECT id, status
        FROM course_index_versions
        WHERE id = ? AND course_id = ? AND api_key_hash = ?
      `,
    )
    .get(params.versionId, params.courseId, params.apiKeyHash) as { id?: string; status?: string } | undefined;

  if (!version?.id) {
    throw new Error('course version not found');
  }
  if (version.status !== 'ready') {
    throw new Error('course version is not ready');
  }

  db.prepare('UPDATE courses SET active_version_id = ?, updated_at = ? WHERE course_id = ? AND api_key_hash = ?').run(
    params.versionId,
    isoNow(),
    params.courseId,
    params.apiKeyHash,
  );
}

export function completeCourseIndexVersion(params: {
  id: string;
  sourceCount: number;
  chunkCount: number;
  status?: 'ready' | 'failed';
}) {
  const db = ensureDb();
  db.prepare(
    `
      UPDATE course_index_versions
      SET source_count = ?, chunk_count = ?, status = ?, updated_at = ?
      WHERE id = ?
    `,
  ).run(params.sourceCount, params.chunkCount, params.status ?? 'ready', isoNow(), params.id);
}

export function insertCourseMaterial(params: {
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
  const db = ensureDb();
  db.prepare(
    `
      INSERT INTO course_materials (
        id, version_id, course_id, file_name, media_type, source_kind, source_hash, structure_json, extracted_text, created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `,
  ).run(
    params.id,
    params.versionId,
    params.courseId,
    params.fileName,
    params.mediaType,
    params.sourceKind,
    params.sourceHash ?? null,
    jsonStringify(params.structure),
    params.extractedText,
    isoNow(),
  );
}

export function findExistingMaterialByHash(params: {
  courseId: string;
  versionId: string;
  sourceHash: string;
}) {
  const db = ensureDb();
  return (
    db
      .prepare(
        `
          SELECT id, file_name
          FROM course_materials
          WHERE course_id = ? AND version_id = ? AND source_hash = ?
          LIMIT 1
        `,
      )
      .get(params.courseId, params.versionId, params.sourceHash) as
      | {
          id: string;
          file_name: string;
        }
      | undefined
  ) ?? null;
}

export function insertCourseChunk(params: {
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
  const db = ensureDb();
  db.prepare(
    `
      INSERT INTO course_chunks (
        id, version_id, course_id, material_id, chunk_index, label, section, text, token_estimate, embedding_json, created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `,
  ).run(
    params.id,
    params.versionId,
    params.courseId,
    params.materialId,
    params.chunkIndex,
    params.label,
    params.section ?? null,
    params.text,
    params.tokenEstimate,
    jsonStringify(params.embedding),
    isoNow(),
  );
}

export function listCourseIndexVersions(courseId: string, apiKeyHash: string) {
  const db = ensureDb();
  return db
    .prepare(
      `
        SELECT v.id, v.course_id, v.version_label, v.status, v.source_count, v.chunk_count, v.embedding_model, v.created_at, v.updated_at,
               CASE WHEN c.active_version_id = v.id THEN 1 ELSE 0 END as is_active
        FROM course_index_versions v
        JOIN courses c ON c.course_id = v.course_id
        WHERE v.course_id = ? AND v.api_key_hash = ?
        ORDER BY v.created_at DESC
      `,
    )
    .all(courseId, apiKeyHash);
}

export function resolveCourseIndexVersion(params: {
  courseId?: string | null;
  versionId?: string | null;
  apiKeyHash: string;
}) {
  const db = ensureDb();
  if (params.versionId) {
    const version = db
      .prepare(
        `
          SELECT v.id, v.course_id, v.version_label, v.status, v.source_count, v.chunk_count, v.embedding_model, v.created_at, v.updated_at,
                 CASE WHEN c.active_version_id = v.id THEN 1 ELSE 0 END as is_active
          FROM course_index_versions v
          JOIN courses c ON c.course_id = v.course_id
          WHERE v.id = ? AND v.api_key_hash = ?
        `,
      )
      .get(params.versionId, params.apiKeyHash) as
      | {
          id: string;
          course_id: string;
          version_label: string | null;
          status: string;
          source_count: number;
          chunk_count: number;
          embedding_model: string | null;
          created_at: string;
          updated_at: string;
          is_active: number;
        }
      | undefined;
    return version ?? null;
  }

  if (!params.courseId) {
    return null;
  }

  const version = db
    .prepare(
      `
        SELECT v.id, v.course_id, v.version_label, v.status, v.source_count, v.chunk_count, v.embedding_model, v.created_at, v.updated_at,
               CASE WHEN c.active_version_id = v.id THEN 1 ELSE 0 END as is_active
        FROM course_index_versions v
        JOIN courses c ON c.course_id = v.course_id
        WHERE v.course_id = ? AND v.api_key_hash = ? AND v.status = 'ready'
        ORDER BY CASE WHEN c.active_version_id = v.id THEN 0 ELSE 1 END, v.created_at DESC
        LIMIT 1
      `,
    )
    .get(params.courseId, params.apiKeyHash) as
    | {
        id: string;
        course_id: string;
        version_label: string | null;
        status: string;
        source_count: number;
        chunk_count: number;
        embedding_model: string | null;
        created_at: string;
        updated_at: string;
        is_active: number;
      }
    | undefined;
  return version ?? null;
}

export function getCourseChunksByVersion(versionId: string) {
  const db = ensureDb();
  return db
    .prepare(
      `
        SELECT id, material_id, chunk_index, label, text, token_estimate, embedding_json
               , section
        FROM course_chunks
        WHERE version_id = ?
        ORDER BY material_id, chunk_index
      `,
    )
    .all(versionId) as Array<{
    id: string;
    material_id: string;
    chunk_index: number;
    label: string;
    section?: string | null;
    text: string;
    token_estimate: number;
    embedding_json: string;
  }>;
}

export function searchCourseChunkCandidates() {
  return null;
}
