export class StreamPlayback {
  constructor(getContext) {
    this.getContext = getContext;
    this.reset();
  }
  reset() {
    for (const source of this.sources || []) { source.onended = null; try { source.stop(); } catch {} source.disconnect(); }
    this.sources = new Set(); this.segments = []; this.frames = []; this.names = [];
    this.ranges = []; this.duration = 0; this.ended = false; this.received = 0; this.played = 0; this.underruns = 0;
  }
  accept(data) {
    if (data.kind === 'start') { this.reset(); return; }
    if (data.sampleRate !== undefined && data.sampleRate !== 24000) throw new Error('Invalid PCM sample rate');
    if (data.names?.length) this.names = data.names;
    if (data.frames) {
      for (const frame of data.frames) {
        if (!Number.isFinite(frame.t) || frame.weights.length !== this.names.length || !frame.weights.every(Number.isFinite)) throw new Error('Invalid NVIDIA frame');
        if (this.frames.length && frame.t <= this.frames.at(-1).t) throw new Error('Out-of-order NVIDIA frames');
        this.frames.push(frame);
      }
    }
    if (data.kind === 'segment') {
      const raw = atob(data.pcm);
      if (raw.length % 2 || raw.length > 48000 || !Number.isFinite(data.offset)) throw new Error('Invalid PCM');
      if (Math.abs(data.offset - this.received) > .001) throw new Error('Out-of-order audio');
      const samples = new Float32Array(raw.length / 2);
      for (let i = 0; i < samples.length; i++) { const v = raw.charCodeAt(i * 2) | (raw.charCodeAt(i * 2 + 1) << 8); samples[i] = (v >= 32768 ? v - 65536 : v) / 32768; }
      this.segments.push({ offset: data.offset, samples }); this.received += samples.length / 24000;
    }
    if (data.kind === 'end') { this.duration = data.duration; this.ended = true; }
  }
  tick() {
    const context = this.getContext();
    if (!context || context.state !== 'running') return { active: false, time: this.played, weights: {}, duration: this.duration || 90 };
    const safeEnd = this.ended ? this.duration : Math.max(0, (this.frames.at(-1)?.t || 0) - .04);
    while (this.segments.length) {
      const part = this.segments[0]; const length = part.samples.length / 24000;
      if (part.offset + length > safeEnd + .001) break;
      const last = this.ranges.at(-1);
      const running = last && last.end > context.currentTime + .03;
      // Rebuffer on a shared sound/animation clock, never animate missing audio.
      if (!running && !this.ended && safeEnd - part.offset < 1) break;
      if (!running && last) this.underruns++;
      const start = running ? last.end : context.currentTime + .12;
      const buffer = context.createBuffer(1, part.samples.length, 24000); buffer.copyToChannel(part.samples, 0);
      const source = context.createBufferSource(); source.buffer = buffer; source.connect(context.destination);
      this.sources.add(source); source.onended = () => { this.sources.delete(source); source.disconnect(); };
      source.start(start); this.ranges.push({ start, end: start + length, offset: part.offset });
      this.segments.shift();
    }
    const stamp = context.getOutputTimestamp?.();
    const now = stamp?.performanceTime > 0 ? stamp.contextTime + Math.max(0, (performance.now() - stamp.performanceTime) / 1000) : context.currentTime - (context.outputLatency || 0);
    const range = this.ranges.find(r => now >= r.start && now < r.end);
    if (!range) return { active: false, time: this.played, weights: {}, duration: this.duration || 90 };
    this.played = Math.max(this.played, range.offset + now - range.start);
    let index = this.frames.findIndex(f => f.t > this.played);
    if (index < 0) index = this.frames.length - 1;
    const a = this.frames[Math.max(0, index - 1)], b = this.frames[index];
    const mix = a && b && b.t > a.t ? Math.max(0, Math.min(1, (this.played - a.t) / (b.t - a.t))) : 0;
    return { active: true, time: this.played, duration: this.duration || 90,
      weights: Object.fromEntries(this.names.map((name, i) => [name, a ? a.weights[i] * (1 - mix) + (b?.weights[i] || 0) * mix : 0])) };
  }
}
