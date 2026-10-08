const assert = require('node:assert/strict');
const { test } = require('node:test');
const { execFileSync, spawnSync } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const config = path.join(__dirname, 'local-config.cjs');

function environment(values) {
  const env = { ...process.env, ...values };
  for (const key of ['AVATAR_STATE_DIR', 'AVATAR_MODEL_PATH', 'AVATAR_SAMPLE_AUDIO']) if (!Object.hasOwn(values, key)) delete env[key];
  return env;
}

test('uses only explicit private asset paths and one consistent state directory', () => {
  const output = execFileSync(process.execPath, ['-e', 'console.log(JSON.stringify(require(process.argv[1])))', config], {
    env: environment({ NODE_ENV: 'test', AVATAR_STATE_DIR: path.join(__dirname, '.local', 'test-state'), AVATAR_MODEL_PATH: path.join(__dirname, '.local', 'private-model.glb') }), encoding: 'utf8',
  });
  const settings = JSON.parse(output);
  assert.equal(settings.streamKey, path.join(settings.stateRoot, 'stream', 'key.txt'));
  assert.equal(settings.clips, path.join(settings.stateRoot, 'clips'));
  assert.equal(settings.model, path.join(__dirname, '.local', 'private-model.glb'));
  assert.equal(settings.sample, null);
});

test('does not assume a model or read another project environment', () => {
  const output = execFileSync(process.execPath, ['-e', 'console.log(JSON.stringify(require(process.argv[1])))', config], { env: environment({ NODE_ENV: 'test' }), encoding: 'utf8' });
  const settings = JSON.parse(output);
  assert.equal(settings.model, null);
  assert.equal(settings.sample, null);
  assert.equal(settings.stateRoot, path.join(__dirname, '.local'));
});

test('refuses production startup before any listener is opened', () => {
  const result = spawnSync(process.execPath, ['-e', 'require(process.argv[1])', config], { env: environment({ NODE_ENV: 'production' }), encoding: 'utf8' });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /local-only/);
});

test('the published viewer keeps rejected hand gestures disabled', () => {
  assert.match(fs.readFileSync(path.join(__dirname, 'viewer.js'), 'utf8'), /handGestures:\s*false/);
});
