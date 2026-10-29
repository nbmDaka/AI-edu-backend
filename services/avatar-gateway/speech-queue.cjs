const sentences = new Intl.Segmenter('ru', { granularity: 'sentence' });

class SpokenText {
  constructor(emit) { this.emit = emit; this.pending = ''; }
  push(text, final = false) {
    this.pending += text;
    const parts = [...sentences.segment(this.pending)].map(part => part.segment);
    this.pending = final ? '' : parts.pop() || '';
    for (const part of parts) this.emitBounded(part);
  }
  emitBounded(text) {
    text = text.trim();
    while (text.length > 1000) {
      // Preserve sentences normally; split an oversized one at a clause or word.
      const clauses = [...text.slice(0, 1000).matchAll(/[,;:]\s/gu)];
      const clause = clauses.at(-1)?.index;
      const boundary = clause !== undefined && clause >= 300 ? clause + 1 : text.lastIndexOf(' ', 1000);
      const end = boundary > 0 ? boundary : 1000;
      this.emit(text.slice(0, end).trim());
      text = text.slice(end).trimStart();
    }
    if (text) this.emit(text);
  }
}

function createSpeechQueue(fetchSpeech, signal) {
  const jobs = [];
  let finished = false, active = 0, wake;
  const notify = () => { wake?.(); wake = undefined; };
  const prime = () => {
    if (signal.aborted) return;
    for (const job of jobs) {
      if (active >= 2) break;
      if (job.response) continue;
      active++;
      job.response = Promise.resolve().then(() => fetchSpeech(job.text));
      // The second request may fail before the first is consumed.
      job.response.catch(() => {});
    }
  };
  signal.addEventListener('abort', notify, { once: true });
  return {
    push(text) { signal.throwIfAborted(); jobs.push({ text }); prime(); notify(); },
    finish() { finished = true; notify(); },
    async *responses() {
      while (!finished || jobs.length) {
        signal.throwIfAborted();
        if (!jobs.length) { await new Promise(resolve => { wake = resolve; }); continue; }
        const job = jobs[0];
        try { yield await job.response; }
        finally { jobs.shift(); active--; prime(); }
      }
    },
    close() {
      signal.removeEventListener('abort', notify);
      for (const job of jobs) job.response?.then(response => response.body?.cancel()).catch(() => {});
    },
  };
}

module.exports = { SpokenText, createSpeechQueue };
