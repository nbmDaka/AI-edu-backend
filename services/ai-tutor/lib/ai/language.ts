import type { SupportedLanguage } from '@/lib/ai/types';

export function detectSupportedLanguage(text: string): SupportedLanguage {
  const normalized = text.toLowerCase();
  const hasKazakhSpecific = /[әіңғүұқөһ]/i.test(normalized);
  if (hasKazakhSpecific) {
    return 'kk';
  }

  const hasCyrillic = /[а-яё]/i.test(normalized);
  const hasLatin = /[a-z]/i.test(normalized);

  if (hasLatin && !hasCyrillic) {
    return 'en';
  }
  if (hasCyrillic) {
    return 'ru';
  }
  return 'ru';
}

export function noAnswerText(language: SupportedLanguage): string {
  if (language === 'kk') {
    return 'Дәріс материалдарында бұл туралы ақпарат жоқ. Сұрағыңызды нақтылап көріңіз.';
  }
  if (language === 'en') {
    return 'This is not covered in the lecture materials. Please refine your question.';
  }
  return 'В материалах лекции этого нет. Уточните вопрос в рамках текущей лекции, и я постараюсь помочь.';
}

export function languageInstruction(language: SupportedLanguage): string {
  if (language === 'kk') {
    return [
      'Жауапты тек қазақ тілінде бер.',
      'Пайдаланушы тілі қазақша болса, ағылшын сөздер мен фразаларды қолданба.',
      'Контексттегі ағылшын терминдерді қазақша түсіндірме атауға ауыстыр.',
      'Тек қажет болса ағылшын түпнұсқасын бір рет жақша ішінде қысқа түрде көрсет.',
    ].join(' ');
  }
  if (language === 'en') {
    return 'Reply in English.';
  }
  return [
    'Отвечай строго на русском языке.',
    'Если вопрос на русском, не используй английские слова и фразы в основном тексте ответа.',
    'Термины из контекста переводи на русский.',
    'Только при необходимости допускается один английский оригинал в скобках.',
  ].join(' ');
}

export function tutorIdentityText(language: SupportedLanguage): string {
  if (language === 'kk') {
    return 'Мен осы беттегі дәріс бойынша AI-тьютормын. Мен тек дәріс материалдарына сүйеніп жауап беремін.';
  }
  if (language === 'en') {
    return 'I am the AI tutor for this lecture page. I answer only using the lecture materials shown here.';
  }
  return 'Я AI-тьютор этой лекции. Я отвечаю только на основе материалов лекции на этой странице.';
}

export function isIdentityQuestion(text: string): boolean {
  const normalized = text.toLowerCase().trim();
  return /кто ты|кто вы|что ты такое|who are you|what are you|сен кімсің|сіз кімсіз|кімсің/.test(
    normalized,
  );
}
