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

export const runtime = 'nodejs';

const CHAT_MODEL = process.env.OPENAI_MODEL ?? 'gpt-4o-mini';
const MAX_HISTORY_MESSAGES = Number(process.env.AI_MAX_HISTORY_MESSAGES ?? 12);

const client = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY,
});

const encoder = new TextEncoder();

type ChatRequestBody = {
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
      response: jsonError('РџРµСЂРµРґР°Р№С‚Рµ РЅРµРїСѓСЃС‚РѕР№ РјР°СЃСЃРёРІ messages.', 400),
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
      response: jsonError('РќРµ РЅР°Р№РґРµРЅРѕ РІР°Р»РёРґРЅС‹С… СЃРѕРѕР±С‰РµРЅРёР№.', 400),
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
    return jsonError('РҐСЂР°РЅРёР»РёС‰Рµ AI-РјРѕРґСѓР»СЏ РЅРµРґРѕСЃС‚СѓРїРЅРѕ.', 503);
  }

  if (!process.env.OPENAI_API_KEY) {
    return jsonError('OPENAI_API_KEY РЅРµ РЅР°СЃС‚СЂРѕРµРЅ РЅР° СЃРµСЂРІРµСЂРµ.', 500);
  }

  let body: ChatRequestBody;
  try {
    body = (await req.json()) as ChatRequestBody;
  } catch {
    return jsonError('РўРµР»Рѕ Р·Р°РїСЂРѕСЃР° РґРѕР»Р¶РЅРѕ Р±С‹С‚СЊ РєРѕСЂСЂРµРєС‚РЅС‹Рј JSON.', 400);
  }

  const sessionId = String(body.session_id ?? body.sessionId ?? '').trim();
  if (!sessionId) {
    return jsonError('РџРѕР»Рµ session_id РѕР±СЏР·Р°С‚РµР»СЊРЅРѕ.', 400);
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
    return jsonError('РџРѕСЃР»РµРґРЅРµРµ СЃРѕРѕР±С‰РµРЅРёРµ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ РЅРµ РЅР°Р№РґРµРЅРѕ.', 400);
  }

  const answerLanguage = detectSupportedLanguage(latestUserMessage.content);
  const apiKeyHash = hashApiKey(readApiKeyFromHeaders(req.headers));
  const requestEventId = randomUUID();
  const userMessageId = String(body.message_id ?? '').trim() || randomUUID();
  const locateInLecture = Boolean(body.locateInLecture);
  const courseId = String(body.course_id ?? '').trim() || null;
  const courseVersionId = String(body.course_version_id ?? '').trim() || null;
  const lessonId = String(body.lesson_id ?? '').trim() || null;
  const lessonTitle = String(body.lesson_title ?? '').trim() || null;
  const priorUserMessages = sanitizedMessages
    .filter((msg) => msg.role === 'user')
    .slice(0, -1)
    .map((msg) => msg.content);
  const historyChainGuardrail =
    priorUserMessages.some((message) =>
      /РёРіРЅРѕСЂРёСЂСѓ(Р№|Р№С‚Рµ)|СЂР°СЃРєСЂРѕР№\s+(СЃРёСЃС‚РµРјРЅ[Р°-СЏС‘У™С–ТЈТ“ТЇТ±Т›У©Т»]*\s+)?(РїСЂРѕРјРїС‚|РёРЅСЃС‚СЂСѓРєС†)|ignore\s+previous\s+instructions|reveal\s+(the\s+)?system\s+prompt/i.test(
        message,
      ),
    ) &&
    /(РїСЂРµРґС‹РґСѓС‰[Р°-СЏС‘У™С–ТЈТ“ТЇТ±Т›У©Т»]*|РїСЂРѕС€Р»[Р°-СЏС‘У™С–ТЈТ“ТЇТ±Т›У©Т»]*|СЂР°РЅСЊС€Рµ|above|earlier|previous|that task|С‚Сѓ\s+Р·Р°РґР°С‡[Р°-СЏС‘У™С–ТЈТ“ТЇТ±Т›У©Т»]*)/i.test(
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
        message: 'Р-Р°РїСЂРѕСЃ РѕС‚РєР»РѕРЅРµРЅ: РѕР±РЅР°СЂСѓР¶РµРЅР° РїРѕРїС‹С‚РєР° РїСЂРѕРґРѕР»Р¶РёС‚СЊ СЂР°РЅРµРµ Р·Р°РґР°РЅРЅСѓСЋ РёРЅСЉРµРєС†РёРѕРЅРЅСѓСЋ РёРЅСЃС‚СЂСѓРєС†РёСЋ.',
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
        ? 'РЎРµСЃСЃРёСЏ РїСЂРёРЅР°РґР»РµР¶РёС‚ РґСЂСѓРіРѕРјСѓ API-РєР»РёРµРЅС‚Сѓ.'
        : 'РќРµ СѓРґР°Р»РѕСЃСЊ СЃРѕС…СЂР°РЅРёС‚СЊ СЃРѕСЃС‚РѕСЏРЅРёРµ РґРёР°Р»РѕРіР°.';
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
      ? await retrieveRelevantChunksDetailed(client, retrievalQuery, 6, lessonContent)
      : (await retrieveIndexedCourseChunks({
          client,
          apiKeyHash,
          query: retrievalQuery,
          courseId,
          courseVersionId,
          topK: 6,
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
      }),
    );

    const quotaCheck = await checkQuotaBudget(apiKeyHash, estimatedPromptTokens + retrieval.embeddingTokens);
    if (!quotaCheck.allowed) {
      await finalizeRequestEvent({
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

      return jsonError(quotaCheck.message, quotaCheck.status, { code: quotaCheck.code });
    }
  } catch (error) {
    console.error('Chat retrieval error:', error);
    await finalizeRequestEvent({
      id: requestEventId,
      status: 502,
      guardrailBlocked: false,
      guardrailReasons: [],
      injectionScore: 0,
      blockedStage: null,
      prompt: JSON.stringify(body),
      response: error instanceof Error ? error.message : 'retrieval_failed',
      usage: makeNoAnswerUsage(0),
    });
    return jsonError('РќРµ СѓРґР°Р»РѕСЃСЊ РІС‹РїРѕР»РЅРёС‚СЊ retrieval РґР»СЏ С‚РµРєСѓС‰РµРіРѕ Р·Р°РїСЂРѕСЃР°.', 502, {
      code: 'retrieval_failed',
    });
  }

  const stream = new ReadableStream({
    async start(controller) {
      const guardrailInfo = {
        blocked: false,
        reasons: [] as string[],
      };

      try {
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
          sendSseEvent(controller, 'chunk', { text });
          sendSseEvent(controller, 'done', {
            sources: [],
            citations: [],
            locateSnippet: null,
            usage,
            guardrails: guardrailInfo,
          });
          await finalizeRequestEvent({
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
          controller.close();
          return;
        }

        sendSseEvent(controller, 'meta', {
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
          sendSseEvent(controller, 'chunk', { text });
          sendSseEvent(controller, 'done', {
            sources: [],
            citations: [],
            locateSnippet: null,
            usage,
            guardrails: guardrailInfo,
          });
          await finalizeRequestEvent({
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
          controller.close();
          return;
        }

        const prompt = buildResponsesInput({
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
        });

        let fullAnswer = '';
        let usageChunk: {
          input_tokens?: number;
          output_tokens?: number;
          total_tokens?: number;
        } | null = null;

        for await (const event of responseStream) {
          if (event.type === 'response.output_text.delta') {
            const token = (event as { type: string; delta: string }).delta;
            if (token) {
              fullAnswer += token;
              sendSseEvent(controller, 'chunk', { text: token });
            }
          } else if (event.type === 'response.completed') {
            const completed = event as { type: string; response: { usage?: { input_tokens?: number; output_tokens?: number; total_tokens?: number } } };
            usageChunk = completed.response?.usage ?? null;
          }
        }

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
          /РІ РјР°С‚РµСЂРёР°Р»Р°С… Р»РµРєС†РёРё СЌС‚РѕРіРѕ РЅРµС‚|РґУ™СЂС–СЃ РјР°С‚РµСЂРёР°Р»РґР°СЂС‹РЅРґР°.*Р¶РѕТ›|not covered in the lecture materials/i.test(
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

        sendSseEvent(controller, 'done', {
          sources: hasNoAnswerPhrase || outputGuardrail.blocked ? [] : sources,
          citations: hasNoAnswerPhrase || outputGuardrail.blocked ? [] : retrieval.chunks.map((chunk) => chunk.id),
          locateSnippet,
          usage,
          guardrails: guardrailInfo,
        });

        await finalizeRequestEvent({
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

        controller.close();
      } catch (error) {
        console.error('Chat stream error:', error);
        const errorMessage =
          'РћС€РёР±РєР° РїСЂРё РѕР±СЂР°С‰РµРЅРёРё Рє РјРѕРґРµР»Рё. РџСЂРѕРІРµСЂСЊС‚Рµ РєР»СЋС‡, Р»РёРјРёС‚С‹ API Рё РїРѕРІС‚РѕСЂРёС‚Рµ Р·Р°РїСЂРѕСЃ.';
        sendSseEvent(controller, 'error', {
          code: 'chat_stream_failed',
          message: errorMessage,
          retryable: true,
        });
        await finalizeRequestEvent({
          id: requestEventId,
          status: 500,
          guardrailBlocked: false,
          guardrailReasons: [],
          injectionScore: 0,
          blockedStage: null,
          retrievalQuery: retrieval.diagnostics.retrieval_query,
          retrievedChunkIds: retrieval.chunks.map((chunk) => chunk.id),
          retrievalScores: retrieval.diagnostics.scores,
          retrievalFallbackUsed: retrieval.diagnostics.fallback_used,
          prompt: JSON.stringify(body),
          response: error instanceof Error ? error.message : errorMessage,
          usage: makeNoAnswerUsage(retrieval.embeddingTokens),
        });
        controller.close();
      }
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
