export const gestureDurations = { wave: 3.4, nod: 1.8, explain: 3.6, look: 2.8 };
const smooth = t => { t = Math.max(0, Math.min(1, t)); return t * t * (3 - 2 * t); };
export function gestureEnvelope(time, duration, attack = .7, release = .7) {
  return smooth(time / attack) * smooth((duration - time) / release);
}

export class BodyGestures {
  constructor(model, THREE, { reducedMotion = false, handGestures = true } = {}) {
    this.model = model;
    this.T = THREE;
    this.reducedMotion = reducedMotion;
    this.handGestures = handGestures;
    this.state = 'idle';
    this.stateBlend = 0;
    this.time = 0;
    this.active = null;
    this.nextExplanation = 0;
    this.explanationSide = 0;
    this.lastWave = -100;
    this.bones = new Map();
    for (const name of ['Head', 'NeckTwist01', 'Spine02', ...['R', 'L'].flatMap(s => [`${s}_Upperarm`, `${s}_Forearm`, `${s}_Hand`, `${s}_ForearmTwist01`, `${s}_ForearmTwist02`])]) {
      const bone = model.getObjectByName(`CC_Base_${name}`);
      if (bone) this.bones.set(name, { bone, rest: bone.quaternion.clone() });
    }
    this.vector = new THREE.Vector3();
    this.parentRotation = new THREE.Quaternion();
    this.delta = new THREE.Quaternion();
    this.targetRotation = new THREE.Quaternion();
    this.euler = new THREE.Euler();
  }
  setState(state) {
    if (!['idle', 'listening', 'thinking'].includes(state)) return;
    if (state === 'listening' && this.state !== 'listening' && !this.active) this.play('nod');
    this.state = state;
  }
  play(name) {
    if (this.reducedMotion || !Object.hasOwn(gestureDurations, name)) return false;
    if (!this.handGestures && (name === 'wave' || name === 'explain')) return false;
    if (name === 'wave' && this.time - this.lastWave < 5) return false;
    if (name === 'wave') this.lastWave = this.time;
    this.active = { name, start: this.time, duration: gestureDurations[name], side: this.explanationSide++ % 2 ? 'L' : 'R', release: null };
    return true;
  }
  cancel() {
    if (this.active && this.active.release === null) this.active.release = this.time;
    this.nextExplanation = this.time + 4;
  }
  speechStarted(gesture = 'explain') {
    this.state = 'idle';
    this.play(gesture === 'wave' ? (this.handGestures ? 'wave' : 'nod') : 'explain');
    this.nextExplanation = this.time + 7.5;
  }
  rotate(name, x, y, z) {
    const entry = this.bones.get(name);
    if (!entry) return;
    entry.bone.parent.getWorldQuaternion(this.parentRotation);
    this.delta.setFromEuler(this.euler.set(x, y, z));
    this.targetRotation.copy(this.parentRotation).invert().multiply(this.delta).multiply(this.parentRotation);
    entry.bone.quaternion.copy(entry.rest).premultiply(this.targetRotation);
    entry.bone.updateWorldMatrix(false, true);
  }
  joint(name, x, y, z) {
    const entry = this.bones.get(name);
    if (!entry) return;
    entry.bone.quaternion.copy(entry.rest).multiply(this.delta.setFromEuler(this.euler.set(x, y, z, 'XYZ')));
  }
  arm(side, lift, spread, flex, pronation, wrist, weight) {
    const sign = side === 'R' ? -1 : 1;
    this.joint(`${side}_Upperarm`, lift * weight, 0, sign * spread * weight);
    // CC's elbow flexes about local X. Keep palm rotation out of the wrist;
    // its sibling twist chain distributes pronation from elbow to hand.
    const twist = -sign * pronation * weight;
    this.joint(`${side}_Forearm`, flex * weight, twist, 0);
    this.joint(`${side}_ForearmTwist01`, 0, -twist, 0);
    this.joint(`${side}_ForearmTwist02`, 0, twist * .5, 0);
    this.joint(`${side}_Hand`, wrist * weight, 0, 0);
  }
  update(dt, speaking) {
    this.time += Math.min(.1, Math.max(0, dt));
    // Restore the saved relaxed pose first so motion never accumulates in the rig.
    for (const { bone, rest } of this.bones.values()) bone.quaternion.copy(rest);
    this.model.updateMatrixWorld(true);
    if (this.reducedMotion) return;
    this.stateBlend += ((this.state === 'thinking' ? 1 : 0) - this.stateBlend) * (1 - Math.exp(-dt / .45));
    if (speaking && !this.active && this.time >= this.nextExplanation) {
      this.play('explain'); this.nextExplanation = this.time + 9;
    }
    let pitch = .006 * Math.sin(this.time * .8), yaw = .009 * Math.sin(this.time * .43), roll = .004 * Math.sin(this.time * .61);
    yaw += this.stateBlend * .11;
    roll -= this.stateBlend * .028;
    let weight = 0;
    const gesture = this.active;
    if (gesture) {
      const age = this.time - gesture.start;
      weight = gestureEnvelope(age, gesture.duration);
      if (gesture.release !== null) weight *= 1 - smooth((this.time - gesture.release) / .45);
      if (age >= gesture.duration || (gesture.release !== null && this.time - gesture.release >= .45)) this.active = null;
      if (gesture.name === 'wave') {
        const oscillation = Math.sin(Math.max(0, age - .8) * 5.5) * .06;
        this.arm('R', .65, .62 + oscillation, 2.2, 1.4, .035, weight);
        pitch += .018 * weight;
      } else if (gesture.name === 'explain') {
        const side = gesture.side;
        const beat = .04 * Math.sin(age * 3);
        this.arm(side, .25, .24, 1.65 + beat, .95, .04, weight);
        pitch += .016 * Math.sin(age * 2.6) * weight;
      } else if (gesture.name === 'nod') {
        pitch += .065 * Math.sin(age * 5) * weight;
      } else if (gesture.name === 'look') {
        yaw += .16 * weight;
        roll -= .016 * weight;
      }
    }
    this.rotate('Head', pitch, yaw, roll);
    this.model.updateMatrixWorld(true);
    this.lastWeight = weight;
  }
  getState() {
    const names = ['Head', 'R_Upperarm', 'R_Forearm', 'R_Hand', 'L_Upperarm', 'L_Forearm'];
    return { gesture: this.active?.name ?? null, age: this.active ? this.time - this.active.start : 0, weight: this.lastWeight ?? 0, state: this.state, handGestures: this.handGestures,
      bones: Object.fromEntries(names.filter(n => this.bones.has(n)).map(n => [n, this.bones.get(n).bone.quaternion.toArray()])) };
  }
}
