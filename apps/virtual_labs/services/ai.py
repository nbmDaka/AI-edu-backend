"""AI Orchestrator на сервере (ТЗ, п. 26, 35): единый интерфейс (API Adapter Layer) к внешним сервисам ИИ.

Какой сервис использовать для задачи (текст или озвучивание), задаёт администратор в разделе
«Администрирование → Модели ИИ». Если там ничего не включено, берутся переменные окружения
(VIRTUAL_LABS_AI_KIND, VIRTUAL_LABS_AI_BASE_URL, VIRTUAL_LABS_AI_API_KEY, VIRTUAL_LABS_AI_MODEL).
Ключи хранятся только на сервере и в интерфейс не передаются. Без настроенной модели
интерфейс работает в демо-режиме (шаблонный генератор).
"""
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..lab_settings import conf

SYSTEM = ('Ты — помощник преподавателя в обучающей системе AI edu. Создаёшь учебные материалы высокого методического качества. '
          'Строго соблюдай структуру из блока ФОРМАТ, используй Markdown-заголовки ## для разделов. '
          'Не выдумывай источники, DOI и статистику.')

DEFAULTS = {
    'openai': {'base_url': 'https://api.openai.com/v1', 'model': 'gpt-4o-mini'},
    'compatible': {'base_url': 'https://openrouter.ai/api/v1', 'model': 'deepseek/deepseek-chat'},
    'gemini': {'base_url': 'https://generativelanguage.googleapis.com/v1beta', 'model': 'gemini-2.5-flash'},
    'anthropic': {'base_url': 'https://api.anthropic.com/v1', 'model': 'claude-sonnet-5-5'},
    'openai_tts': {'base_url': 'https://api.openai.com/v1', 'model': 'gpt-4o-mini-tts', 'voice': 'alloy'},
    'elevenlabs': {'base_url': 'https://api.elevenlabs.io/v1', 'model': 'eleven_multilingual_v2', 'voice': '21m00Tcm4TlvDQ8ikWAM'},
}

# Маршрутизация по типу задачи (ТЗ, п. 26): лекция → LLM, озвучивание → TTS и т. д.
ROUTES = {
    'llm': 'text', 'llm+chat': 'text', 'llm+search': 'text', 'llm+image': 'text', 'evaluation': 'text',
    'llm+tts': 'text', 'tts': 'tts',
}


class AIError(Exception):
    pass


@dataclass
class ProviderConfig:
    kind: str
    name: str
    base_url: str
    model: str
    api_key: str
    voice: str = ''

    @property
    def public(self):
        return {'kind': self.kind, 'name': self.name, 'model': self.model}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Перенаправления запрещены, чтобы ключ не ушёл на другой адрес."""
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _request(url, payload, headers, timeout=None, raw=False):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST',
                                 headers={'Content-Type': 'application/json', **headers})
    try:
        with _opener.open(req, timeout=timeout or conf('AI_TIMEOUT_SECONDS')) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:2000].decode('utf-8', 'replace')
        try:
            j = json.loads(detail)
            err = j.get('error') or j.get('detail') or {}
            detail = err.get('message') if isinstance(err, dict) else str(err)
        except ValueError:
            pass
        raise AIError(f'Сервис ИИ вернул ошибку {exc.code}: {detail or exc.reason}') from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AIError(f'Сервис ИИ недоступен: {getattr(exc, "reason", exc)}') from None
    if raw:
        return body
    try:
        return json.loads(body)
    except ValueError:
        raise AIError('Сервис ИИ вернул ответ не в формате JSON.') from None


def _env_provider(task):
    if task == 'text':
        kind = (os.environ.get('VIRTUAL_LABS_AI_KIND') or '').lower()
        key = os.environ.get('VIRTUAL_LABS_AI_API_KEY', '')
        base = os.environ.get('VIRTUAL_LABS_AI_BASE_URL', '')
        model = os.environ.get('VIRTUAL_LABS_AI_MODEL', '')
    else:
        kind = (os.environ.get('VIRTUAL_LABS_TTS_KIND') or '').lower()
        key = os.environ.get('VIRTUAL_LABS_TTS_API_KEY', '')
        base, model = '', os.environ.get('VIRTUAL_LABS_TTS_MODEL', '')
    if kind not in DEFAULTS or (not key and kind != 'compatible'):
        return None
    d = DEFAULTS[kind]
    return ProviderConfig(kind, kind, (base or d['base_url']).rstrip('/'), model or d['model'], key,
                          os.environ.get('VIRTUAL_LABS_TTS_VOICE', d.get('voice', '')))


def active_provider(task='text'):
    """Активное подключение для задачи: из таблицы администратора, иначе из переменных окружения."""
    from ..models import AIProvider
    row = AIProvider.objects.filter(task=task, is_active=True).order_by('-updated_at').first()
    if row:
        d = DEFAULTS.get(row.kind, {})
        return ProviderConfig(row.kind, row.name, (row.base_url or d.get('base_url', '')).rstrip('/'),
                              row.model or d.get('model', ''), row.api_key, row.voice or d.get('voice', ''))
    return _env_provider(task)


def _openai_chat(p, messages, temperature):
    headers = {'Authorization': f'Bearer {p.api_key}'} if p.api_key else {}
    j = _request(f'{p.base_url}/chat/completions', {'model': p.model, 'messages': messages, 'temperature': temperature}, headers)
    text = ((j.get('choices') or [{}])[0].get('message') or {}).get('content')
    if not text:
        raise AIError('Модель вернула пустой ответ.')
    return text


def _gemini_chat(p, messages, temperature):
    system = '\n'.join(m['content'] for m in messages if m['role'] == 'system')
    contents = [{'role': 'model' if m['role'] == 'assistant' else 'user', 'parts': [{'text': m['content']}]}
                for m in messages if m['role'] != 'system']
    j = _request(f'{p.base_url}/models/{p.model}:generateContent',
                 {'systemInstruction': {'parts': [{'text': system}]}, 'contents': contents,
                  'generationConfig': {'temperature': temperature}}, {'x-goog-api-key': p.api_key})
    parts = (((j.get('candidates') or [{}])[0].get('content') or {}).get('parts')) or []
    text = ''.join(x.get('text', '') for x in parts)
    if not text:
        raise AIError('Модель вернула пустой ответ.')
    return text


def _anthropic_chat(p, messages, temperature):
    system = '\n'.join(m['content'] for m in messages if m['role'] == 'system')
    rest = [{'role': m['role'], 'content': m['content']} for m in messages if m['role'] != 'system']
    j = _request(f'{p.base_url}/messages', {'model': p.model, 'system': system, 'messages': rest,
                                            'max_tokens': 8000, 'temperature': temperature},
                 {'x-api-key': p.api_key, 'anthropic-version': '2023-06-01'})
    text = ''.join(b.get('text', '') for b in j.get('content') or [] if b.get('type') == 'text')
    if not text:
        raise AIError('Модель вернула пустой ответ.')
    return text


TEXT_ADAPTERS = {'openai': _openai_chat, 'compatible': _openai_chat, 'gemini': _gemini_chat, 'anthropic': _anthropic_chat}


def generate(prompt=None, messages=None, system=None, temperature=0.6):
    """Генерация текста активной моделью. Возвращает текст, провайдера, модель и время."""
    p = active_provider('text')
    if not p:
        raise AIError('Языковая модель на сервере не подключена. Работает демо-режим.')
    if p.kind not in TEXT_ADAPTERS:
        raise AIError('Это подключение не умеет генерировать текст.')
    msgs = [{'role': 'system', 'content': system or SYSTEM}] + (messages or [{'role': 'user', 'content': prompt or ''}])
    t0 = time.monotonic()
    text = TEXT_ADAPTERS[p.kind](p, msgs, temperature)
    return {'text': text, 'provider': p.kind, 'model': p.model, 'ms': int((time.monotonic() - t0) * 1000)}


def synthesize(text, voice=None):
    """Озвучивание: MP3 и название сервиса."""
    p = active_provider('tts')
    if not p:
        raise AIError('Озвучивание на сервере не подключено. Используется синтезатор браузера.')
    if p.kind == 'openai_tts':
        audio = _request(f'{p.base_url}/audio/speech', {'model': p.model, 'voice': voice or p.voice or 'alloy',
                                                        'input': text, 'response_format': 'mp3'},
                         {'Authorization': f'Bearer {p.api_key}'}, raw=True)
    elif p.kind == 'elevenlabs':
        audio = _request(f'{p.base_url}/text-to-speech/{voice or p.voice}?output_format=mp3_44100_128',
                         {'text': text, 'model_id': p.model, 'voice_settings': {'stability': 0.5, 'similarity_boost': 0.75}},
                         {'xi-api-key': p.api_key, 'Accept': 'audio/mpeg'}, raw=True)
    else:
        raise AIError('Это подключение не умеет озвучивать текст.')
    return audio, p.kind


def status():
    text, tts = active_provider('text'), active_provider('tts')
    return {'ai': {'configured': bool(text), **(text.public if text else {'kind': 'demo', 'name': 'Демо-режим', 'model': ''})},
            'tts': {'configured': bool(tts), **(tts.public if tts else {'kind': 'browser', 'name': 'Браузер', 'model': ''})}}
