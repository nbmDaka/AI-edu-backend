const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { handleAnimation, health } = require('./a2f-bridge.cjs');
const { model, sample } = require('./local-config.cjs');
if (!model || !fs.existsSync(model)) throw new Error('Set AVATAR_MODEL_PATH to your private, separately licensed GLB.');
const three = path.resolve(path.dirname(require.resolve('three')), '..');
const assets = new Map([
  ['/', path.join(__dirname, 'viewer.html')],
  ['/viewer.js', path.join(__dirname, 'viewer.js')],
  ['/a2f-timeline.mjs', path.join(__dirname, 'a2f-timeline.mjs')],
  ['/stream-playback.mjs', path.join(__dirname, 'stream-playback.mjs')],
  ['/body-gestures.mjs', path.join(__dirname, 'body-gestures.mjs')],
  ['/a2f-cc-map.json', path.join(__dirname, 'a2f-cc-map.json')],
  ['/kevin.glb', model],
  ['/sample.wav', sample],
]);
const port = Number(process.env.AVATAR_PORT || 5184);
const allowedHosts = new Set([`localhost:${port}`, `127.0.0.1:${port}`]);
const mime = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8', '.json': 'application/json', '.glb': 'model/gltf-binary', '.wav': 'audio/wav' };
http.createServer(async (req, res) => {
  if (!allowedHosts.has(req.headers.host) || (req.headers.origin && ![`http://localhost:${port}`, `http://127.0.0.1:${port}`].includes(req.headers.origin))) {
    res.writeHead(403); res.end('Local requests only'); return;
  }
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/api/animation' && req.method === 'POST') return handleAnimation(req, res);
  if (url.pathname === '/api/status' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    res.end(JSON.stringify(await health())); return;
  }
  if (req.method !== 'GET') { res.writeHead(405); res.end(); return; }
  let file = assets.get(url.pathname);
  if (url.pathname.startsWith('/three/')) {
    const candidate = path.resolve(three, '.' + url.pathname.slice('/three'.length));
    if (candidate.startsWith(three + path.sep)) file = candidate;
  }
  if (!file || !fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); res.end('Not found'); return; }
  res.writeHead(200, { 'Content-Type': mime[path.extname(file)] || 'application/octet-stream', 'Content-Length': fs.statSync(file).size, 'Cache-Control': 'no-store' });
  fs.createReadStream(file).pipe(res);
}).listen(port, '127.0.0.1', () => console.log(`Avatar test: http://localhost:${port}`));
