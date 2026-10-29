# Cedar Speech Worker

Temporary production adapter for the existing authenticated Django -> AI gateway
speech endpoint. No browser key, public port, GPU, avatar platform API, or student
database change is needed. The fixed voice is Cedar. Raw OpenAI PCM is wrapped in
a valid mono 24 kHz PCM16 WAV with real RIFF/data lengths.

The private avatar gateway can request `response_format: "pcm"` to receive
24 kHz PCM16 chunks as soon as the provider emits them. This shares exactly the
same authorization, durable character quota and concurrency limit as WAV.

The existing gateway splits responses into chunks of at most 800 characters.
This worker accepts up to 1000 characters, two concurrent requests, 30 requests
per minute and 50000 reserved characters per UTC day by default. Daily reservations
are durable in the named volume; failures remain reserved conservatively. This
is an input-volume guard, not an exact dollar spending cap or provider invoice.
Only the bounded quota counters are stored, not speech text or audio.

The private route token is generated on the server and stored in `.env.worker`.
The gateway's server-only `LOCAL_TTS_URL` contains the token. Health responses and
logs must never expose this URL. There is no published host port. Requests still
enter through Django's session, lecture/practice access checks, CSRF and throttle.

Deploy this as a separate Compose project on the platform's internal network.
Set `TUTOR_ENV_FILE=/opt/ai-edu/backend/.env.ai-tutor` for production. Preserve the
prior `LOCAL_TTS_URL` before changing it; rollback only that key and recreate only
the ai-tutor container, then stop this separate worker if no longer needed.
Never run `docker compose down -v` against the platform.

Important: this reuses the project's existing Speech API model for the urgent
voice rollout. OpenAI has announced its shutdown on 2027-01-06. Replace this
adapter with the Realtime API before that date; `gpt-realtime-2.1-mini` cannot be
used by just changing the Speech API model field. See
https://developers.openai.com/api/docs/guides/text-to-speech .

This worker supplies speech only. It does not claim that MetaHuman rigging,
web export, neural lip sync, or cloud GPU deployment is complete.
