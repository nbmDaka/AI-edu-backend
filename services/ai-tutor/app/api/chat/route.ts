import '@/lib/ai/env-override';
export const dynamic = 'force-dynamic';
import { randomUUID } from 'node:crypto';
import OpenAI from 'openai';
import {
  buildResponsesInput,
  buildSourceItems,
  buildSourcesPreview,
  defaultIdentityMessage,
  isComparisonQuery,
  pickLocateSnippet,
  sanitizeAssistantText,
} from '@/lib/ai/chat-pipeline';
import { initAiDatabase, insertMessage, maybeRunDbMaintenance, upsertSession, createRequestEvent, finalizeRequestEvent } from '@/lib/ai/db';
import { evaluateAssistantOutput, evaluateGuardrails } from '@/lib/ai/guardrails';
import { detectSupportedLanguage, isIdentityQuestion, noAnswerText } from '@/lib/ai/language';
import { consumeQuota, estimateUsageCost, checkQuotaBudget, reserveQuotaRequest } from '@/lib/ai/quotas';
import { estimateTokens, hashApiKey, jsonError, readApiKeyFromHeaders } from '@/lib/ai/request';
import { retrieveRelevantChunksDetailed } from '@/lib/rag';
import { retrieveIndexedCourseChunks } from '@/lib/ai/index-search';
import type { InputMessage, SupportedLanguage, UsageSummary } from '@/lib/ai/types';
import { parseLearningContext } from '@/lib/ai/learning-context';

export const runtime = 'nodejs';

const CHAT_MODEL = process.env.OPENAI_MODEL ?? 'gpt-4o-mini';
const MAX_HISTORY_MESSAGES = Number(process.env.AI_MAX_HISTORY_MESSAGES ?? 12);
const configuredChatTimeout = Number(process.env.AI_CHAT_TIMEOUT_MS ?? 120000);
const CHAT_TIMEOUT_MS = Number.isFinite(configuredChatTimeout) && configuredChatTimeout > 0
  ? configuredChatTimeout
  : 120000;
const PROVIDER_TIMEOUT_MS = Number(process.env.AI_PROVIDER_TIMEOUT_MS ?? 60000);
const PROVIDER_MAX_RETRIES = Number(process.env.AI_PROVIDER_MAX_RETRIES ?? 2);

if (!Number.isInteger(PROVIDER_TIMEOUT_MS) || PROVIDER_TIMEOUT_MS < 1 || PROVIDER_TIMEOUT_MS > 600000) {
  throw new Error('AI_PROVIDER_TIMEOUT_MS must be an integer between 1 and 600000');
}
if (!Number.isInteger(PROVIDER_MAX_RETRIES) || PROVIDER_MAX_RETRIES < 0 || PROVIDER_MAX_RETRIES > 5) {
  throw new Error('AI_PROVIDER_MAX_RETRIES must be an integer between 0 and 5');
}

const client = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY,
  timeout: PROVIDER_TIMEOUT_MS,
  maxRetries: PROVIDER_MAX_RETRIES,
});


const encoder = new TextEncoder();

type ChatRequestBody = {
  learning_context?: unknown;
  session_id?: string;
  sessionId?: string;
  message_id?: string;
  client_metadata?: Record<string, unknown>;
  messages?: InputMessage[];
  message?: string;
  locateInLecture?: boolean;
  course_id?: string;
  course_version_id?: string;
  lesson_id?: string;
  lesson_title?: string;
  lesson_content?: string;
};

function validateMessages(messages: InputMessage[] | undefined) {
  if (!Array.isArray(messages) || messages.length === 0) {
    return {
      ok: false as const,
      response: jsonError('Передайте непустой массив messages.', 400),
    };
  }

  const sanitizedMessages = messages
    .filter((msg) => msg && (msg.role === 'user' || msg.role === 'assistant'))
    .map((msg) => ({
      role: msg.role,
      content: String(msg.content ?? '').trim(),
    }))
    .filter((msg) => msg.content.length > 0)
    .slice(-MAX_HISTORY_MESSAGES);

  if (!sanitizedMessages.length) {
    return {
      ok: false as const,
      response: jsonError('Не найдено валидных сообщений.', 400),
    };
  }

  return {
    ok: true as const,
    sanitizedMessages,
  };
}

function makeNoAnswerUsage(embeddingTokens: number): UsageSummary {
  return estimateUsageCost({
    prompt_tokens: 0,
    completion_tokens: 0,
    embedding_tokens: embeddingTokens,
    total_tokens: embeddingTokens,
  });
}

function sendSseEvent(
  controller: ReadableStreamDefaultController<Uint8Array>,
  event: string,
  data: Record<string, unknown>,
) {
  controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
}

export async function POST(req: Request) {
  try {
    await initAiDatabase();
    await maybeRunDbMaintenance();
  } catch (error) {
    console.error('AI database init error:', error);
    return jsonError('Хранилище AI-модуля недоступно.', 503);
  }

  if (!process.env.OPENAI_API_KEY) {
    return jsonError('OPENAI_API_KEY не настроен на сервере.', 500);
  }

  let body: ChatRequestBody;
  try {
    body = (await req.json()) as ChatRequestBody;
  } catch {
    return jsonError('Тело запроса должно быть корректным JSON.', 400);
  }

  const sessionId = String(body.session_id ?? body.sessionId ?? '').trim();
  if (!sessionId) {
    return jsonError('Поле session_id обязательно.', 400);
  }

  const inputMessages =
    Array.isArray(body.messages) || typeof body.message !== 'string'
      ? body.messages
      : [{ role: 'user' as const, content: body.message }];
  const validated = validateMessages(inputMessages);
  if (!validated.ok) {
    return validated.response;
  }

  const sanitizedMessages = validated.sanitizedMessages;
  const latestUserMessage = [...sanitizedMessages].reverse().find((msg) => msg.role === 'user');
  if (!latestUserMessage) {
    return jsonError('Последнее сообщение пользователя не найдено.', 400);
  }

  const answerLanguage = detectSupportedLanguage(latestUserMessage.content);
  const apiKeyHash = hashApiKey(readApiKeyFromHeaders(req.headers));
  const requestEventId = randomUUID();
  const userMessageId = String(body.message_id ?? '').trim() || randomUUID();
  const locateInLecture = Boolean(body.locateInLecture);
  const courseId = String(body.course_id ?? '').trim() || null;
  let learningContext: ReturnType<typeof parseLearningContext>;
  try {
    if (body.learning_context !== undefined && (!process.env.API_SHARED_KEY ||
        readApiKeyFromHeaders(req.headers) !== process.env.API_SHARED_KEY.trim() || req.headers.has('origin'))) {
      return jsonError('Learning context requires a trusted server request.', 403);
    }
    learningContext = parseLearningContext(body.learning_context, courseId);
  } catch {
    return jsonError('Invalid learning_context.', 400);
  }
  const courseVersionId = String(body.course_version_id ?? '').trim() || null;
  const lessonId = String(body.lesson_id ?? '').trim() || null;
  const lessonTitle = String(body.lesson_title ?? '').trim() || null;
  const priorUserMessages = sanitizedMessages
    .filter((msg) => msg.role === 'user')
    .slice(0, -1)
    .map((msg) => msg.content);
  const historyChainGuardrail =
    priorUserMessages.some((message) =>
      /игнориру(й|йте)|раскрой\s+(системн[а-яёәіңғүұқөһ]*\s+)?(промпт|инструкц)|ignore\s+previous\s+instructions|reveal\s+(the\s+)?system\s+prompt/i.test(
        message,
      ),
    ) &&
    /(предыдущ[а-яёәіңғүұқөһ]*|прошл[а-яёәіңғүұқөһ]*|раньше|above|earlier|previous|that task|ту\s+задач[а-яёәіңғүұқөһ]*)/i.test(
      latestUserMessage.content,
    );
  const evaluatedGuardrails = evaluateGuardrails(latestUserMessage.content, {
    history: priorUserMessages,
  });
  const guardrails = historyChainGuardrail
    ? {
        blocked: true,
        reasons: ['history_prompt_injection_chain'],
        category: 'prompt_injection' as const,
        code: 'prompt_injection' as const,
        message: 'Запрос отклонен: обнаружена попытка продолжить ранее заданную инъекционную инструкцию.',
        injection_score: 0.95,
        blocked_stage: 'moderation' as const,
      }
    : evaluatedGuardrails;

  try {
    await upsertSession(sessionId, apiKeyHash, latestUserMessage.content.slice(0, 80));
    await createRequestEvent({
      id: requestEventId,
      sessionId,
      apiKeyHash,
      route: '/api/chat',
      metadata: {
        client_metadata: body.client_metadata ?? {},
        locateInLecture,
        model: CHAT_MODEL,
        course_id: courseId,
        course_version_id: courseVersionId,
        lesson_id: lessonId,
        lesson_title: lessonTitle,
      },
    });

    await insertMessage({
      id: userMessageId,
      sessionId,
      role: 'user',
      content: latestUserMessage.content,
      language: answerLanguage,
    });
  } catch (error) {
    const message =
      error instanceof Error && error.message === 'session ownership mismatch'
        ? 'Сессия принадлежит другому API-клиенту.'
        : 'Не удалось сохранить состояние диалога.';
    return jsonError(message, error instanceof Error && error.message === 'session ownership mismatch' ? 403 : 503);
  }

  if (guardrails.blocked) {
    await finalizeRequestEvent({
      id: requestEventId,
      status: guardrails.category === 'validation' ? 400 : 403,
      guardrailBlocked: true,
      guardrailReasons: guardrails.reasons,
      injectionScore: guardrails.injection_score,
      blockedStage: guardrails.blocked_stage,
      prompt: JSON.stringify(body),
      response: guardrails.message,
      usage: makeNoAnswerUsage(0),
    });

    return jsonError(guardrails.message, guardrails.category === 'validation' ? 400 : 403, {
      code: guardrails.code,
      category: guardrails.category,
      guardrails: {
        blocked: true,
        reasons: guardrails.reasons,
      },
    });
  }

  const earlyQuotaCheck = await checkQuotaBudget(apiKeyHash, estimateTokens(latestUserMessage.content));
  if (!earlyQuotaCheck.allowed) {
    await finalizeRequestEvent({
      id: requestEventId,
      status: earlyQuotaCheck.status,
      guardrailBlocked: false,
      guardrailReasons: [earlyQuotaCheck.code],
      injectionScore: 0,
      blockedStage: 'validation',
      prompt: JSON.stringify(body),
      response: earlyQuotaCheck.message,
      usage: makeNoAnswerUsage(0),
    });

    return jsonError(earlyQuotaCheck.message, earlyQuotaCheck.status, { code: earlyQuotaCheck.code });
  }

  await reserveQuotaRequest(apiKeyHash, requestEventId, estimateTokens(latestUserMessage.content));

  const upstreamAbort = new AbortController();
  let clientCancelled = false;
  let timedOut = false;
  let streamClosed = false;
  let finalization: Promise<void> | null = null;
  const cancelForClient = () => {
    clientCancelled = true;
    upstreamAbort.abort();
  };
  req.signal.addEventListener('abort', cancelForClient, { once: true });
  if (req.signal.aborted) cancelForClient();
  const timeout = setTimeout(() => {
    timedOut = true;
    upstreamAbort.abort();
  }, CHAT_TIMEOUT_MS);
  const cleanup = () => {
    clearTimeout(timeout);
    req.signal.removeEventListener('abort', cancelForClient);
  };
  const finalizeOnce = (params: Parameters<typeof finalizeRequestEvent>[0]) => {
    if (!finalization) finalization = finalizeRequestEvent(params);
    return finalization;
  };
  const finalizeFailure = (status: number, response: string) => finalizeOnce({
    id: requestEventId,
    status,
    guardrailBlocked: false,
    guardrailReasons: [],
    injectionScore: 0,
    blockedStage: null,
    retrievalQuery: retrieval?.diagnostics.retrieval_query,
    retrievedChunkIds: retrieval?.chunks.map((chunk) => chunk.id),
    retrievalScores: retrieval?.diagnostics.scores,
    retrievalFallbackUsed: retrieval?.diagnostics.fallback_used,
    prompt: JSON.stringify(body),
    response,
    usage: makeNoAnswerUsage(retrieval?.embeddingTokens ?? 0),
  });

  let retrieval: Awaited<ReturnType<typeof retrieveRelevantChunksDetailed>> | NonNullable<Awaited<ReturnType<typeof retrieveIndexedCourseChunks>>>;
  let sources: ReturnType<typeof buildSourceItems>;
  try {
    const userMessagesOnly = sanitizedMessages.filter((msg) => msg.role === 'user');
    const previousUser =
      userMessagesOnly.length > 1 ? userMessagesOnly[userMessagesOnly.length - 2]?.content : '';
    const retrievalQuery = previousUser
      ? `${previousUser}\n${latestUserMessage.content}`
      : latestUserMessage.content;

    const lessonContent = typeof body.lesson_content === 'string' ? body.lesson_content.trim() : null;

    retrieval = lessonContent
      ? await retrieveRelevantChunksDetailed(client, retrievalQuery, 6, lessonContent, upstreamAbort.signal)
      : (await retrieveIndexedCourseChunks({
          client,
          apiKeyHash,
          query: retrievalQuery,
          courseId,
          courseVersionId,
          topK: 6,
          signal: upstreamAbort.signal,
        })) ?? emptyRetrieval(retrievalQuery);
    sources = buildSourceItems(
      retrieval.chunks.map((chunk) => ({
        chunk_id: chunk.id,
        label: chunk.label,
        text: chunk.text,
        score: chunk.score ?? 0,
      })),
    );

    const estimatedPromptTokens = estimateTokens(
      JSON.stringify({
        messages: sanitizedMessages,
        query: retrievalQuery,
        chunks: retrieval.chunks.map((chunk) => chunk.text),
        learning_context: learningContext,
      }),
    );

    const quotaCheck = await checkQuotaBudget(apiKeyHash, estimatedPromptTokens + retrieval.embeddingTokens);
    if (!quotaCheck.allowed) {
      await finalizeOnce({
        id: requestEventId,
        status: quotaCheck.status,
        guardrailBlocked: false,
        guardrailReasons: [quotaCheck.code],
        injectionScore: 0,
        blockedStage: 'validation',
        retrievalQuery: retrieval.diagnostics.retrieval_query,
        retrievedChunkIds: retrieval.chunks.map((chunk) => chunk.id),
        retrievalScores: retrieval.diagnostics.scores,
        retrievalFallbackUsed: retrieval.diagnostics.fallback_used,
        prompt: JSON.stringify(body),
        response: quotaCheck.message,
        usage: makeNoAnswerUsage(retrieval.embeddingTokens),
      });

      cleanup();
      return jsonError(quotaCheck.message, quotaCheck.status, { code: quotaCheck.code });
    }
  } catch (error) {
    const status = clientCancelled ? 499 : timedOut ? 504 : 502;
    const code = clientCancelled ? 'client_cancelled' : timedOut ? 'chat_timeout' : 'retrieval_failed';
    if (!clientCancelled && !timedOut) console.error('Chat retrieval error:', error);
    await finalizeFailure(status, code);
    cleanup();
    return jsonError(timedOut ? 'Превышено время ожидания ответа.' : 'Не удалось выполнить retrieval для текущего запроса.', status, { code });
  }

  const stream = new ReadableStream({
    async start(controller) {
      const guardrailInfo = {
        blocked: false,
        reasons: [] as string[],
      };

      const emit = (event: string, data: Record<string, unknown>) => {
        if (clientCancelled || streamClosed) throw new Error('client_cancelled');
        try {
          sendSseEvent(controller, event, data);
        } catch (error) {
          cancelForClient();
          throw error;
        }
      };
      try {
        upstreamAbort.signal.throwIfAborted();
        if (isIdentityQuestion(latestUserMessage.content)) {
          const text = defaultIdentityMessage(answerLanguage);
          const usage = makeNoAnswerUsage(retrieval.embeddingTokens);
          const assistantMessageId = `${requestEventId}:assistant`;
          await insertMessage({
            id: assistantMessageId,
            sessionId,
            role: 'assistant',
            content: text,
            language: answerLanguage,
          });
          await consumeQuota(apiKeyHash, usage);
          emit('chunk', { text });
          emit('done', {
            sources: [],
            citations: [],
            locateSnippet: null,
            usage,
            guardrails: guardrailInfo,
          });
          await finalizeOnce({
            id: requestEventId,
            status: 200,
            guardrailBlocked: false,
            guardrailReasons: [],
            injectionScore: 0,
            blockedStage: null,
            retrievalQuery: retrieval.diagnostics.retrieval_query,
            retrievedChunkIds: [],
            retrievalScores: [],
            retrievalFallbackUsed: retrieval.diagnostics.fallback_used,
            prompt: JSON.stringify(body),
            response: text,
            usage,
          });
          return;
        }

        emit('meta', {
          retrieved_count: retrieval.chunks.length,
          sources_preview: buildSourcesPreview(
            retrieval.chunks.map((chunk) => ({
              chunk_id: chunk.id,
              label: chunk.label,
              text: chunk.text,
              score: chunk.score ?? 0,
            })),
          ),
        });

        if (!hasRelevantChunks(retrieval.chunks.length)) {
          const text = noAnswerText(answerLanguage);
          const usage = makeNoAnswerUsage(retrieval.embeddingTokens);
          const assistantMessageId = `${requestEventId}:assistant`;
          await insertMessage({
            id: assistantMessageId,
            sessionId,
            role: 'assistant',
            content: text,
            language: answerLanguage,
          });
          await consumeQuota(apiKeyHash, usage);
          emit('chunk', { text });
          emit('done', {
            sources: [],
            citations: [],
            locateSnippet: null,
            usage,
            guardrails: guardrailInfo,
          });
          await finalizeOnce({
            id: requestEventId,
            status: 200,
            guardrailBlocked: false,
            guardrailReasons: [],
            injectionScore: 0,
            blockedStage: null,
            retrievalQuery: retrieval.diagnostics.retrieval_query,
            retrievedChunkIds: [],
            retrievalScores: retrieval.diagnostics.scores,
            retrievalFallbackUsed: retrieval.diagnostics.fallback_used,
            prompt: JSON.stringify(body),
            response: text,
            usage,
          });
          return;
        }

        const prompt = buildResponsesInput({
          learningContext,
          query: latestUserMessage.content,
          history: sanitizedMessages,
          chunks: retrieval.chunks.map((chunk) => ({ label: chunk.label, text: chunk.text })),
          language: answerLanguage,
        });

        const responseStream = await client.responses.create({
          model: CHAT_MODEL,
          instructions: prompt.instructions,
          input: prompt.input,
          stream: true,
          store: false,
        }, { signal: AbortSignal.any([upstreamAbort.signal, AbortSignal.timeout(PROVIDER_TIMEOUT_MS)]) });

        let completed = false;
        let fullAnswer = '';
        let usageChunk: {
          input_tokens?: number;
          output_tokens?: number;
          total_tokens?: number;
        } | null = null;

        for await (const event of responseStream) {
          upstreamAbort.signal.throwIfAborted();
          if (event.type === 'response.output_text.delta') {
            const token = (event as { type: string; delta: string }).delta;
            if (token) {
              fullAnswer += token;
              emit('chunk', { text: token });
            }
          } else if (event.type === 'response.completed') {
            completed = true;
            const completedEvent = event as { type: string; response: { usage?: { input_tokens?: number; output_tokens?: number; total_tokens?: number } } };
            usageChunk = completedEvent.response?.usage ?? null;
          } else if (event.type === 'response.failed' || event.type === 'response.incomplete') {
            throw new Error('incomplete_provider_stream');
          }
        }
        upstreamAbort.signal.throwIfAborted();
        if (!completed) throw new Error('incomplete_provider_stream');

        const cleanedAnswer = sanitizeAssistantText(fullAnswer);
        const outputGuardrail = evaluateAssistantOutput(cleanedAnswer || fullAnswer);
        const finalAnswer = outputGuardrail.blocked
          ? outputGuardrail.safeText
          : cleanedAnswer || fullAnswer || noAnswerText(answerLanguage);
        if (outputGuardrail.blocked) {
          guardrailInfo.blocked = true;
          guardrailInfo.reasons = outputGuardrail.reasons;
        }
        const hasNoAnswerPhrase =
          /в материалах лекции этого нет|дәріс материалдарында.*жоқ|not covered in the lecture materials/i.test(
            finalAnswer,
          );
        const locateQuery = `${latestUserMessage.content}\n${cleanedAnswer}`;
        const locateSnippet =
          locateInLecture && !hasNoAnswerPhrase && !outputGuardrail.blocked
            ? pickLocateSnippet(
                locateQuery,
                retrieval.chunks.map((chunk) => ({ text: chunk.text })),
                { preferTable: isComparisonQuery(latestUserMessage.content) },
              )
            : null;

        const usage = estimateUsageCost({
          prompt_tokens: usageChunk?.input_tokens ?? estimateTokens(prompt.instructions + JSON.stringify(prompt.input)),
          completion_tokens: usageChunk?.output_tokens ?? estimateTokens(finalAnswer),
          embedding_tokens: retrieval.embeddingTokens,
          total_tokens:
            usageChunk?.total_tokens ??
            (usageChunk?.input_tokens ?? estimateTokens(prompt.instructions + JSON.stringify(prompt.input))) +
              (usageChunk?.output_tokens ?? estimateTokens(finalAnswer)) +
              retrieval.embeddingTokens,
        });

        const assistantMessageId = `${requestEventId}:assistant`;
        await insertMessage({
          id: assistantMessageId,
          sessionId,
          role: 'assistant',
          content: finalAnswer,
          language: answerLanguage,
        });
        await consumeQuota(apiKeyHash, usage);

        emit('done', {
          sources: hasNoAnswerPhrase || outputGuardrail.blocked ? [] : sources,
          citations: hasNoAnswerPhrase || outputGuardrail.blocked ? [] : retrieval.chunks.map((chunk) => chunk.id),
          locateSnippet,
          usage,
          guardrails: guardrailInfo,
        });

        await finalizeOnce({
          id: requestEventId,
          status: 200,
          guardrailBlocked: outputGuardrail.blocked,
          guardrailReasons: outputGuardrail.reasons,
          injectionScore: outputGuardrail.score,
          blockedStage: outputGuardrail.blocked ? 'generation' : null,
          retrievalQuery: retrieval.diagnostics.retrieval_query,
          retrievedChunkIds: retrieval.chunks.map((chunk) => chunk.id),
          retrievalScores: retrieval.diagnostics.scores,
          retrievalFallbackUsed: retrieval.diagnostics.fallback_used,
          prompt: JSON.stringify({ instructions: prompt.instructions, input: prompt.input }),
          response: finalAnswer,
          usage,
        });

      } catch (error) {
        const status = clientCancelled ? 499 : timedOut ? 504 : 500;
        const code = timedOut ? 'chat_timeout' : 'chat_stream_failed';
        const errorMessage = timedOut
          ? 'Превышено время ожидания ответа. Повторите запрос.'
          : 'Ошибка при обращении к модели. Проверьте ключ, лимиты API и повторите запрос.';
        if (!clientCancelled && !timedOut) console.error('Chat stream error:', error);
        await finalizeFailure(status, clientCancelled ? 'client_cancelled' : error instanceof Error ? error.message : errorMessage);
        if (!clientCancelled && !streamClosed) {
          try { emit('error', { code, message: errorMessage, retryable: true }); }
          catch { /* A downstream disconnect must not trigger another write. */ }
        }
      } finally {
        cleanup();
        if (!clientCancelled && !streamClosed) {
          try { controller.close(); } catch { /* The downstream may already be closed. */ }
        }
        streamClosed = true;
      }
    },
    async cancel() {
      streamClosed = true;
      cancelForClient();
      try { await finalizeFailure(499, 'client_cancelled'); }
      finally { cleanup(); }
    },
  });

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
    },
  });
}

function hasRelevantChunks(count: number) {
  return count > 0;
}

function emptyRetrieval(query: string): Awaited<ReturnType<typeof retrieveRelevantChunksDetailed>> {
  return {
    chunks: [],
    diagnostics: {
      retrieval_query: query,
      top_k: 0,
      scores: [],
      fallback_used: false,
    },
    embeddingTokens: 0,
  };
}
