const test = require('node:test');
const assert = require('node:assert/strict');
const { createAvatarGateway, createQuotaStore } = require('./gateway.cjs');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { SpokenText, createSpeechQueue } = require('./speech-queue.cjs');

const env = { SPEECH_WORKER_URL: 'http://aiedu-voice-worker:8002/test-token/synthesize', AVATAR_EXPIRES_AT: String(Math.floor(Date.now() / 1000) + 3600), API_SHARED_KEY: 'test-server-key', OPENAI_API_KEY: 'test-only' };
const options = { env, readKey: () => 'test-native-key', clientFactory: () => ({}), reserveQuota: () => true };

test('spoken text keeps complete sentences, decimal numbers and words intact', () => {
  const output = [];
  const text = new SpokenText(sentence => output.push(sentence));
  text.push('Значение равно 3.');
  assert.deepEqual(output, []);
  text.push('14. Это пример.');
  text.push('', true);
  assert.deepEqual(output, ['Значение равно 3.14.', 'Это пример.']);
  const long = 'Нормальное предложение ' + 'про учебные материалы '.repeat(23) + 'закончено.';
  text.push(long, true);
  assert.equal(output.at(-1), long);
  const oversized = 'Слова остаются целыми, ' + 'преподаватель объясняет материал '.repeat(80) + 'конец.';
  const bounded = [];
  new SpokenText(sentence => bounded.push(sentence)).push(oversized, true);
  assert.ok(bounded.every(sentence => sentence.length <= 1000));
  assert.equal(bounded.join(' '), oversized);
});

test('speech prefetch overlaps two sentences, preserves order and never fans out unboundedly', async () => {
  const controller = new AbortController();
  const fetched = [];
  const queue = createSpeechQueue(async text => { fetched.push(text); return new Response(text); }, controller.signal);
  queue.push('first'); queue.push('second'); queue.push('third'); queue.finish();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(fetched, ['first', 'second']);
  const response = queue.responses();
  const first = await response.next();
  assert.equal(await first.value.text(), 'first');
  assert.deepEqual(fetched, ['first', 'second']);
  const second = await response.next();
  assert.equal(await second.value.text(), 'second');
  assert.deepEqual(fetched, ['first', 'second', 'third']);
  const third = await response.next();
  assert.equal(await third.value.text(), 'third');
  assert.equal((await response.next()).done, true);
  queue.close();
});

test('cancellation wakes an empty speech queue and prevents further provider requests', async () => {
  const controller = new AbortController();
  const queue = createSpeechQueue(() => { throw new Error('Must not be called'); }, controller.signal);
  const pending = queue.responses().next();
  controller.abort();
  await assert.rejects(pending, { name: 'AbortError' });
  assert.throws(() => queue.push('late'), { name: 'AbortError' });
  queue.close();
});

test('closed-test request quota survives process restart', t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'avatar-quota-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  let reserve = createQuotaStore({ AVATAR_DATA_DIR: directory });
  for (let index = 0; index < 79; index++) assert.equal(reserve(), true);
  reserve = createQuotaStore({ AVATAR_DATA_DIR: directory });
  assert.equal(reserve(), true); assert.equal(reserve(), false);
});

test('only the private speech worker and an unexpired deadline are accepted', () => {
  for (const url of ['https://example.org/', 'http://127.0.0.1:8002/', 'http://aiedu-voice-worker:8002/a?key=x', 'http://x@y:8002/']) {
    assert.throws(() => createAvatarGateway({ ...options, env: { ...env, SPEECH_WORKER_URL: url } }));
  }
  assert.throws(() => createAvatarGateway({ ...options, env: { ...env, AVATAR_EXPIRES_AT: '0' } }));
});

test('a synthetic streamed PCM answer preserves audio offsets, NVIDIA frames and access checks', async t => {
  let sampleCount = 0;
  const inferenceSizes = [];
  const nativeFetch = async (url, request) => {
    const address = String(url);
    if (address.startsWith('http://aiedu-voice-worker:8002/')) {
      assert.equal(JSON.parse(request.body).response_format, 'pcm');
      return new Response(Buffer.alloc(144000), { headers: { 'Content-Type': 'audio/pcm' } });
    }
    assert.equal(request.headers['X-Stream-Key'], 'test-native-key');
    const body = JSON.parse(request.body);
    if (address.endsWith('/start')) return Response.json({ session: 'test-session' });
    if (address.endsWith('/stop')) return Response.json({ stopped: true });
    assert.equal(address, 'http://127.0.0.1:8012/chunk');
    const size = Buffer.from(body.pcm, 'base64').length;
    inferenceSizes.push(size); sampleCount += size / 2;
    return Response.json({ names: ['jawOpen'], frames: size ? [{ t: sampleCount / 24000, weights: [.3] }] : [], ...(body.final ? { duration: sampleCount / 24000 } : {}) });
  };
  const server = createAvatarGateway({ ...options, fetchImpl: nativeFetch });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const base = `http://127.0.0.1:${server.address().port}`;
  assert.equal((await fetch(base + '/api/avatar', { method: 'POST' })).status, 403);
  assert.equal((await fetch(base + '/api/avatar', { method: 'POST', headers: { 'X-API-Key': env.API_SHARED_KEY, Origin: 'https://example.org' } })).status, 403);
  const response = await fetch(base + '/api/avatar', {
    method: 'POST', headers: { 'X-API-Key': env.API_SHARED_KEY },
    body: JSON.stringify({ messages: [{ role: 'user', content: 'Test' }], lesson_content: 'Synthetic lesson.', lesson_title: 'Test', speech_text: 'Synthetic speech.' }),
  });
  assert.equal(response.status, 200);
  const payloads = (await response.text()).split('\n').filter(line => line.startsWith('data: ')).map(line => JSON.parse(line.slice(6)));
  assert.equal(payloads[0].engine, 'nvidia-audio2face-sdk-mark-2.3');
  const segments = payloads.filter(packet => packet.kind === 'segment');
  assert.deepEqual(segments.map(packet => packet.offset), [0, .5, 1, 1.5, 2, 2.5]);
  assert.ok(segments.every(packet => Buffer.from(packet.pcm, 'base64').length === 24000));
  assert.deepEqual(segments.flatMap(packet => packet.frames).map(frame => frame.t), [.5, 1, 2, 3]);
  assert.equal(payloads.find(packet => packet.kind === 'end').duration, 3);
  assert.deepEqual(inferenceSizes, [24000, 24000, 48000, 48000, 0]);
  assert.equal(payloads.at(-1).text, 'Synthetic speech.');
  assert.equal(JSON.stringify(payloads).includes(env.SPEECH_WORKER_URL), false);
});

test('first-sentence speech generation overlaps native warmup instead of waiting for it', { timeout: 5000 }, async t => {
  let releaseNative, speechStarted;
  const warmup = new Promise(resolve => { releaseNative = resolve; });
  const speechReady = new Promise(resolve => { speechStarted = resolve; });
  const spoken = [];
  const server = createAvatarGateway({ ...options,
    clientFactory: () => ({ chat: { completions: { create: async function* () {
      yield { choices: [{ delta: { content: 'Короткий ответ. ' } }] };
      yield { choices: [{ delta: { content: 'Следующее предложение целиком.' } }] };
    } } } }),
    fetchImpl: async (url, request) => {
      const body = JSON.parse(request.body);
      if (String(url).includes('aiedu-voice-worker')) {
        spoken.push(body.text); speechStarted();
        return new Response(Buffer.alloc(0), { headers: { 'Content-Type': 'audio/pcm' } });
      }
      if (String(url).endsWith('/start')) { await warmup; return Response.json({ session: 'warm' }); }
      if (String(url).endsWith('/stop')) return Response.json({ stopped: true });
      return Response.json({ names: ['jawOpen'], frames: [], duration: 0 });
    },
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  t.after(() => releaseNative());
  const response = await fetch(`http://127.0.0.1:${server.address().port}/api/avatar`, {
    method: 'POST', headers: { 'X-API-Key': env.API_SHARED_KEY },
    body: JSON.stringify({ messages: [{ role: 'user', content: 'Test' }], lesson_content: 'Synthetic lesson.' }),
  });
  await speechReady;
  assert.deepEqual(spoken, ['Короткий ответ.', 'Следующее предложение целиком.']);
  releaseNative();
  assert.match(await response.text(), /event: done/);
});
