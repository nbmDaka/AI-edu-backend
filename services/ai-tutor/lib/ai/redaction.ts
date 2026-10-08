const REDACTION_PATTERNS: Array<[RegExp, string]> = [
  [/\b(sk-[a-z0-9_-]{16,})\b/gi, '[REDACTED_OPENAI_KEY]'],
  [/\b(bearer\s+[a-z0-9._-]{12,})\b/gi, '[REDACTED_BEARER_TOKEN]'],
  [/\b([a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})\b/gi, '[REDACTED_EMAIL]'],
  [/\b(\+?\d[\d\s().-]{8,}\d)\b/g, '[REDACTED_PHONE]'],
  [/\b\d{12,}\b/g, '[REDACTED_LONG_ID]'],
];

export function redactSensitiveText(input: string): string {
  let result = input;
  for (const [pattern, replacement] of REDACTION_PATTERNS) {
    result = result.replace(pattern, replacement);
  }
  return result;
}

export function redactJson(value: unknown): string {
  try {
    return redactSensitiveText(JSON.stringify(value));
  } catch {
    return '[UNSERIALIZABLE]';
  }
}
