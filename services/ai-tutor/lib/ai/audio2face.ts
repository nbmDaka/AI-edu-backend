import { Buffer } from 'buffer';

// 52 standard ARKit blendshape names in order
export const ARKIT_BLENDSHAPE_NAMES = [
  'eyeBlinkLeft', 'eyeLookDownLeft', 'eyeLookInLeft', 'eyeLookOutLeft', 'eyeLookUpLeft', 'eyeSquintLeft', 'eyeWideLeft',
  'eyeBlinkRight', 'eyeLookDownRight', 'eyeLookInRight', 'eyeLookOutRight', 'eyeLookUpRight', 'eyeSquintRight', 'eyeWideRight',
  'jawForward', 'jawLeft', 'jawRight', 'jawOpen',
  'mouthClose', 'mouthFunnel', 'mouthPucker', 'mouthLeft', 'mouthRight',
  'mouthSmileLeft', 'mouthSmileRight', 'mouthFrownLeft', 'mouthFrownRight',
  'mouthDimpleLeft', 'mouthDimpleRight', 'mouthStretchLeft', 'mouthStretchRight',
  'mouthRollLower', 'mouthRollUpper', 'mouthShrugLower', 'mouthShrugUpper',
  'mouthPressLeft', 'mouthPressRight', 'mouthLowerDownLeft', 'mouthLowerDownRight',
  'mouthUpperUpLeft', 'mouthUpperUpRight',
  'browDownLeft', 'browDownRight', 'browInnerUp', 'browOuterUpLeft', 'browOuterUpRight',
  'cheekPuff', 'cheekSquintLeft', 'cheekSquintRight',
  'noseSneerLeft', 'noseSneerRight', 'tongueOut'
];

export interface BlendshapeFrame {
  time: number; // in seconds
  weights: number[]; // 52 weights matching ARKIT_BLENDSHAPE_NAMES
}

export interface Audio2FaceResult {
  blendshapeNames: string[];
  frames: BlendshapeFrame[];
}

/**
 * Parses a WAV file from a Buffer, reads the PCM 16-bit audio data,
 * and computes average amplitudes for 30 FPS frames.
 */
export function generateProceduralBlendshapes(wavBuffer: Buffer): Audio2FaceResult {
  // Parse WAV Header
  // Chunk ID: "RIFF"
  if (wavBuffer.toString('ascii', 0, 4) !== 'RIFF') {
    throw new Error('Not a valid RIFF file');
  }

  // Format: "WAVE"
  if (wavBuffer.toString('ascii', 8, 12) !== 'WAVE') {
    throw new Error('Not a valid WAVE file');
  }

  let sampleRate = 22050;
  let numChannels = 1;
  let bitsPerSample = 16;
  let dataOffset = 44;
  let dataSize = wavBuffer.length - 44;

  // Walk through subchunks to find "fmt " and "data"
  let offset = 12;
  while (offset < wavBuffer.length - 8) {
    const subchunkId = wavBuffer.toString('ascii', offset, offset + 4);
    const subchunkSize = wavBuffer.readUInt32LE(offset + 4);

    if (subchunkId === 'fmt ') {
      numChannels = wavBuffer.readUInt16LE(offset + 8 + 2);
      sampleRate = wavBuffer.readUInt32LE(offset + 8 + 4);
      bitsPerSample = wavBuffer.readUInt16LE(offset + 8 + 14);
    } else if (subchunkId === 'data') {
      dataOffset = offset + 8;
      dataSize = subchunkSize;
      break;
    }

    offset += 8 + subchunkSize;
  }

  // Ensure data size doesn't overflow buffer
  if (dataOffset + dataSize > wavBuffer.length) {
    dataSize = wavBuffer.length - dataOffset;
  }

  // Bits per sample check
  if (bitsPerSample !== 16) {
    // Fallback: if not 16-bit PCM, generate empty timeline based on approximate length
    const approxDuration = wavBuffer.length / 44100; // rough estimation
    return generateDummyTimeline(approxDuration);
  }

  // Read samples (16-bit signed PCM)
  const bytesPerSample = 2;
  const totalSamples = Math.floor(dataSize / (bytesPerSample * numChannels));

  // Audio parameters
  const fps = 30;
  const frameDuration = 1 / fps; // 0.0333s
  const samplesPerFrame = Math.floor(sampleRate * frameDuration);
  const totalFrames = Math.floor(totalSamples / samplesPerFrame);

  const frames: BlendshapeFrame[] = [];

  // Generate frames based on amplitude
  for (let f = 0; f < totalFrames; f++) {
    const startSample = f * samplesPerFrame;
    const endSample = startSample + samplesPerFrame;

    let sumAbsolute = 0;
    let sampleCount = 0;

    for (let s = startSample; s < endSample && s < totalSamples; s++) {
      // For multi-channel, average the channels or just read channel 0
      const sampleIdx = dataOffset + s * bytesPerSample * numChannels;
      if (sampleIdx + 2 <= wavBuffer.length) {
        const val = wavBuffer.readInt16LE(sampleIdx);
        sumAbsolute += Math.abs(val);
        sampleCount++;
      }
    }

    // Average amplitude (0.0 to 1.0)
    const avgAmp = sampleCount > 0 ? (sumAbsolute / sampleCount) / 32768 : 0;

    // Smooth the amplitude slightly to prevent jitter
    const smoothedAmp = Math.min(avgAmp * 3.5, 1.0); // Boost amplitude for visual responsiveness

    // Initialize ARKit weights array (52 zeros)
    const weights = new Array(ARKIT_BLENDSHAPE_NAMES.length).fill(0);

    // Map amplitude to mouth shapes
    // Index mapping in ARKIT_BLENDSHAPE_NAMES:
    // jawOpen (17), mouthFunnel (19), mouthPucker (20), mouthSmileLeft (23), mouthSmileRight (24), mouthRollLower (31)
    const jawOpenIdx = ARKIT_BLENDSHAPE_NAMES.indexOf('jawOpen');
    const mouthFunnelIdx = ARKIT_BLENDSHAPE_NAMES.indexOf('mouthFunnel');
    const mouthPuckerIdx = ARKIT_BLENDSHAPE_NAMES.indexOf('mouthPucker');
    const mouthSmileLeftIdx = ARKIT_BLENDSHAPE_NAMES.indexOf('mouthSmileLeft');
    const mouthSmileRightIdx = ARKIT_BLENDSHAPE_NAMES.indexOf('mouthSmileRight');
    const browInnerUpIdx = ARKIT_BLENDSHAPE_NAMES.indexOf('browInnerUp');

    if (jawOpenIdx !== -1) weights[jawOpenIdx] = smoothedAmp * 0.75;
    if (mouthFunnelIdx !== -1) weights[mouthFunnelIdx] = smoothedAmp * 0.25;
    if (mouthPuckerIdx !== -1) weights[mouthPuckerIdx] = smoothedAmp * 0.15;

    // Add natural subtle smile
    if (mouthSmileLeftIdx !== -1) weights[mouthSmileLeftIdx] = 0.12;
    if (mouthSmileRightIdx !== -1) weights[mouthSmileRightIdx] = 0.12;

    // Slight brow lift when speaking loudly
    if (browInnerUpIdx !== -1 && smoothedAmp > 0.4) {
      weights[browInnerUpIdx] = (smoothedAmp - 0.4) * 0.3;
    }

    // Procedural Eye Blinking (every ~4 seconds, blink lasts ~100ms / 3 frames)
    const time = f * frameDuration;
    const blinkCycle = 4.0; // seconds
    const blinkDuration = 0.1; // 100ms
    const timeInCycle = time % blinkCycle;

    if (timeInCycle < blinkDuration) {
      const eyeBlinkLeftIdx = ARKIT_BLENDSHAPE_NAMES.indexOf('eyeBlinkLeft');
      const eyeBlinkRightIdx = ARKIT_BLENDSHAPE_NAMES.indexOf('eyeBlinkRight');
      if (eyeBlinkLeftIdx !== -1) weights[eyeBlinkLeftIdx] = 1.0;
      if (eyeBlinkRightIdx !== -1) weights[eyeBlinkRightIdx] = 1.0;
    }

    frames.push({
      time: Number(time.toFixed(3)),
      weights
    });
  }

  // Handle case where audio is extremely short
  if (frames.length === 0) {
    return generateDummyTimeline(0.5);
  }

  return {
    blendshapeNames: ARKIT_BLENDSHAPE_NAMES,
    frames
  };
}

/**
 * Generates an empty/dummy blendshape timeline of a given duration
 */
function generateDummyTimeline(duration: number): Audio2FaceResult {
  const fps = 30;
  const frameDuration = 1 / fps;
  const totalFrames = Math.max(1, Math.floor(duration / frameDuration));
  const frames: BlendshapeFrame[] = [];

  for (let f = 0; f < totalFrames; f++) {
    const time = f * frameDuration;
    const weights = new Array(ARKIT_BLENDSHAPE_NAMES.length).fill(0);

    // Idle smile
    const mouthSmileLeftIdx = ARKIT_BLENDSHAPE_NAMES.indexOf('eyeBlinkLeft'); // just use indexes
    if (mouthSmileLeftIdx !== -1) weights[ARKIT_BLENDSHAPE_NAMES.indexOf('mouthSmileLeft')] = 0.1;
    if (mouthSmileLeftIdx !== -1) weights[ARKIT_BLENDSHAPE_NAMES.indexOf('mouthSmileRight')] = 0.1;

    frames.push({
      time: Number(time.toFixed(3)),
      weights
    });
  }

  return {
    blendshapeNames: ARKIT_BLENDSHAPE_NAMES,
    frames
  };
}

/**
 * Communicates with NVIDIA Audio2Face NIM container if configured.
 * Falls back to procedural generation on failure or if not configured.
 */
export async function getAudio2FaceBlendshapes(wavBuffer: Buffer): Promise<Audio2FaceResult> {
  const a2fUrl = process.env.AUDIO2FACE_URL;

  if (!a2fUrl) {
    console.log('AUDIO2FACE_URL is not set. Using high-quality procedural blendshapes fallback.');
    return generateProceduralBlendshapes(wavBuffer);
  }

  try {
    console.log(`Connecting to NVIDIA Audio2Face NIM at ${a2fUrl}...`);

    // Nvidia NIM A2F 3D HTTP REST payload
    // A2F REST endpoint typically accepts audio in standard multipart form or base64 PCM
    const response = await fetch(a2fUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        audio_data: wavBuffer.toString('base64'),
        audio_format: 'WAV',
        model_name: process.env.AUDIO2FACE_MODEL ?? 'James',
        output_format: 'ARKIT'
      }),
    });

    if (!response.ok) {
      throw new Error(`Audio2Face server returned status ${response.status}`);
    }

    const data = (await response.json()) as {
      blendshape_names: string[];
      frames: { time: number; weights: number[] }[];
    };

    if (data && Array.isArray(data.frames)) {
      console.log(`Successfully received ${data.frames.length} frames from Audio2Face.`);
      return {
        blendshapeNames: data.blendshape_names ?? ARKIT_BLENDSHAPE_NAMES,
        frames: data.frames.map(f => ({
          time: f.time,
          weights: f.weights
        }))
      };
    }

    throw new Error('Invalid response structure from Audio2Face');
  } catch (err) {
    console.warn('Audio2Face NIM integration failed. Falling back to procedural generation.', err);
    return generateProceduralBlendshapes(wavBuffer);
  }
}
