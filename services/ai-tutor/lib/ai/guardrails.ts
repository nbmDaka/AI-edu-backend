import type { GuardrailResult } from '@/lib/ai/types';

const AI_MAX_INPUT_CHARS = Number(process.env.AI_MAX_INPUT_CHARS ?? 1200);
const HISTORY_INJECTION_WINDOW = Number(process.env.AI_PROMPT_INJECTION_HISTORY_WINDOW ?? 4);
const RETRIEVAL_SOFT_BLOCK_THRESHOLD = Number(process.env.AI_RETRIEVAL_GUARDRAIL_SCORE_THRESHOLD ?? 0.82);
const RETRIEVAL_PENALTY_THRESHOLD = Number(process.env.AI_RETRIEVAL_GUARDRAIL_PENALTY_THRESHOLD ?? 0.32);
const OUTPUT_BLOCK_THRESHOLD = Number(process.env.AI_OUTPUT_GUARDRAIL_SCORE_THRESHOLD ?? 0.58);

type PatternRule = {
  id: string;
  regex: RegExp;
  weight: number;
};

type OutputGuardrailResult = {
  blocked: boolean;
  reasons: string[];
  score: number;
  safeText: string;
};

type RetrievalChunkSafety = {
  score: number;
  reasons: string[];
};

const HARD_PROMPT_INJECTION_RULES: PatternRule[] = [
  { id: 'ignore_previous_instructions', regex: /ignore\s+(all\s+)?previous\s+instructions/i, weight: 0.7 },
  { id: 'ignore_system_prompt', regex: /ignore\s+the\s+system\s+prompt/i, weight: 0.72 },
  { id: 'reveal_system_prompt', regex: /reveal\s+(the\s+)?system\s+prompt/i, weight: 0.8 },
  { id: 'developer_message', regex: /developer\s+(message|prompt|instructions?)/i, weight: 0.7 },
  { id: 'act_as_override', regex: /\bact\s+as\s+(?!ai[-\s]?tutor)/i, weight: 0.58 },
  { id: 'bypass_rules', regex: /bypass\s+(the\s+)?(rules|guardrails|restrictions|limits)/i, weight: 0.74 },
  { id: 'system_prompt_ru', regex: /системн[а-яёәіңғүұқөһ]*\s+(промпт|инструкц)/i, weight: 0.64 },
  {
    id: 'ignore_instructions_ru',
    regex: /игнориру(й|йте)\s+(все\s+)?(предыдущ[а-яёәіңғүұқөһ]*\s+)?инструкц/i,
    weight: 0.76,
  },
  { id: 'reveal_prompt_ru', regex: /раскрой\s+(системн[а-яёәіңғүұқөһ]*\s+)?(промпт|инструкц)/i, weight: 0.8 },
  { id: 'bypass_ru', regex: /обойди\s+(ограничени|правила|guardrails)/i, weight: 0.76 },
  { id: 'system_prompt_kk', regex: /жүйелік\s+(промпт|нұсқаулық)/i, weight: 0.64 },
  { id: 'ignore_instructions_kk', regex: /нұсқауларды\s+елеме/i, weight: 0.74 },
  { id: 'bypass_kk', regex: /ережелерді\s+айналып\s+өт/i, weight: 0.76 },
  {
    id: 'obfuscated_ignore',
    regex:
      /i[\W_]*g[\W_]*n[\W_]*o[\W_]*r[\W_]*e[\W_]*(all[\W_]+)?p[\W_]*r[\W_]*e[\W_]*v[\W_]*i[\W_]*o[\W_]*u[\W_]*s[\W_]+i[\W_]*n[\W_]*s[\W_]*t[\W_]*r[\W_]*u[\W_]*c[\W_]*t[\W_]*i[\W_]*o[\W_]*n/i,
    weight: 0.9,
  },
  {
    id: 'obfuscated_system_prompt',
    regex:
      /s[\W_]*y[\W_]*s[\W_]*t[\W_]*e[\W_]*m[\W_]+p[\W_]*r[\W_]*o[\W_]*m[\W_]*p[\W_]*t/i,
    weight: 0.88,
  },
];

const SOFT_PROMPT_INJECTION_RULES: PatternRule[] = [
  { id: 'override_chain', regex: /\b(disregard|override|replace)\b.{0,40}\b(instructions?|rules|policy)\b/i, weight: 0.26 },
  { id: 'reveal_hidden', regex: /\b(reveal|show|print|dump)\b.{0,50}\b(hidden|internal|secret)\b/i, weight: 0.24 },
  { id: 'prompt_leak', regex: /\b(system|developer|hidden|internal)\b.{0,25}\b(prompt|message|policy|instruction)\b/i, weight: 0.24 },
  { id: 'tool_hijack', regex: /\b(call|invoke|use)\b.{0,30}\btool|function\b/i, weight: 0.2 },
  { id: 'output_raw_prompt', regex: /\b(raw|full|exact)\b.{0,25}\b(prompt|instructions?)\b/i, weight: 0.2 },
  { id: 'ru_hidden_instructions', regex: /скрыт[а-яёәіңғүұқөһ]*\s+(инструкц|правил|промпт)/i, weight: 0.24 },
  {
    id: 'ru_reveal_internal',
    regex: /(покажи|выведи|раскрой).{0,35}(внутренн[а-яёәіңғүұқөһ]*|служебн[а-яёәіңғүұқөһ]*)/i,
    weight: 0.24,
  },
  { id: 'ru_ignore_context', regex: /игнориру(й|йте).{0,30}(контекст|материал|лекци)/i, weight: 0.22 },
  { id: 'kk_internal_prompt', regex: /(ішкі|жасырын).{0,35}(нұсқаулық|промпт)/i, weight: 0.22 },
];

const HISTORY_REFERENTIAL_RULES: PatternRule[] = [
  { id: 'follow_previous_request', regex: /(выполни|следуй|сделай).{0,30}(предыдущ|прошл|раньше)/i, weight: 0.28 },
  { id: 'continue_above', regex: /\b(do|follow|continue)\b.{0,25}\b(previous|above|earlier)\b/i, weight: 0.28 },
  { id: 'fulfil_prior_override', regex: /\b(now|теперь)\b.{0,25}\b(do|show|выполни|покажи)\b/i, weight: 0.14 },
];

const UNSAFE_CONTENT_PATTERNS: PatternRule[] = [
  { id: 'bomb_ru', regex: /как\s+сделать\s+бомбу/i, weight: 1 },
  { id: 'bomb_en', regex: /make\s+a\s+bomb/i, weight: 1 },
  { id: 'self_harm_en', regex: /suicide|self-harm/i, weight: 1 },
  { id: 'self_harm_ru', regex: /суицид|самоубийств|самоповрежден/i, weight: 1 },
  { id: 'weapon_kk', regex: /қару|бомба/i, weight: 1 },
];

const OUTPUT_LEAK_RULES: PatternRule[] = [
  { id: 'output_system_prompt', regex: /\b(system prompt|developer message|developer instructions?)\b/i, weight: 0.46 },
  {
    id: 'output_internal_policy_ru',
    regex: /(внутренн[а-яёәіңғүұқөһ]*|служебн[а-яёәіңғүұқөһ]*).{0,35}(инструкц|правил)/i,
    weight: 0.44,
  },
  { id: 'output_untrusted_context_marker', regex: /UNTRUSTED CONTEXT/i, weight: 0.9 },
  { id: 'output_tool_listing', regex: /\b(tool|function)\b.{0,35}\bavailable|allowed|internal\b/i, weight: 0.32 },
  { id: 'output_prompt_echo', regex: /\b(ignore previous instructions|reveal the system prompt)\b/i, weight: 0.34 },
];

const RETRIEVAL_CONTEXT_RULES: PatternRule[] = [
  { id: 'context_ignore_instructions', regex: /ignore\s+(all\s+)?previous\s+instructions/i, weight: 0.6 },
  { id: 'context_system_prompt', regex: /\b(system prompt|developer prompt|developer message)\b/i, weight: 0.56 },
  { id: 'context_bypass', regex: /\bbypass\b.{0,30}\b(rules|guardrails|restrictions)\b/i, weight: 0.58 },
  { id: 'context_ru_ignore', regex: /игнориру(й|йте).{0,25}(инструкц|правил)/i, weight: 0.62 },
  { id: 'context_ru_prompt', regex: /системн[а-яёәіңғүұқөһ]*\s+(промпт|инструкц)/i, weight: 0.56 },
  { id: 'context_ru_bypass', regex: /обойди.{0,25}(ограничени|правила)/i, weight: 0.62 },
];

function repeatedTokenRatio(text: string): number {
  const tokens = text
    .toLowerCase()
    .split(/\s+/)
    .map((token) => token.trim())
    .filter(Boolean);

  if (tokens.length < 6) {
    return 0;
  }

  const counts = new Map<string, number>();
  for (const token of tokens) {
    counts.set(token, (counts.get(token) ?? 0) + 1);
  }

  let maxCount = 0;
  for (const count of counts.values()) {
    if (count > maxCount) {
      maxCount = count;
    }
  }

  return maxCount / tokens.length;
}

function runRules(text: string, rules: PatternRule[]) {
  let score = 0;
  const reasons: string[] = [];
  for (const rule of rules) {
    if (rule.regex.test(text)) {
      score += rule.weight;
      reasons.push(rule.id);
    }
  }
  return {
    score,
    reasons,
  };
}

function ok(): GuardrailResult {
  return {
    blocked: false,
    reasons: [],
    category: 'none',
    code: 'ok',
    message: '',
    injection_score: 0,
    blocked_stage: null,
  };
}

function assessPromptInjection(input: string, history: string[] = []) {
  const latestText = input.trim();
  const recentHistory = history
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(-HISTORY_INJECTION_WINDOW);

  const hardCurrent = runRules(latestText, HARD_PROMPT_INJECTION_RULES);
  const softCurrent = runRules(latestText, SOFT_PROMPT_INJECTION_RULES);
  const hardHistory = recentHistory.map((entry) => runRules(entry, HARD_PROMPT_INJECTION_RULES));
  const historyReferential = runRules(latestText, HISTORY_REFERENTIAL_RULES);
  const historyHardScore = hardHistory.reduce((sum, item) => sum + item.score, 0);
  const historyReasons = hardHistory.flatMap((item) => item.reasons);

  const repeatedRatio = repeatedTokenRatio(latestText);
  const cumulativeScore = Math.min(
    1,
    hardCurrent.score + softCurrent.score + Math.min(0.35, historyHardScore * 0.4) + historyReferential.score,
  );
  const referentialChain =
    historyHardScore > 0 &&
    (historyReferential.score > 0 || /(предыдущ[а-яёәіңғүұқөһ]*|прошл[а-яёәіңғүұқөһ]*|earlier|previous|above)/i.test(latestText));
  const blocked = hardCurrent.score >= 0.58 || referentialChain || cumulativeScore >= 0.62;

  return {
    blocked,
    score: cumulativeScore,
    reasons: Array.from(new Set([...hardCurrent.reasons, ...softCurrent.reasons, ...historyReasons, ...historyReferential.reasons])),
    repeatedRatio,
  };
}

export function evaluateGuardrails(input: string, options?: { history?: string[] }): GuardrailResult {
  const text = input.trim();
  if (!text) {
    return {
      blocked: true,
      reasons: ['empty_input'],
      category: 'validation',
      code: 'empty_input',
      message: 'Передайте непустой пользовательский запрос.',
      injection_score: 0,
      blocked_stage: 'validation',
    };
  }

  if (text.length > AI_MAX_INPUT_CHARS) {
    return {
      blocked: true,
      reasons: ['oversized_input'],
      category: 'validation',
      code: 'oversized_input',
      message: `Сообщение слишком длинное. Лимит: ${AI_MAX_INPUT_CHARS} символов.`,
      injection_score: 0,
      blocked_stage: 'validation',
    };
  }

  const garbledRatio = repeatedTokenRatio(text);
  if (garbledRatio >= 0.7) {
    return {
      blocked: true,
      reasons: ['garbled_input'],
      category: 'validation',
      code: 'garbled_input',
      message: 'Запрос выглядит некорректно или состоит из повторяющихся фрагментов. Уточните формулировку.',
      injection_score: 0,
      blocked_stage: 'validation',
    };
  }

  const priorHistory = (options?.history ?? []).map((item) => item.trim()).filter(Boolean);
  const priorInjectionDetected = priorHistory.some((entry) => assessPromptInjection(entry).blocked);
  if (
    priorInjectionDetected &&
    /(предыдущ[а-яёәіңғүұқөһ]*|прошл[а-яёәіңғүұқөһ]*|раньше|above|earlier|previous|that task|ту\s+задач[а-яёәіңғүұқөһ]*)/i.test(
      text,
    )
  ) {
    return {
      blocked: true,
      reasons: ['history_prompt_injection_chain'],
      category: 'prompt_injection',
      code: 'prompt_injection',
      message: 'Запрос отклонен: обнаружена попытка продолжить ранее заблокированную инструкцию.',
      injection_score: 0.92,
      blocked_stage: 'moderation',
    };
  }

  const injection = assessPromptInjection(text, options?.history ?? []);
  if (injection.blocked) {
    return {
      blocked: true,
      reasons: injection.reasons,
      category: 'prompt_injection',
      code: 'prompt_injection',
      message: 'Запрос отклонен: обнаружена попытка изменить системные инструкции или обойти ограничения.',
      injection_score: injection.score,
      blocked_stage: 'moderation',
    };
  }

  for (const pattern of UNSAFE_CONTENT_PATTERNS) {
    if (pattern.regex.test(text)) {
      return {
        blocked: true,
        reasons: [pattern.id],
        category: 'moderation',
        code: 'unsafe_content',
        message: 'Запрос отклонен правилами безопасности.',
        injection_score: 0,
        blocked_stage: 'moderation',
      };
    }
  }

  return ok();
}

export function evaluateRetrievedChunkSafety(text: string): RetrievalChunkSafety {
  const trimmed = text.trim();
  if (!trimmed) {
    return { score: 0, reasons: [] };
  }

  const ruleHits = runRules(trimmed, RETRIEVAL_CONTEXT_RULES);
  const repeatedRatio = repeatedTokenRatio(trimmed);
  const score = Math.min(1, ruleHits.score + (repeatedRatio >= 0.68 ? 0.28 : 0));
  const reasons = [...ruleHits.reasons];
  if (repeatedRatio >= 0.68) {
    reasons.push('context_garbled');
  }
  return {
    score,
    reasons,
  };
}

export function sanitizeRetrievedChunks<T extends { text: string; score?: number }>(chunks: T[]) {
  const sanitized: Array<T & { safety_score: number; safety_reasons: string[] }> = [];
  let filteredCount = 0;

  for (const chunk of chunks) {
    const safety = evaluateRetrievedChunkSafety(chunk.text);
    if (safety.score >= RETRIEVAL_SOFT_BLOCK_THRESHOLD) {
      filteredCount += 1;
      continue;
    }

    const penalty = safety.score >= RETRIEVAL_PENALTY_THRESHOLD ? Math.min(0.24, safety.score * 0.35) : 0;
    const nextChunk = {
      ...chunk,
      score: typeof chunk.score === 'number' ? Math.max(0, chunk.score - penalty) : chunk.score,
      safety_score: Number(safety.score.toFixed(4)),
      safety_reasons: safety.reasons,
    };
    sanitized.push(nextChunk);
  }

  const hasScore = sanitized.every((chunk) => typeof chunk.score === 'number');
  if (hasScore) {
    sanitized.sort((a: any, b: any) => Number(b.score ?? 0) - Number(a.score ?? 0));
  }

  return {
    chunks: sanitized,
    filteredCount,
  };
}

export function evaluateAssistantOutput(text: string): OutputGuardrailResult {
  const trimmed = text.trim();
  if (!trimmed) {
    return {
      blocked: false,
      reasons: [],
      score: 0,
      safeText: '',
    };
  }

  const ruleHits = runRules(trimmed, OUTPUT_LEAK_RULES);
  const score = Math.min(1, ruleHits.score);
  const blocked = score >= OUTPUT_BLOCK_THRESHOLD;

  return {
    blocked,
    reasons: ruleHits.reasons,
    score,
    safeText: blocked
      ? 'Я не могу показывать внутренние инструкции, системный промпт или служебные детали. Могу ответить только по материалам курса.'
      : trimmed,
  };
}
