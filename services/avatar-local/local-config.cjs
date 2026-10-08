const path = require('node:path');

if (process.env.NODE_ENV === 'production') throw new Error('The avatar prototype is local-only, not a production deployment.');
const stateRoot = path.resolve(process.env.AVATAR_STATE_DIR || path.join(__dirname, '.local'));
const streamKey = path.join(stateRoot, 'stream', 'key.txt');
const clips = path.join(stateRoot, 'clips');
const model = process.env.AVATAR_MODEL_PATH ? path.resolve(process.env.AVATAR_MODEL_PATH) : null;
const sample = process.env.AVATAR_SAMPLE_AUDIO ? path.resolve(process.env.AVATAR_SAMPLE_AUDIO) : null;

module.exports = { stateRoot, streamKey, clips, model, sample };
