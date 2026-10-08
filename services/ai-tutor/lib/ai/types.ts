export type SupportedLanguage = 'ru' | 'kk' | 'en';

export type InputMessage = {
  role: 'user' | 'assistant';
  content: string;
};

export type SourceItem = {
  chunk_id: number;
  label: string;
  snippet: string;
  score: number;
};

export type UsageSummary = {
  prompt_tokens: number;
  completion_tokens: number;
  embedding_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number;
};

export type GuardrailResult = {
  blocked: boolean;
  reasons: string[];
  category: 'none' | 'validation' | 'moderation' | 'prompt_injection';
  code:
    | 'ok'
    | 'empty_input'
    | 'oversized_input'
    | 'garbled_input'
    | 'unsafe_content'
    | 'prompt_injection';
  message: string;
  injection_score: number;
  blocked_stage: 'validation' | 'moderation' | 'generation' | null;
};

export type RetrievalDiagnostics = {
  retrieval_query: string;
  top_k: number;
  scores: Array<{
    chunk_id: number;
    label: string;
    score: number;
    rank: number;
  }>;
  fallback_used: boolean;
};
