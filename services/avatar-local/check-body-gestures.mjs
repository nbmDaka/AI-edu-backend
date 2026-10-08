import assert from 'node:assert/strict';
import test from 'node:test';
import * as THREE from 'three';
import { BodyGestures, gestureEnvelope } from './body-gestures.mjs';

function rig() {
  const model = new THREE.Group();
  for (const side of ['R', 'L']) {
    const arm = new THREE.Bone(), forearm = new THREE.Bone(), hand = new THREE.Bone();
    arm.name = `CC_Base_${side}_Upperarm`; forearm.name = `CC_Base_${side}_Forearm`; hand.name = `CC_Base_${side}_Hand`;
    arm.position.set(side === 'R' ? -.18 : .18, 1.44, 0);
    forearm.position.y = -.27; hand.position.y = -.24;
    arm.add(forearm); forearm.add(hand); model.add(arm);
  }
  const head = new THREE.Bone(); head.name = 'CC_Base_Head'; model.add(head);
  return model;
}
test('envelopes are bounded and have smooth starts/ends', () => {
  for (let t = -1; t < 6; t += .01) assert.ok(gestureEnvelope(t, 3.4) >= 0 && gestureEnvelope(t, 3.4) <= 1);
  assert.equal(gestureEnvelope(0, 3.4), 0);
  assert.equal(gestureEnvelope(3.4, 3.4), 0);
  assert.ok(gestureEnvelope(.001, 3.4) < .00001);
});
test('a wave moves the hand, cancellation releases the arm without a snap', () => {
  const model = rig(), motion = new BodyGestures(model, THREE);
  const arm = model.getObjectByName('CC_Base_R_Upperarm');
  assert.equal(motion.play('wave'), true);
  for (let i = 0; i < 80; i++) motion.update(1 / 60, false);
  const raised = arm.quaternion.clone();
  assert.ok(raised.angleTo(new THREE.Quaternion()) > .5);
  motion.cancel(); motion.update(1 / 60, false);
  assert.ok(raised.angleTo(arm.quaternion) < .06);
  for (let i = 0; i < 35; i++) motion.update(1 / 60, false);
  assert.ok(arm.quaternion.angleTo(new THREE.Quaternion()) < 1e-5);
});
test('pose updates never accumulate and morph weights are untouched', () => {
  const model = rig(), motion = new BodyGestures(model, THREE);
  model.morphTargetInfluences = [.5, .25];
  motion.play('explain');
  for (let i = 0; i < 600; i++) motion.update(1 / 60, false);
  assert.ok(model.getObjectByName('CC_Base_R_Upperarm').quaternion.angleTo(new THREE.Quaternion()) < 1e-5);
  assert.deepEqual(model.morphTargetInfluences, [.5, .25]);
  assert.ok(Object.values(motion.getState().bones).flat().every(Number.isFinite));
});
test('greetings have a cooldown; reduced motion keeps the rest pose', () => {
  const model = rig(), motion = new BodyGestures(model, THREE);
  motion.play('wave'); assert.equal(motion.play('wave'), false);
  const reduced = new BodyGestures(rig(), THREE, { reducedMotion: true });
  assert.equal(reduced.play('wave'), false);
  reduced.update(.05, true);
  assert.equal(reduced.getState().gesture, null);
});

test('disabled hand gestures use a small greeting nod and keep both arms at rest', () => {
  const model = rig(), motion = new BodyGestures(model, THREE, { handGestures: false });
  assert.equal(motion.play('wave'), false);
  assert.equal(motion.play('explain'), false);
  motion.speechStarted('wave');
  assert.equal(motion.getState().gesture, 'nod');
  for (let i = 0; i < 240; i++) {
    motion.update(1 / 60, true);
    for (const side of ['R', 'L']) {
      for (const joint of ['Upperarm', 'Forearm', 'Hand']) {
        assert.ok(model.getObjectByName(`CC_Base_${side}_${joint}`).quaternion.angleTo(new THREE.Quaternion()) < 1e-5);
      }
    }
  }
  motion.cancel();
  assert.equal(motion.getState().gesture, null);
});

test('experimental hand motion never twists the wrist to face the camera', () => {
  const model = rig(), motion = new BodyGestures(model, THREE);
  for (const name of ['wave', 'explain', 'explain']) {
    motion.play(name);
    for (let i = 0; i < 240; i++) {
      motion.update(1 / 60, false);
      for (const side of ['R', 'L']) {
        const wrist = model.getObjectByName(`CC_Base_${side}_Hand`);
        assert.ok(wrist.quaternion.angleTo(new THREE.Quaternion()) <= .041);
      }
    }
  }
});
