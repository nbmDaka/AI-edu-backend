import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { validateClip, sampleWeights, retargetWeights, SpeechSmoother } from './a2f-timeline.mjs';
import { StreamPlayback } from './stream-playback.mjs';
import { BodyGestures } from './body-gestures.mjs';

const embedded = new URLSearchParams(location.search).get('embed') === '1';
const parentOrigin = new URLSearchParams(location.search).get('parent');
const trustedParent = embedded && ['http://localhost:5173', 'http://127.0.0.1:5173'].includes(parentOrigin);
const streamPlayback = new StreamPlayback(() => audioContext);

const stage = document.querySelector('#stage');
const status = document.querySelector('#status');
const audioStatus = document.querySelector('#audioStatus');
const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1;
stage.append(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color('#e2e8e6');
const environment = new RoomEnvironment();
const pmrem = new THREE.PMREMGenerator(renderer);
const environmentTarget = pmrem.fromScene(environment, .04);
scene.environment = environmentTarget.texture;
scene.environmentIntensity = .25;
environment.dispose();
pmrem.dispose();
const camera = new THREE.PerspectiveCamera(35, 1, .01, 30);
scene.add(new THREE.HemisphereLight('#ffffff', '#7a817d', .6));
for (const [position, intensity, color] of [[[-2,3,4],2.2,'#fff7ee'],[[3,2,2],.6,'#eaf2ff'],[[0,3,-2],1.2,'#ffffff']]) {
  const light = new THREE.DirectionalLight(color, intensity);
  light.position.set(...position);
  scene.add(light);
}
const meshes = [];
let ready = false, audioContext, audioSource, speaking = false, jaw = 0;
let disposed = false;
let clip = null, audioStarted = 0, request = null, audioTime = 0;
let speechMorphs = {};
const mapping = await fetch('/a2f-cc-map.json').then(response => response.json());
const speechMotion = new SpeechSmoother(Object.values(mapping).flatMap(rule => rule.targets));
let manualJaw = 0;
let audioGeneration = 0;
let bodyMotion, pendingGesture = 'explain', wasSpeaking = false, bodySpeechStarted = false;
let nextBlink = 2.8, blinkStart = -10, blinkCycle = 0;
const blinkIntervals = [4.1, 3.3, 5.6, 3.8];
const values = { jaw: 0, smile: 0, viseme: '' };
const morphNames = new Set();
function setMorph(name, value) {
  for (const mesh of meshes) {
    const index = mesh.morphTargetDictionary?.[name];
    if (index !== undefined) mesh.morphTargetInfluences[index] = value;
  }
}
function resetCamera() {
  camera.position.set(0, 1.56, .95);
  camera.lookAt(0, 1.53, 0);
}
function fail(error) {
  if (disposed) return;
  const element = document.querySelector('#error');
  element.style.display = 'block';
  element.textContent = `Ошибка: ${error.message || error}`;
  status.textContent = 'Ошибка загрузки';
  console.error(error);
}
function tuneEye(material) {
  material.roughness = .3;
  material.specularIntensity = .5;
  material.specularColor?.set('#ffffff');
  // CC's iris/pupil controls are not represented by its generic FBX material.
  material.onBeforeCompile = shader => {
    shader.fragmentShader = shader.fragmentShader.replace('#include <map_fragment>', `
      #include <map_fragment>
      #ifdef USE_MAP
        float eyeRadius = distance(vMapUv, vec2(0.5));
        float irisMask = 1.0 - smoothstep(0.125, 0.15, eyeRadius);
        float pupilMask = 1.0 - smoothstep(0.041, 0.052, eyeRadius);
        diffuseColor.rgb *= mix(vec3(0.85), vec3(0.22, 0.29, 0.37), irisMask);
        diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.008, 0.01, 0.014), pupilMask);
      #endif
    `);
  };
  material.customProgramCacheKey = () => 'kevin-eye-v2';
}
new GLTFLoader().load('/kevin.glb', gltf => {
  if (disposed) return;
  const model = gltf.scene;
  const box = new THREE.Box3().setFromObject(model);
  const height = box.max.y - box.min.y;
  model.scale.multiplyScalar(1.8 / height);
  model.updateMatrixWorld(true);
  const bounds = new THREE.Box3().setFromObject(model);
  model.position.y -= bounds.min.y;
  model.position.x -= (bounds.min.x + bounds.max.x) / 2;
  model.updateMatrixWorld(true);
  // Relax the imported A-pose without changing the original rig or source asset.
  for (const side of ['L', 'R']) {
    const arm = model.getObjectByName(`CC_Base_${side}_Upperarm`);
    const forearm = model.getObjectByName(`CC_Base_${side}_Forearm`);
    if (!arm || !forearm) continue;
    const direction = forearm.getWorldPosition(new THREE.Vector3()).sub(arm.getWorldPosition(new THREE.Vector3())).normalize();
    const delta = new THREE.Quaternion().setFromUnitVectors(direction, new THREE.Vector3(side === 'L' ? .12 : -.12, -1, .05).normalize());
    const parent = arm.parent.getWorldQuaternion(new THREE.Quaternion());
    arm.quaternion.premultiply(parent.clone().invert().multiply(delta).multiply(parent));
    model.updateMatrixWorld(true);
  }
  model.traverse(obj => {
    if (!obj.isMesh) return;
    obj.frustumCulled = false;
    meshes.push(obj);
    for (const name of Object.keys(obj.morphTargetDictionary || {})) morphNames.add(name);
    for (const material of [obj.material].flat()) {
      if (/Transparency|Eyelash/.test(material.name)) {
        material.alphaTest = .01;
        material.transparent = true;
        material.depthWrite = false;
        material.forceSinglePass = true;
        material.side = THREE.DoubleSide;
      }
      if (material.name === 'Hair_Transparency') {
        material.color.set('#33251d');
        material.roughness = .7;
        material.metalness = 0;
        material.alphaTest = .01;
        material.alphaToCoverage = false;
        material.transparent = true;
        material.depthWrite = false;
      }
      if (material.name === 'Scalp_Transparency') material.visible = false;
      if (/^Std_(Eye_[LR]|Cornea_[LR])$/.test(material.name)) tuneEye(material);
      if (/^Std_Skin_/.test(material.name)) {
        material.roughness = .62;
        material.specularIntensity = .35;
        material.specularColor?.set('#ffffff');
        material.normalScale.set(.6, .6);
      }
      if (/Tearline|Occlusion/.test(material.name)) material.depthWrite = false;
      if (/Tongue|Teeth/.test(material.name)) { material.transparent = false; material.opacity = 1; }
    }
  });
  scene.add(model);
  bodyMotion = new BodyGestures(model, THREE, {
    reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
    // Hand poses stay off until a reviewed animation clip replaces the rejected procedural wave.
    handGestures: false,
  });
  resetCamera();
  const select = document.querySelector('#viseme');
  const labels = {
    V_Open: 'А / Э', V_Explosive: 'Б / П / М', V_Dental_Lip: 'Ф / В',
    V_Tight_O: 'О / У', V_Tight: 'С / З', V_Wide: 'И', V_Affricate: 'Ч / Ж',
    V_Lip_Open: 'Раскрытые губы', V_Tongue_up: 'Язык вверх', V_Tongue_Raise: 'Подъём языка',
    V_Tongue_Out: 'Язык вперёд', V_Tongue_Narrow: 'Узкий язык', V_Tongue_Lower: 'Язык вниз',
    V_Tongue_Curl_U: 'Кончик языка вверх', V_Tongue_Curl_D: 'Кончик языка вниз',
  };
  for (const name of [...morphNames].filter(n => n.startsWith('V_') && n !== 'V_None')) {
    select.add(new Option(labels[name] || name, name));
  }
  ready = true;
  const missing = Object.values(mapping).flatMap(rule => rule.targets).filter(name => !morphNames.has(name));
  if (missing.length) { ready = false; fail(new Error(`Нет морфов: ${missing.join(', ')}`)); return; }
  status.textContent = 'Модель готова';
  if (trustedParent) parent.postMessage({ type: 'kevin-ready' }, parentOrigin);
  document.querySelector('#play').disabled = false;
  window.avatarTest = {
    renderer, model, meshes, camera, scene,
    getState: () => ({ ready, speaking, jaw, audioTime, speechMorphs, speechBlend: speechMotion.blend, engine: clip?.engine,
      frames: clip?.weightMat.length || streamPlayback.frames.length, processingMs: clip?.processingMs, cached: clip?.cached,
      streaming: embedded, received: streamPlayback.received, played: streamPlayback.played, underruns: streamPlayback.underruns,
      morphs: [...morphNames], calls: renderer.info.render.calls, triangles: renderer.info.render.triangles, body: bodyMotion.getState() }),
    setMorph,
    playGesture: name => bodyMotion.play(name),
  };
}, progress => {
  if (progress.total) status.textContent = `Загрузка модели ${Math.round(progress.loaded / progress.total * 100)}%`;
}, fail);

function stopAudio() {
  bodyMotion?.cancel();
  bodyMotion?.setState('idle');
  bodySpeechStarted = false;
  pendingGesture = 'explain';
  streamPlayback.reset();
  audioGeneration++;
  request?.abort();
  request = null;
  if (audioSource) { audioSource.onended = null; audioSource.stop(); audioSource.disconnect(); }
  audioSource = null;
  speaking = false;
  clip = null;
  audioTime = 0;
  audioStatus.textContent = 'Готов к воспроизведению';
  document.querySelector('#play').disabled = !ready;
  for (const id of ['jaw', 'smile', 'viseme']) document.getElementById(id).disabled = false;
}
async function pcmWave(buffer) {
  if (buffer.duration < .1 || buffer.duration > 60) throw new Error('Длина записи должна быть от 0,1 до 60 секунд');
  const length = Math.round(buffer.duration * 16000);
  const offline = new OfflineAudioContext(1, length, 16000);
  const source = offline.createBufferSource();
  source.buffer = buffer;
  source.connect(offline.destination);
  source.start();
  const mono = (await offline.startRendering()).getChannelData(0);
  const wave = new ArrayBuffer(44 + length * 2);
  const view = new DataView(wave);
  const word = (offset, value) => { for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i)); };
  word(0, 'RIFF'); view.setUint32(4, wave.byteLength - 8, true); word(8, 'WAVE');
  word(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true); view.setUint32(28, 32000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  word(36, 'data'); view.setUint32(40, length * 2, true);
  for (let i = 0; i < length; i++) {
    const sample = Math.max(-1, Math.min(1, mono[i]));
    view.setInt16(44 + i * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
  }
  return wave;
}
async function playAudio(file) {
  stopAudio();
  const generation = audioGeneration;
  document.querySelector('#play').disabled = true;
  for (const id of ['jaw', 'smile', 'viseme']) document.getElementById(id).disabled = true;
  audioStatus.textContent = 'Подготовка записи...';
  try {
    if (file?.size > 25 * 1024 * 1024) throw new Error('Файл больше 25 МБ');
    request = new AbortController();
    const signal = request.signal;
    audioContext ||= new AudioContext();
    await audioContext.resume();
    const data = file ? await file.arrayBuffer() : await fetch('/sample.wav', { signal }).then(r => { if (!r.ok) throw new Error('Нет пробной записи'); return r.arrayBuffer(); });
    const buffer = await audioContext.decodeAudioData(data);
    if (generation !== audioGeneration) return;
    const wave = await pcmWave(buffer);
    if (generation !== audioGeneration) return;
    audioStatus.textContent = 'Audio2Face обрабатывает запись...';
    const response = await fetch('/api/animation', { method: 'POST', headers: { 'Content-Type': 'audio/wav' }, body: wave, signal });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Ошибка Audio2Face');
    const timeline = validateClip(result);
    // Play exactly the PCM that was analyzed, not the original codec's time base.
    const playback = await audioContext.decodeAudioData(wave.slice(0));
    if (Math.abs(playback.duration - timeline.duration) > .002) throw new Error('Длительность анимации не совпала со звуком');
    if (generation !== audioGeneration) return;
    await audioContext.resume();
    if (generation !== audioGeneration) return;
    audioSource = audioContext.createBufferSource();
    audioSource.buffer = playback;
    audioSource.connect(audioContext.destination);
    audioSource.onended = () => {
      audioSource?.disconnect(); audioSource = null; speaking = false;
      audioStatus.textContent = 'Запись завершена'; document.querySelector('#play').disabled = false;
      for (const id of ['jaw', 'smile', 'viseme']) document.getElementById(id).disabled = false;
    };
    clip = timeline;
    request = null;
    audioStarted = audioContext.currentTime + .06;
    speaking = true;
    audioSource.start(audioStarted);
    audioStatus.textContent = 'Воспроизведение · Audio2Face';
  } catch (error) {
    if (generation !== audioGeneration) return;
    audioStatus.textContent = `Ошибка аудио: ${error.message}`;
    document.querySelector('#play').disabled = !ready;
    for (const id of ['jaw', 'smile', 'viseme']) document.getElementById(id).disabled = false;
  }
}
document.querySelector('#play').onclick = () => playAudio(document.querySelector('#file').files[0]);
document.querySelector('#stop').onclick = stopAudio;
document.querySelector('#file').onchange = () => { stopAudio(); document.querySelector('#play').textContent = document.querySelector('#file').files.length ? 'Воспроизвести' : 'Пробная запись'; };
document.querySelector('#jaw').oninput = event => { values.jaw = +event.target.value; };
document.querySelector('#smile').oninput = event => { values.smile = +event.target.value; };
document.querySelector('#viseme').onchange = event => { if (values.viseme) setMorph(values.viseme, 0); values.viseme = event.target.value; };
document.querySelector('#reset').onclick = () => {
  stopAudio();
  speechMotion.reset();
  manualJaw = jaw = 0;
  values.jaw = values.smile = 0;
  values.viseme = '';
  for (const mesh of meshes) mesh.morphTargetInfluences?.fill(0);
  document.querySelector('#jaw').value = document.querySelector('#smile').value = 0;
  document.querySelector('#viseme').value = '';
  resetCamera();
};
new ResizeObserver(() => {
  const { width, height } = stage.getBoundingClientRect();
  if (width <= 0 || height <= 0) return;
  renderer.setSize(width, height);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
}).observe(stage);
const started = performance.now();
let previousTime = 0;
renderer.setAnimationLoop(() => {
  const time = (performance.now() - started) / 1000;
  const dt = Math.min(.1, Math.max(0, time - previousTime));
  previousTime = time;
  if (ready) {
    const streamed = embedded ? streamPlayback.tick() : null;
    if (trustedParent && streamPlayback.ended && !streamPlayback.sources.size && !streamPlayback.segments.length && streamPlayback.played > 0) {
      streamPlayback.ended = false;
      bodyMotion.cancel();
      bodySpeechStarted = false;
      parent.postMessage({ type: 'kevin-idle' }, parentOrigin);
    }
    if (streamed) speaking = streamed.active;
    if (speaking && !bodySpeechStarted) { bodyMotion.speechStarted(pendingGesture); bodySpeechStarted = true; }
    // A streaming buffer pause is not the end of a conversational gesture.
    if (!embedded && !speaking && wasSpeaking) { bodyMotion.cancel(); bodySpeechStarted = false; }
    wasSpeaking = speaking;
    bodyMotion.update(dt, speaking);
    const timestamp = audioContext?.getOutputTimestamp?.();
    const outputTime = timestamp?.performanceTime > 0
      ? timestamp.contextTime + Math.max(0, (performance.now() - timestamp.performanceTime) / 1000)
      : Math.max(0, (audioContext?.currentTime || 0) - (audioContext?.outputLatency || 0));
    // Device timestamps may recalibrate after resume; never replay mouth frames.
    audioTime = streamed ? streamed.time : speaking ? Math.max(audioTime, Math.min(clip.duration, Math.max(0, outputTime - audioStarted))) : 0;
    const target = retargetWeights(streamed ? streamed.weights : speaking && clip ? sampleWeights(clip, audioTime) : {}, mapping);
    speechMorphs = speechMotion.update(target, speaking, audioTime, streamed?.duration || clip?.duration || 0, dt);
    for (const [name, value] of Object.entries(speechMorphs)) setMorph(name, value);
    manualJaw += (values.jaw - manualJaw) * (1 - Math.exp(-dt / .07));
    jaw = speechMorphs.Jaw_Open + manualJaw * (1 - speechMotion.blend);
    for (const mesh of meshes) {
      const morphs = mesh.morphTargetDictionary || {};
      // Combined CC mouth shapes must not be applied alongside Jaw_Open.
      const merged = Object.keys(morphs).find(name => name.endsWith('Merged_Open_Mouth'));
      const index = morphs[merged] ?? morphs.Jaw_Open;
      if (merged && morphs.Jaw_Open !== undefined) mesh.morphTargetInfluences[morphs.Jaw_Open] = 0;
      if (index !== undefined) mesh.morphTargetInfluences[index] = jaw;
    }
    const idleSmile = (.16 + values.smile * .55) * (1 - speechMotion.blend);
    setMorph('Mouth_Smile_L', idleSmile + speechMorphs.Mouth_Smile_L);
    setMorph('Mouth_Smile_R', idleSmile + speechMorphs.Mouth_Smile_R);
    setMorph('Eye_Squint_L', .16);
    setMorph('Eye_Squint_R', .16);
    if (time >= nextBlink) {
      blinkStart = time;
      nextBlink = time + blinkIntervals[blinkCycle++ % blinkIntervals.length];
    }
    const phase = time - blinkStart;
    const blink = document.querySelector('#blink').checked && phase < .24
      ? (phase < .085 ? phase / .085 : 1 - (phase - .085) / .155) : 0;
    setMorph('Eye_Blink_L', .12 + .88 * blink);
    setMorph('Eye_Blink_R', .12 + .88 * blink);
    if (values.viseme) setMorph(values.viseme, .7 * (1 - speechMotion.blend));
  }
  renderer.render(scene, camera);
});
addEventListener('pagehide', () => { disposed = true; stopAudio(); audioContext?.close(); renderer.dispose(); });
async function updateEngineStatus() {
  try {
    const state = await fetch('/api/status').then(response => response.json());
    document.querySelector('#engineStatus').textContent = state.ready ? (state.busy ? 'Audio2Face занят' : 'Audio2Face готов') : 'Audio2Face не готов';
  } catch { document.querySelector('#engineStatus').textContent = 'Нет связи с Audio2Face'; }
}
if (!embedded) updateEngineStatus();
const engineTimer = embedded ? null : setInterval(updateEngineStatus, 10000);
addEventListener('pagehide', () => clearInterval(engineTimer));
addEventListener('message', async event => {
  if (!trustedParent || event.source !== parent || event.origin !== parentOrigin) return;
  try {
    if (event.data?.type === 'kevin-unlock') { audioContext ||= new AudioContext(); await audioContext.resume(); }
    if (event.data?.type === 'kevin-stop') stopAudio();
    if (event.data?.type === 'kevin-gesture' && ['wave', 'explain'].includes(event.data.name)) pendingGesture = event.data.name;
    if (event.data?.type === 'kevin-state') bodyMotion?.setState(event.data.state);
    if (event.data?.type === 'kevin-stream') {
      if (event.data.data?.kind === 'start') bodySpeechStarted = false;
      streamPlayback.accept(event.data.data);
    }
  } catch (error) { stopAudio(); parent.postMessage({ type: 'kevin-error', message: error.message }, parentOrigin); }
});
