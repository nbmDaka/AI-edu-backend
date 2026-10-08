import OpenAI from 'openai';
import { getCourseChunksByVersion, resolveCourseIndexVersion, searchCourseChunkCandidates } from '@/lib/ai/db';
import { sanitizeRetrievedChunks } from '@/lib/ai/guardrails';
import type { RetrievalDiagnostics } from '@/lib/ai/types';

const EMBEDDING_MODEL = process.env.EMBEDDING_MODEL ?? 'text-embedding-3-small';
const DEFAULT_VECTOR_LIMIT = Number(process.env.AI_VECTOR_TOP_K ?? 6);

function cosineSimilarity(a: number[], b: number[]): number {
  let dotProduct = 0;
  let normA = 0;
  let normB = 0;

  for (let i = 0; i < a.length; i += 1) {
    dotProduct += a[i] * b[i];
    normA += a[i] * a[i];
    normB += b[i] * b[i];
  }

  const denominator = Math.sqrt(normA) * Math.sqrt(normB);
  return denominator ? dotProduct / denominator : 0;
}

function normalizeTokens(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[^a-zа-яёәіңғүұқөһ0-9\s]/gi, ' ')
    .split(/\s+/)
    .map((token) => token.trim())
    .filter((token) => token.length >= 3);
}

function keywordScore(query: string, chunkText: string): number {
  const queryTokens = new Set(normalizeTokens(query));
  if (!queryTokens.size) {
    return 0;
  }
  const chunkTokens = new Set(normalizeTokens(chunkText));
  let matches = 0;
  queryTokens.forEach((token) => {
    if (chunkTokens.has(token)) {
      matches += 1;
    }
  });
  return matches / queryTokens.size;
}

function sectionBoost(query: string, label: string, section?: string | null): number {
  const haystack = `${label} ${section ?? ''}`.toLowerCase();
  const tokens = normalizeTokens(query);
  if (!tokens.length) {
    return 0;
  }
  let hits = 0;
  for (const token of tokens) {
    if (haystack.includes(token)) {
      hits += 1;
    }
  }
  return Math.min(0.18, (hits / tokens.length) * 0.18);
}

async function loadFallbackChunks(versionId: string) {
  const chunks = await getCourseChunksByVersion(versionId);
  return chunks.map((chunk: any) => ({
    id: chunk.id,
    material_id: chunk.material_id,
    chunk_index: chunk.chunk_index,
    label: chunk.label,
    section: chunk.section ?? null,
    text: chunk.text,
    token_estimate: chunk.token_estimate,
    embedding: JSON.parse(chunk.embedding_json) as number[],
  }));
}

export async function retrieveIndexedCourseChunks(params: {
  client: OpenAI;
  apiKeyHash: string;
  query: string;
  courseId?: string | null;
  courseVersionId?: string | null;
  topK?: number;
}) {
  const version = await resolveCourseIndexVersion({
    courseId: params.courseId ?? null,
    versionId: params.courseVersionId ?? null,
    apiKeyHash: params.apiKeyHash,
  });

  if (!version || version.status !== 'ready') {
    return null;
  }

  const queryEmbeddingResponse = await params.client.embeddings.create({
    model: EMBEDDING_MODEL,
    input: params.query,
  });
  const queryVector = queryEmbeddingResponse.data[0]?.embedding;
  if (!queryVector) {
    return {
      version,
      chunks: [],
      diagnostics: {
        retrieval_query: params.query,
        top_k: params.topK ?? 6,
        scores: [],
        fallback_used: false,
      } satisfies RetrievalDiagnostics,
      embeddingTokens: queryEmbeddingResponse.usage?.total_tokens ?? 0,
    };
  }

  const candidateLimit = params.topK ?? DEFAULT_VECTOR_LIMIT;
  const vectorCandidates = await searchCourseChunkCandidates({
    versionId: String(version.id),
    queryEmbedding: queryVector,
    limit: candidateLimit,
  });

  const fallbackUsed = !vectorCandidates;

  const rankedSource = vectorCandidates
    ? vectorCandidates.map((chunk: any) => ({
        ...chunk,
        score: chunk.vector_score * 0.72 + keywordScore(params.query, chunk.text) * 0.18 + sectionBoost(params.query, chunk.label, chunk.section),
      }))
    : (await loadFallbackChunks(String(version.id))).map((chunk: any) => ({
        ...chunk,
        score:
          cosineSimilarity(queryVector, chunk.embedding) * 0.72 +
          keywordScore(params.query, chunk.text) * 0.18 +
          sectionBoost(params.query, chunk.label, chunk.section),
      }));

  if (!rankedSource.length) {
    return {
      version,
      chunks: [],
      diagnostics: {
        retrieval_query: params.query,
        top_k: params.topK ?? DEFAULT_VECTOR_LIMIT,
        scores: [],
        fallback_used: fallbackUsed,
      } satisfies RetrievalDiagnostics,
      embeddingTokens: queryEmbeddingResponse.usage?.total_tokens ?? 0,
    };
  }

  const ranked = rankedSource
    .sort((a: any, b: any) => b.score - a.score)
    .slice(0, params.topK ?? DEFAULT_VECTOR_LIMIT)
    .map((chunk: any, index: number) => ({
      ...chunk,
      rank: index + 1,
    }));

  const sanitizedRanked = sanitizeRetrievedChunks(ranked).chunks.map((chunk: any, index: number) => ({
    ...chunk,
    rank: index + 1,
  }));

  return {
    version,
    chunks: sanitizedRanked.map((chunk: any) => ({
      id: chunk.chunk_index + 1,
      label: chunk.label,
      text: chunk.text,
      score: chunk.score,
      rank: chunk.rank,
    })),
    diagnostics: {
      retrieval_query: params.query,
      top_k: params.topK ?? DEFAULT_VECTOR_LIMIT,
      fallback_used: fallbackUsed,
      scores: sanitizedRanked.map((chunk: any) => ({
        chunk_id: chunk.chunk_index + 1,
        label: chunk.label,
        score: Number(chunk.score.toFixed(4)),
        rank: chunk.rank,
      })),
    } satisfies RetrievalDiagnostics,
    embeddingTokens: queryEmbeddingResponse.usage?.total_tokens ?? 0,
  };
}
