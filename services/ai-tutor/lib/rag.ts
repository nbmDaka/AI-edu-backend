import OpenAI from 'openai';
import { sanitizeRetrievedChunks } from '@/lib/ai/guardrails';
import type { RetrievalDiagnostics } from '@/lib/ai/types';

async function getLectureContent() {
  return {
    paragraphs: []
  };
}

export type LectureChunk = {
  id: number;
  label: string;
  text: string;
};

export type RankedChunk = LectureChunk & {
  score: number;
  rank: number;
};

const EMBEDDING_MODEL = process.env.EMBEDDING_MODEL ?? 'text-embedding-3-small';
const MIN_SCORE_THRESHOLD = 0.08;
const DEFAULT_TOP_K = 6;

let cachedChunks: LectureChunk[] | null = null;
let cachedChunkEmbeddings:
  | {
      model: string;
      vectors: number[][];
      usageTokens: number;
    }
  | null = null;

export async function buildLectureChunks(maxChunkChars = 520): Promise<LectureChunk[]> {
  if (cachedChunks) {
    return cachedChunks;
  }

  const lecture = await getLectureContent();
  const chunks: LectureChunk[] = [];
  let buffer = '';
  let chunkId = 1;

  for (const paragraph of lecture.paragraphs) {
    const candidate = buffer ? `${buffer}\n\n${paragraph}` : paragraph;

    if (candidate.length > maxChunkChars && buffer) {
      chunks.push({
        id: chunkId,
        label: `Фрагмент ${chunkId}`,
        text: buffer,
      });
      chunkId += 1;
      buffer = paragraph;
      continue;
    }

    buffer = candidate;
  }

  if (buffer) {
    chunks.push({
      id: chunkId,
      label: `Фрагмент ${chunkId}`,
      text: buffer,
    });
  }

  cachedChunks = chunks;
  return chunks;
}

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
  if (!denominator) {
    return 0;
  }

  return dotProduct / denominator;
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
  if (queryTokens.size === 0) {
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

async function getChunkEmbeddings(
  client: OpenAI,
): Promise<{ vectors: number[][]; usageTokens: number; cacheHit: boolean }> {
  const chunks = await buildLectureChunks();

  if (chunks.length === 0) {
    return {
      vectors: [],
      usageTokens: 0,
      cacheHit: false,
    };
  }

  if (cachedChunkEmbeddings?.model === EMBEDDING_MODEL) {
    return {
      vectors: cachedChunkEmbeddings.vectors,
      usageTokens: 0,
      cacheHit: true,
    };
  }

  const response = await client.embeddings.create({
    model: EMBEDDING_MODEL,
    input: chunks.map((chunk) => chunk.text),
  });

  const vectors = response.data.map((item) => item.embedding);
  cachedChunkEmbeddings = {
    model: EMBEDDING_MODEL,
    vectors,
    usageTokens: response.usage?.total_tokens ?? 0,
  };

  return {
    vectors,
    usageTokens: response.usage?.total_tokens ?? 0,
    cacheHit: false,
  };
}

export async function buildCustomChunks(text: string, maxChunkChars = 520): Promise<LectureChunk[]> {
  const paragraphs = text
    .split('\n')
    .map(p => p.trim())
    .filter(Boolean);
  const chunks: LectureChunk[] = [];
  let buffer = '';
  let chunkId = 1;

  for (const paragraph of paragraphs) {
    const candidate = buffer ? `${buffer}\n\n${paragraph}` : paragraph;

    if (candidate.length > maxChunkChars && buffer) {
      chunks.push({
        id: chunkId,
        label: `Фрагмент ${chunkId}`,
        text: buffer,
      });
      chunkId += 1;
      buffer = paragraph;
      continue;
    }

    buffer = candidate;
  }

  if (buffer) {
    chunks.push({
      id: chunkId,
      label: `Фрагмент ${chunkId}`,
      text: buffer,
    });
  }

  return chunks;
}

async function getCustomChunkEmbeddings(
  client: OpenAI,
  chunks: LectureChunk[],
): Promise<{ vectors: number[][]; usageTokens: number; cacheHit: boolean }> {
  if (chunks.length === 0) {
    return { vectors: [], usageTokens: 0, cacheHit: false };
  }

  const response = await client.embeddings.create({
    model: EMBEDDING_MODEL,
    input: chunks.map((chunk) => chunk.text),
  });

  const vectors = response.data.map((item) => item.embedding);

  return {
    vectors,
    usageTokens: response.usage?.total_tokens ?? 0,
    cacheHit: false,
  };
}

export async function retrieveRelevantChunksDetailed(
  client: OpenAI,
  userQuery: string,
  topK = DEFAULT_TOP_K,
  customContent?: string,
): Promise<{
  chunks: RankedChunk[];
  diagnostics: RetrievalDiagnostics;
  embeddingTokens: number;
}> {
  const chunks = customContent
    ? await buildCustomChunks(customContent)
    : await buildLectureChunks();

  if (chunks.length === 0) {
    return {
      chunks: [],
      diagnostics: {
        retrieval_query: userQuery,
        top_k: topK,
        scores: [],
        fallback_used: false,
      },
      embeddingTokens: 0,
    };
  }

  const chunkEmbeddings = customContent
    ? await getCustomChunkEmbeddings(client, chunks)
    : await getChunkEmbeddings(client);

  const queryEmbeddingResponse = await client.embeddings.create({
    model: EMBEDDING_MODEL,
    input: userQuery,
  });

  const queryVector = queryEmbeddingResponse.data[0]?.embedding;
  if (!queryVector) {
    return {
      chunks: [],
      diagnostics: {
        retrieval_query: userQuery,
        top_k: topK,
        scores: [],
        fallback_used: false,
      },
      embeddingTokens: (queryEmbeddingResponse.usage?.total_tokens ?? 0) + chunkEmbeddings.usageTokens,
    };
  }

  const ranked = chunks
    .map((chunk, index) => ({
      ...chunk,
      score:
        cosineSimilarity(queryVector, chunkEmbeddings.vectors[index]) * 0.82 +
        keywordScore(userQuery, chunk.text) * 0.18,
    }))
    .sort((a, b) => b.score - a.score)
    .slice(0, topK)
    .map((chunk, index) => ({
      ...chunk,
      rank: index + 1,
    }));

  const sanitizedRanked = sanitizeRetrievedChunks(ranked).chunks.map((chunk, index) => ({
    ...chunk,
    rank: index + 1,
  }));

  if (!sanitizedRanked.length) {
    return {
      chunks: [],
      diagnostics: {
        retrieval_query: userQuery,
        top_k: topK,
        scores: [],
        fallback_used: false,
      },
      embeddingTokens: (queryEmbeddingResponse.usage?.total_tokens ?? 0) + chunkEmbeddings.usageTokens,
    };
  }

  if ((sanitizedRanked[0]?.score ?? 0) >= MIN_SCORE_THRESHOLD) {
    return {
      chunks: sanitizedRanked,
      diagnostics: {
        retrieval_query: userQuery,
        top_k: topK,
        fallback_used: false,
        scores: sanitizedRanked.map((chunk) => ({
          chunk_id: chunk.id,
          label: chunk.label,
          score: Number(chunk.score.toFixed(4)),
          rank: chunk.rank,
        })),
      },
      embeddingTokens: (queryEmbeddingResponse.usage?.total_tokens ?? 0) + chunkEmbeddings.usageTokens,
    };
  }

  // Fallback: keyword overlap helps with short title-like queries.
  const keywordRanked = chunks
    .map((chunk) => ({
      ...chunk,
      score: keywordScore(userQuery, chunk.text),
    }))
    .sort((a, b) => b.score - a.score)
    .slice(0, topK)
    .map((chunk, index) => ({
      ...chunk,
      rank: index + 1,
    }));

  const sanitizedKeywordRanked = sanitizeRetrievedChunks(keywordRanked).chunks.map((chunk, index) => ({
    ...chunk,
    rank: index + 1,
  }));

  if (!sanitizedKeywordRanked.length || (sanitizedKeywordRanked[0]?.score ?? 0) < 0.2) {
    return {
      chunks: [],
      diagnostics: {
        retrieval_query: userQuery,
        top_k: topK,
        fallback_used: true,
        scores: sanitizedKeywordRanked.map((chunk) => ({
          chunk_id: chunk.id,
          label: chunk.label,
          score: Number(chunk.score.toFixed(4)),
          rank: chunk.rank,
        })),
      },
      embeddingTokens: (queryEmbeddingResponse.usage?.total_tokens ?? 0) + chunkEmbeddings.usageTokens,
    };
  }

  return {
    chunks: sanitizedKeywordRanked,
    diagnostics: {
      retrieval_query: userQuery,
      top_k: topK,
      fallback_used: true,
      scores: sanitizedKeywordRanked.map((chunk) => ({
        chunk_id: chunk.id,
        label: chunk.label,
        score: Number(chunk.score.toFixed(4)),
        rank: chunk.rank,
      })),
    },
    embeddingTokens: (queryEmbeddingResponse.usage?.total_tokens ?? 0) + chunkEmbeddings.usageTokens,
  };
}

export async function retrieveRelevantChunks(
  client: OpenAI,
  userQuery: string,
  topK = DEFAULT_TOP_K,
  customContent?: string,
): Promise<RankedChunk[]> {
  const result = await retrieveRelevantChunksDetailed(client, userQuery, topK, customContent);
  return result.chunks;
}

export function formatChunksForPrompt(chunks: RankedChunk[]): string {
  return chunks
    .map((chunk) => `[${chunk.label}]\n${chunk.text}`)
    .join('\n\n');
}
