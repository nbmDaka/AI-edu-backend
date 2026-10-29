import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pcmToWav, synthesizePcm } from './speech.mjs';
import { createVoiceServer } from './server.mjs';

test('PCM is wrapped with exact mono WAV lengths, not streaming sentinels', () => {
  const pcm = Buffer.alloc(4800);
  const wav = pcmToWav(pcm);
  assert.equal(wav.readUInt32LE(4), wav.length - 8);
  assert.equal(wav.readUInt32LE(40), pcm.length);
  assert.equal(wav.readUInt32LE(24), 24000);
  assert.equal(wav.readUInt16LE(22), 1);
  assert.equal(wav.readUInt16LE(34), 16);
  assert.throws(() => pcmToWav(Buffer.alloc(3)));
});

test('provider request uses fixed server-owned URL, Cedar, PCM and approved pace', async () => {
  let input;
  const pcm = await synthesizePcm('Здравствуйте.', {
    apiKey: 'private-test-key', model: 'gpt-4o-mini-tts', voice: 'cedar',
    fetchImpl: async (url, options) => { input = { url, ...options }; return new Response(Buffer.alloc(48)); },
  });
  assert.equal(pcm.length, 48);
  assert.equal(input.url, 'https://api.openai.com/v1/audio/speech');
  const body = JSON.parse(input.body);
  assert.equal(body.voice, 'cedar'); assert.equal(body.response_format, 'pcm'); assert.equal(body.speed, 0.98);
});

test('private worker validates access and text, returns WAV and persists quotas', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'cedar-worker-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const token = 'test-private-worker-token-0123456789';
  let calls = 0;
  const env = { OPENAI_API_KEY: 'sk-test-only-key', TTS_WORKER_TOKEN: token, TTS_DATA_DIR: directory, TTS_DAILY_CHARACTER_LIMIT: '1000' };
  const server = await createVoiceServer({ env, fetchImpl: async () => { calls++; return new Response(Buffer.alloc(2400)); } });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const base = `http://127.0.0.1:${server.address().port}`;
  const request = (path, data) => fetch(base + path, { method: 'POST', body: JSON.stringify(data) });
  assert.equal((await fetch(base + '/health')).status, 200);
  assert.equal((await request('/synthesize', { text: 'Test' })).status, 404);
  assert.equal((await request(`/${token}/synthesize`, { text: 'No', language: 'en' })).status, 400);
  const response = await request(`/${token}/synthesize`, { text: 'Здравствуйте.', language: 'ru' });
  assert.equal(response.status, 200); assert.equal(response.headers.get('x-tutor-voice'), 'cedar');
  assert.equal(Buffer.from(await response.arrayBuffer()).toString('ascii', 0, 4), 'RIFF');
  assert.equal(calls, 1);
  assert.equal(JSON.parse(await readFile(join(directory, 'speech-quota.json'), 'utf8')).characters, 13);
  const limited = await request(`/${token}/synthesize`, { text: 'a'.repeat(995), language: 'ru' });
  assert.equal(limited.status, 429); assert.equal(calls, 1);
});

test('PCM response streams before the provider finishes and retains the same quota', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'cedar-pcm-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const token = 'test-private-pcm-token-01234567890123456789';
  let finish;
  const providerDone = new Promise(resolve => { finish = resolve; });
  const provider = new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array(4800).fill(1));
      providerDone.then(() => { controller.enqueue(new Uint8Array(4800).fill(2)); controller.close(); });
    },
  });
  const server = await createVoiceServer({
    env: { OPENAI_API_KEY: 'sk-test-only-key', TTS_WORKER_TOKEN: token, TTS_DATA_DIR: directory },
    fetchImpl: async () => new Response(provider),
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const response = await fetch(`http://127.0.0.1:${server.address().port}/${token}/synthesize`, {
    method: 'POST', body: JSON.stringify({ text: 'Здравствуйте.', response_format: 'pcm' }),
  });
  assert.equal(response.headers.get('content-type'), 'audio/pcm');
  assert.equal(response.headers.get('x-pcm-sample-rate'), '24000');
  const reader = response.body.getReader();
  const first = await reader.read();
  assert.equal(first.value.length, 4800); assert.equal(first.value[0], 1);
  finish();
  const parts = [first.value];
  for (;;) { const chunk = await reader.read(); if (chunk.done) break; parts.push(chunk.value); }
  assert.equal(Buffer.concat(parts).length, 9600);
  assert.equal(JSON.parse(await readFile(join(directory, 'speech-quota.json'), 'utf8')).characters, 13);
});
