const test = require('node:test');
const assert = require('node:assert/strict');
const { createAvatarGateway, createQuotaStore } = require('./gateway.cjs');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const env = { SPEECH_WORKER_URL: 'http://aiedu-voice-worker:8002/test-token/synthesize', AVATAR_EXPIRES_AT: String(Math.floor(Date.now() / 1000) + 3600), API_SHARED_KEY: 'test-server-key', OPENAI_API_KEY: 'test-only' };
const options = { env, readKey: () => 'test-native-key', clientFactory: () => ({}), reserveQuota: () => true };

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
  let inferenceCalls = 0;
  const nativeFetch = async (url, request) => {
    const address = String(url);
    if (address.startsWith('http://aiedu-voice-worker:8002/')) {
      assert.equal(JSON.parse(request.body).response_format, 'pcm');
      return new Response(Buffer.alloc(48000), { headers: { 'Content-Type': 'audio/pcm' } });
    }
    assert.equal(request.headers['X-Stream-Key'], 'test-native-key');
    const body = JSON.parse(request.body);
    if (address.endsWith('/start')) return Response.json({ session: 'test-session' });
    if (address.endsWith('/stop')) return Response.json({ stopped: true });
    assert.equal(address, 'http://127.0.0.1:8012/chunk');
    inferenceCalls++;
    sampleCount += Buffer.from(body.pcm, 'base64').length / 2;
    return Response.json({ names: ['jawOpen'], frames: [], ...(body.final ? { duration: sampleCount / 24000 } : {}) });
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
  assert.deepEqual(payloads.filter(packet => packet.kind === 'segment').map(packet => packet.offset), [0, 0.5]);
  assert.equal(payloads.find(packet => packet.kind === 'end').duration, 1);
  assert.equal(inferenceCalls, 3);
  assert.equal(payloads.at(-1).text, 'Synthetic speech.');
  assert.equal(JSON.stringify(payloads).includes(env.SPEECH_WORKER_URL), false);
});
