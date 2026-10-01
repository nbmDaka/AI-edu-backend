export const dynamic = 'force-dynamic';
import { NextResponse } from 'next/server';

const LOCAL_TTS_URL = process.env.LOCAL_TTS_URL ?? 'http://127.0.0.1:8002/synthesize';
const TTS_TIMEOUT_MS = Number(process.env.TTS_TIMEOUT_MS ?? 120000);
const MAX_TEXT_LENGTH = 10000;
const RUSSIAN_LANGUAGE = 'ru';

function badRequest(message: string) {
  return NextResponse.json({ error: message }, { status: 400 });
}

function splitTextIntoChunks(text: string, maxLen: number = 800): string[] {
  const sentences = text.split(/(?<=[.!?])\s+|\n+/);
  const chunks: string[] = [];
  let currentChunk = '';

  for (const sentence of sentences) {
    const trimmedSentence = sentence.trim();
    if (!trimmedSentence) continue;

    if (trimmedSentence.length > maxLen) {
      if (currentChunk) {
        chunks.push(currentChunk);
        currentChunk = '';
      }
      let temp = trimmedSentence;
      while (temp.length > maxLen) {
        let splitIndex = temp.lastIndexOf(' ', maxLen);
        if (splitIndex === -1 || splitIndex < maxLen / 2) {
          splitIndex = maxLen;
        }
        chunks.push(temp.substring(0, splitIndex).trim());
        temp = temp.substring(splitIndex).trim();
      }
      if (temp) {
        currentChunk = temp;
      }
    } else if ((currentChunk ? currentChunk.length + 1 : 0) + trimmedSentence.length > maxLen) {
      chunks.push(currentChunk);
      currentChunk = trimmedSentence;
    } else {
      currentChunk = currentChunk ? currentChunk + ' ' + trimmedSentence : trimmedSentence;
    }
  }
  if (currentChunk) {
    chunks.push(currentChunk);
  }
  return chunks;
}

function getWavDataOffset(arrayBuffer: ArrayBuffer): { headerSize: number; dataSize: number } {
  const view = new DataView(arrayBuffer);
  let offset = 12;
  const length = arrayBuffer.byteLength;

  while (offset < length - 8) {
    try {
      const chunkId = String.fromCharCode(
        view.getUint8(offset),
        view.getUint8(offset + 1),
        view.getUint8(offset + 2),
        view.getUint8(offset + 3)
      );
      const chunkSize = view.getUint32(offset + 4, true);

      if (chunkId === 'data') {
        return { headerSize: offset + 8, dataSize: chunkSize };
      }

      offset += 8 + chunkSize;
    } catch {
      break;
    }
  }

  return { headerSize: 44, dataSize: length - 44 };
}

export async function POST(req: Request) {
  try {
    const body = (await req.json()) as { text?: string; language?: string };
    const text = String(body?.text ?? '').trim();

    if (!text) {
      return badRequest('Передайте непустой текст.');
    }

    if (text.length > MAX_TEXT_LENGTH) {
      return badRequest(`Текст слишком длинный для TTS. Лимит: ${MAX_TEXT_LENGTH} символов.`);
    }

    if (body?.language && body.language !== RUSSIAN_LANGUAGE) {
      return badRequest('Озвучка поддерживает только русский язык (`ru`).');
    }

    const chunks = splitTextIntoChunks(text, 800);
    const audioBuffers: ArrayBuffer[] = [];

    for (const chunk of chunks) {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), TTS_TIMEOUT_MS);

      try {
        const ttsResponse = await fetch(LOCAL_TTS_URL, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ text: chunk, language: RUSSIAN_LANGUAGE }),
          signal: controller.signal,
        });

        if (!ttsResponse.ok) {
          const responseText = await ttsResponse.text().catch(() => '');
          console.error('Local TTS error for chunk:', ttsResponse.status, responseText);
          return NextResponse.json(
            { error: 'Локальный сервис синтеза речи недоступен или вернул ошибку.' },
            { status: 502 },
          );
        }

        const audioBuffer = await ttsResponse.arrayBuffer();
        audioBuffers.push(audioBuffer);
      } finally {
        clearTimeout(timeout);
      }
    }

    if (audioBuffers.length === 0) {
      return badRequest('Не удалось синтезировать аудио.');
    }

    if (audioBuffers.length === 1) {
      return new Response(audioBuffers[0], {
        headers: {
          'Content-Type': 'audio/wav',
          'Cache-Control': 'no-store',
        },
      });
    }

    const firstWav = getWavDataOffset(audioBuffers[0]);
    const headerBytes = new Uint8Array(audioBuffers[0], 0, firstWav.headerSize);

    let totalDataSize = firstWav.dataSize;
    const dataSlices: Uint8Array[] = [new Uint8Array(audioBuffers[0], firstWav.headerSize)];

    for (let i = 1; i < audioBuffers.length; i++) {
      const wav = getWavDataOffset(audioBuffers[i]);
      totalDataSize += wav.dataSize;
      dataSlices.push(new Uint8Array(audioBuffers[i], wav.headerSize));
    }

    const combinedBuffer = new ArrayBuffer(firstWav.headerSize + totalDataSize);
    const combinedView = new Uint8Array(combinedBuffer);

    combinedView.set(headerBytes, 0);

    let currentOffset = firstWav.headerSize;
    for (const slice of dataSlices) {
      combinedView.set(slice, currentOffset);
      currentOffset += slice.byteLength;
    }

    const combinedDataView = new DataView(combinedBuffer);
    combinedDataView.setUint32(4, firstWav.headerSize + totalDataSize - 8, true);
    combinedDataView.setUint32(firstWav.headerSize - 4, totalDataSize, true);

    return new Response(combinedBuffer, {
      headers: {
        'Content-Type': 'audio/wav',
        'Cache-Control': 'no-store',
      },
    });
  } catch (error) {
    console.error('TTS route error:', error);
    return NextResponse.json(
      { error: 'Ошибка синтеза речи. Попробуйте снова через несколько секунд.' },
      { status: 500 },
    );
  }
}
