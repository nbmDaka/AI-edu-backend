const health = await fetch('http://127.0.0.1:8002/health');
console.log(JSON.stringify(await health.json()));
const response = await fetch(`http://127.0.0.1:8002/${process.env.TTS_WORKER_TOKEN}/synthesize`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ text: 'Здравствуйте. Давайте разберём материал этой лекции.', language: 'ru' }),
});
const buffer = Buffer.from(await response.arrayBuffer());
const valid = response.ok && buffer.toString('ascii', 0, 4) === 'RIFF' && buffer.readUInt32LE(40) === buffer.length - 44;
console.log(JSON.stringify({ status: response.status, voice: response.headers.get('x-tutor-voice'), bytes: buffer.length, wav_valid: valid }));
if (!valid) process.exitCode = 1;
