import '@/lib/ai/env-override';
export const dynamic = 'force-dynamic';
import { NextResponse } from 'next/server';
import { Agent } from 'undici';
import OpenAI, { toFile } from 'openai';

const MAX_AUDIO_BYTES = 10 * 1024 * 1024;
const LOCAL_STT_URL = process.env.LOCAL_STT_URL ?? 'http://127.0.0.1:8001/transcribe';
const STT_TIMEOUT_MS = Number(process.env.STT_TIMEOUT_MS ?? 180000);
const UNDICI_TIMEOUT_MS = Math.max(STT_TIMEOUT_MS + 30000, 120000);
const sttFetchAgent = new Agent({
  headersTimeout: UNDICI_TIMEOUT_MS,
  bodyTimeout: UNDICI_TIMEOUT_MS,
  connectTimeout: 15000,
});

function badRequest(message: string) {
  return NextResponse.json({ error: message }, { status: 400 });
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}

function getUndiciCauseCode(error: unknown): string {
  if (!(error instanceof Error)) {
    return '';
  }
  const maybeCause = (error as Error & { cause?: { code?: string } }).cause;
  return typeof maybeCause?.code === 'string' ? maybeCause.code : '';
}

export async function POST(req: Request) {
  try {
    const formData = await req.formData();
    const file = formData.get('file');
    const languageRaw = formData.get('language');
    const preferredLanguageRaw = formData.get('preferred_language');

    if (!(file instanceof File)) {
      return badRequest('РџРµСЂРµРґР°Р№С‚Рµ Р°СѓРґРёРѕ-С„Р°Р№Р» РІ РїРѕР»Рµ file.');
    }

    if (file.size === 0) {
      return badRequest('РђСѓРґРёРѕ-С„Р°Р№Р» РїСѓСЃС‚РѕР№.');
    }

    if (file.size > MAX_AUDIO_BYTES) {
      return badRequest('РђСѓРґРёРѕ СЃР»РёС€РєРѕРј Р±РѕР»СЊС€РѕРµ. Р›РёРјРёС‚ 10 MB.');
    }

    const allowedBaseTypes = new Set([
      'audio/webm',
      'audio/wav',
      'audio/x-wav',
      'audio/mpeg',
      'audio/mp4',
      'audio/ogg',
      'audio/m4a',
    ]);

    const normalizedType = (file.type || '').split(';')[0].trim().toLowerCase();
    if (normalizedType && !allowedBaseTypes.has(normalizedType)) {
      return badRequest(`РќРµРїРѕРґРґРµСЂР¶РёРІР°РµРјС‹Р№ С„РѕСЂРјР°С‚ Р°СѓРґРёРѕ: ${file.type}`);
    }

    const proxyFormData = new FormData();
    proxyFormData.append('file', file);
    const normalizedLanguage =
      typeof languageRaw === 'string' ? languageRaw.trim().toLowerCase() : 'auto';
    const language = ['auto', 'ru', 'kk', 'en'].includes(normalizedLanguage)
      ? normalizedLanguage
      : 'auto';
    proxyFormData.append('language', language);
    const preferredLanguage =
      typeof preferredLanguageRaw === 'string' ? preferredLanguageRaw.trim().toLowerCase() : '';
    if (['ru', 'kk', 'en'].includes(preferredLanguage)) {
      proxyFormData.append('preferred_language', preferredLanguage);
    }

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), STT_TIMEOUT_MS);
    let text = '';
    let detectedLanguage = '';
    let sttResponse: Response | null = null;

    try {
      try {
        sttResponse = await fetch(LOCAL_STT_URL, {
          method: 'POST',
          body: proxyFormData,
          signal: controller.signal,
          // Use explicit undici timeouts for long-running local STT calls.
          // eslint-disable-next-line @typescript-eslint/ban-ts-comment
          // @ts-expect-error dispatcher is available in Node runtime.
          dispatcher: sttFetchAgent,
        });

        if (!sttResponse.ok) {
          throw new Error(`Local STT returned status ${sttResponse.status}`);
        }

        const data = (await sttResponse.json().catch(() => ({}))) as {
          text?: string;
          transcript?: string;
          transcription?: string;
          language?: string;
        };
        text = (data.text ?? data.transcript ?? data.transcription ?? '').trim();
        detectedLanguage = (data.language ?? '').trim().toLowerCase();
      } catch (sttError) {
        console.warn('Local STT service is offline or returned an error. Using OpenAI Whisper fallback...', sttError);

        if (!process.env.OPENAI_API_KEY) {
          throw sttError;
        }

        const buffer = Buffer.from(await file.arrayBuffer());
        const openaiFile = await toFile(buffer, file.name || 'voice-note.webm', {
          type: file.type || 'audio/webm',
        });

        const openai = new OpenAI({
          apiKey: process.env.OPENAI_API_KEY,
        });

        const transcription = await openai.audio.transcriptions.create({
          file: openaiFile,
          model: 'whisper-1',
          language: language === 'auto' ? undefined : language,
        });

        text = (transcription.text ?? '').trim();
        detectedLanguage = language !== 'auto' ? language : 'ru';
      }
    } finally {
      clearTimeout(timeout);
    }

    return NextResponse.json({ text, language: detectedLanguage });
  } catch (error) {
    console.error('Transcribe route error:', error);

    const causeCode = getUndiciCauseCode(error);
    if (
      isAbortError(error) ||
      causeCode === 'UND_ERR_HEADERS_TIMEOUT' ||
      causeCode === 'UND_ERR_BODY_TIMEOUT'
    ) {
      return NextResponse.json(
        {
          error:
            'РўР°Р№РјР°СѓС‚ СЂР°СЃРїРѕР·РЅР°РІР°РЅРёСЏ СЂРµС‡Рё: Р»РѕРєР°Р»СЊРЅС‹Р№ STT РЅРµ СѓСЃРїРµР» РѕС‚РІРµС‚РёС‚СЊ. РџСЂРѕРІРµСЂСЊС‚Рµ, С‡С‚Рѕ local-stt Р·Р°РїСѓС‰РµРЅ Рё РјРѕРґРµР»СЊ РїСЂРѕРіСЂРµС‚Р°.',
        },
        { status: 504 },
      );
    }

    if (error instanceof TypeError && error.message.includes('fetch failed')) {
      return NextResponse.json(
        {
          error:
            'РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕРґРєР»СЋС‡РёС‚СЊСЃСЏ Рє local-stt. РџСЂРѕРІРµСЂСЊС‚Рµ URL Рё С‡С‚Рѕ СЃРµСЂРІРёСЃ Р·Р°РїСѓС‰РµРЅ РЅР° LOCAL_STT_URL.',
        },
        { status: 502 },
      );
    }

    return NextResponse.json(
      {
        error: 'РћС€РёР±РєР° СЂР°СЃРїРѕР·РЅР°РІР°РЅРёСЏ СЂРµС‡Рё. РџРѕРїСЂРѕР±СѓР№С‚Рµ СЃРЅРѕРІР° С‡РµСЂРµР· РЅРµСЃРєРѕР»СЊРєРѕ СЃРµРєСѓРЅРґ.',
      },
      { status: 500 },
    );
  }
}
