# Private Cloud Avatar Pilot

Django remains the only browser entry point. It authenticates the user, checks
access to the current lecture or practice, and supplies authoritative material.
This internal gateway streams text, private Cedar PCM and genuine NVIDIA
Audio2Face Mark 2.3 facial weights. It does not use a hosted avatar platform.

The container publishes no host ports. It creates an SSH tunnel to a dedicated
GPU account that allows only local forwarding to 127.0.0.1:8012, no shell or SFTP.
Pin the GPU host key obtained through a previously authenticated channel.
The SDK worker must bind to loopback and require its separate private stream key.
Never pass the OpenAI or Google Cloud credentials to the browser or GPU worker.

## Explicit Activation

Supply `.env.gateway` privately with `OPENAI_API_KEY`, `API_SHARED_KEY`,
`SPEECH_WORKER_URL`, `A2F_SSH_HOST`, and an absolute `AVATAR_EXPIRES_AT` timestamp.
The speech URL must point to the private voice worker on Docker port 8002.
Mount `secrets/bridge-key`, `secrets/known-hosts` and `secrets/a2f-stream-key` as
read-only files readable only by UID 1000. None belong in Git.

Backend defaults are disabled. Activation requires `AI_AVATAR_ENABLED=true`,
the exact private gateway URL, a user/email allowlist, a future expiry, and a
private GLB key under `platform-assets/avatar/` with its full SHA256 version.
Do not enable Django DEBUG or expose a local-development avatar endpoint.

Use a separate Compose project on `aiedu-platform`; preserve the main deployment.
This pilot permits one active stream and 80 reserved requests per UTC day.
Reservations survive container restarts. The voice worker separately enforces
its durable character quota. These limits and the expiry are conservative
resource guards, not an exact billing cap. The GPU instance requires its own
automatic stop/delete deadline and daily operating-time budget.

The entrypoint shuts down at expiry and does not restart automatically. Revoking
the backend flag or allowlist disables new requests. Roll back only these flags,
then stop this separate Compose project. Never remove platform database volumes.

## Verification

`npm ci --ignore-scripts`, then `node --test gateway.test.cjs` uses mock PCM and
native frames, without provider calls. Real GPU inference, authenticated platform
playback, microphone permission and the character's appearance require separate
live checks. The neural SDK worker source is in `../avatar-sdk`.

This is streamed text plus phrase-level TTS with microphone VAD and interruption,
not an OpenAI Realtime speech-to-speech session. It is a bounded pilot, not a
multi-user GPU scheduler. Character assets, SDK binaries, model weights and
credentials are deliberately not distributed in this repository.
