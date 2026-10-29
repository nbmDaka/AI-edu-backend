export function pcmToWav(pcm, sampleRate = 24000) {
  if (!pcm.length || pcm.length % 2 || pcm.length > 16000000) throw new Error('invalid_pcm');
  const wav = Buffer.alloc(44 + pcm.length);
  wav.write('RIFF', 0); wav.writeUInt32LE(36 + pcm.length, 4); wav.write('WAVEfmt ', 8);
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(sampleRate, 24); wav.writeUInt32LE(sampleRate * 2, 28);
  wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
  wav.write('data', 36); wav.writeUInt32LE(pcm.length, 40); pcm.copy(wav, 44);
  return wav;
}

export async function* streamPcm(text, { apiKey, model, voice, signal, fetchImpl = fetch }) {
  const response = await fetchImpl('https://api.openai.com/v1/audio/speech', {
    method: 'POST', signal,
    headers: { Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model, voice, input: text, response_format: 'pcm', speed: 0.98,
      instructions: 'Speak Russian with native pronunciation. Sound like a calm, approachable male university teacher around 36 to 45. Use a natural medium pitch, clear conversational delivery and brief meaningful pauses. Do not imitate any real person. Read only the supplied text.',
    }),
  });
  if (!response.ok) {
    await response.body?.cancel();
    const error = new Error('speech_provider_failed');
    error.status = response.status === 429 ? 429 : 502;
    throw error;
  }
  let size = 0;
  for await (const chunk of response.body) {
    size += chunk.length;
    if (size > 16000000) throw new Error('speech_response_too_large');
    yield Buffer.from(chunk);
  }
}

export async function synthesizePcm(text, options) {
  const chunks = [];
  for await (const chunk of streamPcm(text, options)) chunks.push(chunk);
  return Buffer.concat(chunks);
}
