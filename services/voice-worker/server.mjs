import { createServer } from 'node:http';
import { mkdir, readFile, rename, writeFile, lstat } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { timingSafeEqual } from 'node:crypto';
import { once } from 'node:events';
import { pcmToWav, synthesizePcm, streamPcm } from './speech.mjs';

export async function createVoiceServer({ env = process.env, fetchImpl = fetch, now = () => new Date() } = {}) {
  const apiKey = (env.OPENAI_API_KEY || '').trim();
  const token = env.TTS_WORKER_TOKEN || '';
  if (!apiKey.startsWith('sk-') || token.length < 32) throw new Error('voice_worker_not_configured');
  const voice = 'cedar';
  const model = env.OPENAI_TTS_MODEL || 'gpt-4o-mini-tts';
  const dailyLimit = Number(env.TTS_DAILY_CHARACTER_LIMIT || 50000);
  if (!Number.isInteger(dailyLimit) || dailyLimit < 1000 || dailyLimit > 1000000) throw new Error('invalid_speech_limit');
  const directory = resolve(env.TTS_DATA_DIR || '/data');
  const quotaFile = resolve(directory, 'speech-quota.json');
  await mkdir(directory, { recursive: true });
  let quota = { day: now().toISOString().slice(0, 10), characters: 0 };
  try {
    if ((await lstat(quotaFile)).isSymbolicLink()) throw new Error('invalid_quota_file');
    quota = JSON.parse(await readFile(quotaFile, 'utf8'));
    if (!/^\d{4}-\d{2}-\d{2}$/.test(quota.day) || !Number.isSafeInteger(quota.characters) || quota.characters < 0) throw new Error('invalid_quota_file');
  } catch (error) { if (error.code !== 'ENOENT') throw error; }
  let writes = Promise.resolve();
  let active = 0;
  let minute = '';
  let minuteRequests = 0;
  const secretPath = Buffer.from(`/${token}/synthesize`);
  const json = (res, status, body) => {
    if (!res.destroyed) res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' }).end(JSON.stringify(body));
  };
  return createServer(async (req, res) => {
    if (req.method === 'GET' && req.url === '/health') {
      json(res, 200, { status: 'ok', provider: 'openai', voice, ai_generated: true });
      return;
    }
    const supplied = Buffer.from(req.url || '');
    if (req.method !== 'POST' || supplied.length !== secretPath.length || !timingSafeEqual(supplied, secretPath)) {
      json(res, 404, { error: 'Not found' }); return;
    }
    if (active >= 2) { json(res, 429, { error: 'Сервис озвучки занят. Повторите запрос немного позже.' }); return; }
    active += 1;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 65000);
    const onClose = () => { if (!res.writableEnded) controller.abort(); };
    res.on('close', onClose);
    try {
      const buffers = [];
      let bytes = 0;
      for await (const chunk of req) {
        bytes += chunk.length;
        if (bytes > 12000) { json(res, 413, { error: 'Запрос слишком большой.' }); return; }
        buffers.push(chunk);
      }
      let data;
      try { data = JSON.parse(Buffer.concat(buffers).toString('utf8')); }
      catch { json(res, 400, { error: 'Некорректный JSON.' }); return; }
      const text = typeof data.text === 'string' ? data.text.trim() : '';
      if (!text || text.length > 1000 || (data.language && data.language !== 'ru')) {
        json(res, 400, { error: 'Нужен русский текст длиной от 1 до 1000 символов.' }); return;
      }
      if (data.response_format && !['wav', 'pcm'].includes(data.response_format)) {
        json(res, 400, { error: 'Неизвестный формат озвучки.' }); return;
      }
      const timestamp = now().toISOString();
      const day = timestamp.slice(0, 10);
      if (quota.day !== day) quota = { day, characters: 0 };
      if (minute !== timestamp.slice(0, 16)) { minute = timestamp.slice(0, 16); minuteRequests = 0; }
      if (minuteRequests >= 30 || quota.characters + text.length > dailyLimit) {
        json(res, 429, { error: 'Лимит озвучки исчерпан. Попробуйте позже.' }); return;
      }
      minuteRequests += 1;
      quota.characters += text.length;
      // Reserve persistently before a billable call. Failed calls remain reserved.
      const snapshot = JSON.stringify(quota);
      writes = writes.then(async () => {
        const temporary = `${quotaFile}.tmp`;
        await writeFile(temporary, snapshot, { mode: 0o600 });
        await rename(temporary, quotaFile);
      });
      await writes;
      if (data.response_format === 'pcm') {
        for await (const chunk of streamPcm(text, { apiKey, model, voice, signal: controller.signal, fetchImpl })) {
          if (!res.headersSent) res.writeHead(200, {
            'Content-Type': 'audio/pcm', 'Cache-Control': 'no-store', 'X-Tutor-Voice': voice,
            'X-AI-Generated-Speech': 'true', 'X-PCM-Sample-Rate': '24000',
          });
          if (!res.write(chunk)) await once(res, 'drain', { signal: controller.signal });
        }
        res.end(); return;
      }
      const pcm = await synthesizePcm(text, { apiKey, model, voice, signal: controller.signal, fetchImpl });
      const wav = pcmToWav(pcm);
      if (!res.destroyed) res.writeHead(200, {
        'Content-Type': 'audio/wav', 'Content-Length': wav.length, 'Cache-Control': 'no-store',
        'X-Tutor-Voice': voice, 'X-AI-Generated-Speech': 'true',
      }).end(wav);
    } catch (error) {
      // Never log the private URL, provider credentials, response body, or speech text.
      console.warn('speech_request_failed', error.name || 'Error');
      if (res.headersSent) res.destroy();
      else json(res, error.status || (controller.signal.aborted ? 504 : 502), { error: 'Не удалось озвучить ответ. Попробуйте ещё раз.' });
    } finally {
      clearTimeout(timer); res.off('close', onClose); active -= 1;
    }
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const server = await createVoiceServer();
  server.listen(Number(process.env.PORT || 8002), '0.0.0.0', () => console.log('cedar_voice_worker_started'));
  process.on('SIGTERM', () => server.close(() => process.exit(0)));
}
