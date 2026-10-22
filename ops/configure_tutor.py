"""Write server-only tutor settings. The provider key is accepted from the environment."""
import os
import re
import secrets
import tempfile
from pathlib import Path


def read_values(path):
    if path.is_symlink():
        raise ValueError('Refusing a symlink for an environment file')
    if not path.exists():
        return {}, ''
    content = path.read_text(encoding='utf-8-sig')
    values = {}
    for line in content.splitlines():
        name, separator, value = line.partition('=')
        if separator and re.fullmatch(r'[A-Z][A-Z0-9_]*', name.strip()):
            values[name.strip()] = value.strip().strip('\"\'')
    return values, content


def update_file(path, values, content):
    lines = [line for line in content.splitlines()
             if line.partition('=')[0].strip() not in values]
    lines.extend(f'{name}={value}' for name, value in values.items())
    fd, temporary = tempfile.mkstemp(prefix='.tutor-env-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as output:
            output.write('\n'.join(lines) + '\n')
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configure(root, api_key):
    if not re.fullmatch(r'sk-[A-Za-z0-9_-]{20,}', api_key):
        raise ValueError('A valid OPENAI_API_KEY secret is required')
    backend = root / '.env'
    gateway = root / '.env.ai-tutor'
    previous, backend_content = read_values(backend)
    if not backend.is_file():
        raise ValueError('Existing backend environment is required')
    gateway_values, gateway_content = read_values(gateway)
    service_key = gateway_values.get('API_SHARED_KEY', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,}', service_key):
        service_key = secrets.token_urlsafe(48)
    backup = root / '.env.before-tutor'
    if backup.is_symlink():
        raise ValueError('Refusing a symlink for the environment backup')
    if not backup.exists():
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(backend_content)
    defaults = {
        'OPENAI_MODEL': 'gpt-4o-mini',
        'EMBEDDING_MODEL': 'text-embedding-3-small',
        'AI_DB_PROVIDER': 'sqlite',
        'AI_DB_PATH': '/app/data/ai-module.db',
        'API_CORS_ORIGINS': 'https://aiedu.com.kz',
        'API_CORS_ALLOW_CREDENTIALS': 'false',
        'AI_RPM_LIMIT': '30',
        'AI_DAILY_REQUEST_LIMIT': '500',
        'AI_DAILY_TOKEN_LIMIT': '750000',
        'AI_MONTHLY_COST_USD_LIMIT': '100',
        'AI_CHAT_TIMEOUT_MS': '90000',
        'AI_PROVIDER_TIMEOUT_MS': '45000',
        'AI_PROVIDER_MAX_RETRIES': '1',
        'AI_TELEMETRY_RETENTION_DAYS': '30',
        'AI_REDIS_ENABLED': 'false',
    }
    settings = {name: value for name, value in defaults.items() if name not in gateway_values}
    settings.update(OPENAI_API_KEY=api_key, API_SHARED_KEY=service_key)
    update_file(gateway, settings, gateway_content)
    backend_settings = {'AI_SERVICE_URL': 'http://ai-tutor:3001',
                        'AI_SERVICE_API_KEY': service_key, 'AI_SERVICE_TIMEOUT': '100'}
    # This deployment already uses OpenAI for workspace operations as well.
    if previous.get('AI_BASE_URL', '').rstrip('/') == 'https://api.openai.com/v1':
        backend_settings['AI_API_KEY'] = api_key
    update_file(backend, backend_settings, backend_content)


if __name__ == '__main__':
    configure(Path.cwd(), os.environ.get('OPENAI_API_KEY', '').strip())
    print('Tutor environment configured; secret values were not logged.')
