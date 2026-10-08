import assert from 'node:assert/strict';
import { test } from 'node:test';
import { StreamPlayback } from './stream-playback.mjs';

function makeContext() {
  const scheduled = [];
  return { currentTime: 0, state: 'running', scheduled,
    createBuffer: (_, length) => ({ copyToChannel: data => assert.equal(data.length, length) }),
    createBufferSource: () => ({ connect() {}, disconnect() {}, stop() { this.stopped = true; }, start(t) { scheduled.push({ t, source: this }); } }),
  };
}
const pcm = Buffer.alloc(24000).toString('base64');
const packet = (offset, t) => ({ kind: 'segment', sampleRate: 24000, offset, pcm, names: ['jawDrop'], frames: [{ t, weights: [.2] }, { t: t + .03, weights: [.4] }] });

test('does not play sound before neural context is buffered', () => {
  const ctx = makeContext(), stream = new StreamPlayback(() => ctx);
  stream.accept(packet(0, .2)); stream.tick();
  assert.equal(ctx.scheduled.length, 0);
});
test('queues contiguous PCM and samples lip sync on the same playback clock', () => {
  const ctx = makeContext(), stream = new StreamPlayback(() => ctx);
  stream.accept(packet(0, .2)); stream.accept(packet(.5, .7)); stream.accept(packet(1, 1.2));
  stream.tick(); assert.equal(ctx.scheduled.length, 2);
  assert.equal(ctx.scheduled[1].t, ctx.scheduled[0].t + .5);
  ctx.currentTime = .35;
  const state = stream.tick(); assert.equal(state.active, true); assert(state.weights.jawDrop > 0);
  assert(state.time < .3);
});
test('reset cancels scheduled audio and erases old lecture frames', () => {
  const ctx = makeContext(), stream = new StreamPlayback(() => ctx);
  stream.accept(packet(0, .2)); stream.accept({ kind: 'end', duration: .5, names: ['jawDrop'], frames: [{ t: .5, weights: [0] }] });
  stream.tick(); stream.reset();
  assert(ctx.scheduled[0].source.stopped); assert.equal(stream.frames.length, 0); assert.equal(stream.received, 0);
});
test('rejects reordered PCM and nonfinite or reordered neural frames', () => {
  const stream = new StreamPlayback(() => makeContext());
  assert.throws(() => stream.accept(packet(1, .2)), /Out-of-order audio/);
  stream.reset(); stream.accept(packet(0, .2));
  assert.throws(() => stream.accept({ kind: 'end', names: ['jawDrop'], frames: [{ t: .1, weights: [1] }] }), /Out-of-order/);
  assert.throws(() => stream.accept({ kind: 'end', names: ['jawDrop'], frames: [{ t: .3, weights: [NaN] }] }), /Invalid/);
});
test('pauses both animation and sound during an underrun', () => {
  const ctx = makeContext(), stream = new StreamPlayback(() => ctx);
  stream.accept(packet(0, .2)); stream.accept(packet(.5, .7)); stream.accept(packet(1, 1.2)); stream.tick();
  ctx.currentTime = 2;
  assert.equal(stream.tick().active, false);
});
