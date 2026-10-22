"""Bounded OpenAI-compatible HTTP transport. The child process never touches Django/DB."""
import json
import re
import socket
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

MAX_RESPONSE_BYTES = 1_000_000
MAX_ERROR_BYTES = 16_384


def safe_diagnostics(values, api_key=''):
    """Only bounded identifiers cross the process boundary; never free-form messages."""
    if not isinstance(values, dict):
        return {}
    result = {}
    status = values.get('http_status')
    if type(status) is int and 100 <= status <= 599:
        result['http_status'] = status
    for name in ('provider_code', 'provider_type', 'provider_param', 'request_id'):
        value = values.get(name)
        if (isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.\[\]-]{1,128}', value)
                and 'sk-' not in value.lower() and not (api_key and api_key in value)):
            result[name] = value
    return result


def http_diagnostics(error, api_key):
    values = {'http_status': error.code,
              'request_id': error.headers.get('x-request-id') if error.headers else None}
    try:
        raw = error.read(MAX_ERROR_BYTES + 1)
        if len(raw) <= MAX_ERROR_BYTES:
            envelope = json.loads(raw)
            detail = envelope.get('error') if isinstance(envelope, dict) else None
            if isinstance(detail, dict):
                for field in ('code', 'type', 'param'):
                    values['provider_' + field] = detail.get(field)
    except (OSError, ValueError, TypeError, RecursionError):
        pass  # Diagnostics must preserve the original HTTP error even for HTML/broken bodies.
    finally:
        error.close()
    return safe_diagnostics(values, api_key)


class AIError(Exception):
    def __init__(self, code, diagnostics=None):
        self.code = code
        self.diagnostics = safe_diagnostics(diagnostics)
        super().__init__(code)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def token_count(value):
    return value if type(value) is int and 0 <= value <= 2_147_483_647 else None


def call_api(base_url, api_key, model, messages, timeout):
    url = urlsplit(base_url)
    if url.scheme not in ('https', 'http') or not url.netloc or url.username or url.password or url.query or url.fragment or not model:
        raise AIError('ai_not_configured')
    body = json.dumps({'model': model, 'messages': messages, 'response_format': {'type': 'json_object'}}).encode('utf-8')
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if api_key:
        headers['Authorization'] = 'Bearer ' + api_key
    request = Request(base_url.rstrip('/') + '/chat/completions', data=body, headers=headers, method='POST')
    try:
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise AIError('ai_response_too_large')
    except HTTPError as error:
        raise AIError('ai_rate_limited' if error.code == 429 else 'ai_http_error',
                      http_diagnostics(error, api_key)) from None
    except (TimeoutError, socket.timeout):
        raise AIError('ai_timeout') from None
    except URLError as error:
        raise AIError('ai_timeout' if isinstance(error.reason, (TimeoutError, socket.timeout)) else 'ai_connection_error') from None
    except (OSError, ValueError):
        raise AIError('ai_connection_error') from None
    try:
        envelope = json.loads(raw)
        choice = envelope['choices'][0]
        if choice.get('finish_reason') not in (None, 'stop'):
            raise AIError('ai_incomplete_response')
        content = choice['message']['content']
        if not isinstance(content, str):
            raise AIError('ai_invalid_response')
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise AIError('ai_invalid_response')
        usage = envelope.get('usage') or {}
        if not isinstance(usage, dict):
            usage = {}
        actual_model = envelope.get('model')
        actual_model = actual_model if isinstance(actual_model, str) and 0 < len(actual_model) <= 200 else model
        raw_cost = usage.get('cost', envelope.get('cost'))
        currency = usage.get('cost_currency', envelope.get('cost_currency', ''))
        cost = None
        if isinstance(raw_cost, (str, int, float)) and not isinstance(raw_cost, bool):
            try:
                amount = Decimal(str(raw_cost))
                if amount.is_finite() and 0 <= amount < Decimal('1000000000'):
                    cost = str(amount.quantize(Decimal('0.00000001')))
            except InvalidOperation:
                pass
        return {'payload': payload, 'model': actual_model,
                'input_tokens': token_count(usage.get('prompt_tokens')),
                'output_tokens': token_count(usage.get('completion_tokens')),
                'total_tokens': token_count(usage.get('total_tokens')),
                'cost': cost, 'cost_currency': currency if cost is not None and isinstance(currency, str) and len(currency) <= 10 else ''}
    except (KeyError, IndexError, TypeError, ValueError, RecursionError):
        raise AIError('ai_invalid_response') from None


def run_ai(operation):
    """communicate timeout terminates the child, including a hung DNS/socket read."""
    from django.conf import settings
    data = {'base_url': settings.AI_BASE_URL, 'api_key': settings.AI_API_KEY,
            'model': operation.model, 'messages': operation.input_data['messages'],
            'timeout': operation.timeout_seconds}
    try:
        result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'apps.activities.workspace.ai'],
            input=json.dumps(data), text=True, encoding='utf-8', capture_output=True,
            timeout=operation.timeout_seconds, cwd=settings.BASE_DIR)
    except subprocess.TimeoutExpired:
        raise AIError('ai_timeout') from None
    except OSError:
        raise AIError('ai_connection_error') from None
    if result.returncode:
        raise AIError('ai_transport_error')
    try:
        output = json.loads(result.stdout)
        if 'error_code' in output:
            raise AIError(output['error_code'], safe_diagnostics(output.get('diagnostics'), settings.AI_API_KEY))
        return output
    except (TypeError, ValueError):
        raise AIError('ai_invalid_response') from None


if __name__ == '__main__':
    try:
        print(json.dumps(call_api(**json.load(sys.stdin)), ensure_ascii=False))
    except AIError as error:
        print(json.dumps({'error_code': error.code, 'diagnostics': error.diagnostics}))
    except Exception:
        # No provider response bodies, credentials, URLs or exception messages in output.
        print(json.dumps({'error_code': 'ai_transport_error'}))
