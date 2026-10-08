const fs = require('node:fs/promises');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { clips: output } = require('./local-config.cjs');
const api = 'http://127.0.0.1:8011';
const player = '/audio2face/Player';
let busy = false, uncertainExport = false;

function validateWave(data) {
  if (data.length < 44 || data.length > 1920044 || data.toString('ascii', 0, 4) !== 'RIFF' ||
      data.toString('ascii', 8, 12) !== 'WAVE' || data.readUInt32LE(4) !== data.length - 8) {
    throw new Error('Expected a PCM WAV of up to 60 seconds');
  }
  let format, samples;
  for (let offset = 12; offset + 8 <= data.length;) {
    const size = data.readUInt32LE(offset + 4), start = offset + 8;
    if (start + size > data.length) throw new Error('Truncated WAV');
    const id = data.toString('ascii', offset, offset + 4);
    if (id === 'fmt ') {
      if (format || size < 16) throw new Error('Invalid WAV format chunk');
      format = data.subarray(start, start + size);
    }
    if (id === 'data') {
      if (samples) throw new Error('Multiple WAV data chunks');
      samples = data.subarray(start, start + size);
    }
    offset = start + size + (size % 2);
  }
  if (!format || !samples?.length || samples.length % 2 ||
      format.readUInt16LE(0) !== 1 || format.readUInt16LE(2) !== 1 ||
      format.readUInt32LE(4) !== 16000 || format.readUInt32LE(8) !== 32000 ||
      format.readUInt16LE(12) !== 2 || format.readUInt16LE(14) !== 16) {
    throw new Error('Expected mono PCM16 WAV at 16000 Hz');
  }
  const duration = samples.length / 32000;
  if (duration < .1 || duration > 60) throw new Error('Audio duration must be 0.1 to 60 seconds');
  return duration;
}

async function call(route, body, timeout = 15000) {
  const response = await fetch(api + route, {
    method: body ? 'POST' : 'GET',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(timeout),
  });
  if (!response.ok) throw new Error(`Audio2Face HTTP ${response.status}`);
  const result = await response.json();
  if (result !== 'OK' && result.status !== 'OK') throw new Error(result.message || 'Audio2Face not ready');
  return result;
}

async function health() {
  if (busy) return { ready: true, busy, engine: 'NVIDIA Audio2Face', mode: 'clip' };
  try {
    await call('/status');
    const solvers = await call('/A2F/Exporter/GetBlendShapeSolvers');
    return { ready: Boolean(solvers.result?.length) && !uncertainExport, busy, engine: 'NVIDIA Audio2Face', mode: 'clip' };
  } catch {
    return { ready: false, busy, engine: 'NVIDIA Audio2Face', mode: 'clip' };
  }
}

async function animate(data) {
  const duration = validateWave(data);
  const hash = createHash('sha256').update(data).digest('hex');
  const folder = path.join(output, hash);
  const cached = path.join(folder, 'clip.json');
  const { validateClip } = await import('./a2f-timeline.mjs');
  try {
    const clip = validateClip(JSON.parse(await fs.readFile(cached, 'utf8')));
    if (clip.audioHash === hash && clip.duration === duration) return { ...clip, cached: true };
  } catch { /* Only successful, matching neural exports are reusable. */ }
  await call('/status');
  const solvers = await call('/A2F/Exporter/GetBlendShapeSolvers');
  if (solvers.result?.length !== 1 || solvers.result[0] !== '/audio2face/BlendshapeSolve') {
    throw new Error('Load the prepared Mark Audio2Face scene with one blendshape solver');
  }
  await fs.mkdir(folder, { recursive: true });
  await fs.writeFile(path.join(folder, 'speech.wav'), data);
  const started = performance.now();
  await call('/A2F/Player/Pause', { a2f_player: player });
  await call('/A2F/Player/SetRootPath', { a2f_player: player, dir_path: folder.replaceAll('\\', '/') });
  await call('/A2F/Player/SetTrack', { a2f_player: player, file_name: 'speech.wav', time_range: [0, duration] });
  const range = await call('/A2F/Player/GetRange', { a2f_player: player });
  if (!Number.isFinite(range.result?.default?.[1]) || Math.abs(range.result.default[1] - duration) > .05 || range.result?.work?.[0] !== 0) {
    throw new Error('Audio2Face has not loaded the selected audio');
  }
  await call('/A2F/Player/SetTime', { a2f_player: player, time: 0 });
  uncertainExport = true;
  await call('/A2F/Exporter/ExportBlendshapes', {
    solver_node: solvers.result[0], export_directory: folder.replaceAll('\\', '/'),
    file_name: 'speech', format: 'json', batch: false, fps: 30,
  }, 240000);
  uncertainExport = false;
  const matrix = JSON.parse(await fs.readFile(path.join(folder, 'speech_bsweight.json'), 'utf8'));
  if (matrix.numPoses !== 46 || matrix.numFrames !== matrix.weightMat?.length) throw new Error('Unexpected solver output');
  const mapping = JSON.parse(await fs.readFile(path.join(__dirname, 'a2f-cc-map.json'), 'utf8'));
  if (Object.keys(mapping).some(name => !matrix.facsNames.includes(name))) throw new Error('Incompatible FACS names');
  const clip = validateClip({
    engine: 'nvidia-audio2face-2023.2', solver: 'mark-facs-46', fps: 30, duration,
    facsNames: matrix.facsNames, weightMat: matrix.weightMat, audioHash: hash,
    processingMs: Math.round(performance.now() - started),
  });
  await fs.writeFile(cached, JSON.stringify(clip));
  return { ...clip, cached: false };
}

async function handleAnimation(req, res) {
  const json = (code, data) => { res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(data)); };
  if (busy || uncertainExport) return json(409, { error: 'Audio2Face занят. Дождитесь окончания обработки или перезапустите просмотрщик после ошибки экспорта.' });
  if (req.headers['content-type'] !== 'audio/wav') return json(415, { error: 'Требуется WAV' });
  busy = true;
  try {
    let size = 0;
    const chunks = [];
    for await (const chunk of req) {
      size += chunk.length;
      if (size > 1920044) { json(413, { error: 'Запись слишком длинная' }); req.destroy(); return; }
      chunks.push(chunk);
    }
    const data = Buffer.concat(chunks);
    try { validateWave(data); } catch (error) { json(400, { error: error.message }); return; }
    const result = await animate(data);
    json(200, result);
  } catch (error) {
    console.error('Audio2Face:', error.message);
    json(503, { error: 'Не удалось обработать запись в Audio2Face. Проверьте, что сервис запущен и тестовая сцена загружена.' });
  } finally {
    busy = false;
  }
}

module.exports = { handleAnimation, health, validateWave };
