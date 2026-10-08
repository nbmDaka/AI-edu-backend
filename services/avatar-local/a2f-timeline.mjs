export function validateClip(clip) {
  if (clip?.engine !== 'nvidia-audio2face-2023.2' || clip.fps !== 30 ||
      !Number.isFinite(clip.duration) || clip.duration <= 0 || clip.duration > 60 ||
      !Array.isArray(clip.facsNames) || !clip.facsNames.length ||
      !clip.facsNames.every(name => typeof name === 'string') ||
      new Set(clip.facsNames).size !== clip.facsNames.length ||
      !Array.isArray(clip.weightMat) || clip.weightMat.length < 2 ||
      Math.abs(clip.weightMat.length - clip.duration * clip.fps) > 2 ||
      !clip.weightMat.every(row => Array.isArray(row) && row.length === clip.facsNames.length && row.every(Number.isFinite))) {
    throw new Error('Invalid Audio2Face timeline');
  }
  return clip;
}

export function sampleWeights(clip, seconds) {
  const frame = Math.max(0, Math.min(clip.weightMat.length - 1, seconds * clip.fps));
  const index = Math.floor(frame);
  const next = Math.min(index + 1, clip.weightMat.length - 1);
  const mix = frame - index;
  return Object.fromEntries(clip.facsNames.map((name, column) => [name,
    clip.weightMat[index][column] * (1 - mix) + clip.weightMat[next][column] * mix,
  ]));
}

// Anatomical retargeting from Mark's FACS solver, not a Kevin-trained solver.
// Keep gaze/blinking independent; the skin-only diagnostic omits eye/tongue solves.
export function retargetWeights(weights, mapping) {
  const result = {};
  for (const [channel, { targets, gain }] of Object.entries(mapping)) {
    const value = Math.max(0, Math.min(.65, (weights[channel] || 0) * gain));
    for (const target of targets) result[target] = value;
  }
  return result;
}

const smoothStep = (edge, value) => {
  const t = Math.max(0, Math.min(1, value / edge));
  return t * t * (3 - 2 * t);
};

export class SpeechSmoother {
  constructor(names) {
    this.names = [...new Set(names)];
    this.reset();
  }

  reset() {
    this.blend = 0;
    this.target = {};
    this.values = Object.fromEntries(this.names.map(name => [name, 0]));
  }

  update(target, active, seconds, duration, dt) {
    if (active) this.target = target;
    const envelope = active ? smoothStep(.12, seconds) * smoothStep(.18, duration - seconds) : 0;
    const blendAlpha = 1 - Math.exp(-dt / (active ? .045 : .095));
    this.blend += (envelope - this.blend) * blendAlpha;
    if (!active && this.blend < .0001) this.reset();
    const result = {};
    for (const name of this.names) {
      // Mouth closure needs a faster response than rounded vowel shapes.
      const tau = name === 'Jaw_Open' || name.startsWith('Mouth_Press') ? .035 : .055;
      const alpha = 1 - Math.exp(-dt / tau);
      this.values[name] += ((this.target[name] || 0) - this.values[name]) * alpha;
      result[name] = this.values[name] * this.blend;
    }
    return result;
  }
}
