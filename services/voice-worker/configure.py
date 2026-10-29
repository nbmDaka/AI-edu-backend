"""Prepare a private Cedar worker, then activate only its upstream URL."""
import argparse
import os
import re
import secrets
import tempfile
from pathlib import Path


def read_env(path):
    if path.is_symlink():
        raise ValueError('Refusing a symlink environment file')
    content = path.read_text(encoding='utf-8') if path.exists() else ''
    values = {}
    for line in content.splitlines():
        key, separator, value = line.partition('=')
        if separator and re.fullmatch(r'[A-Z][A-Z0-9_]*', key.strip()):
            values[key.strip()] = value.strip().strip('\"\'')
    return content, values


def update(path, content, values):
    lines = [line for line in content.splitlines() if line.partition('=')[0].strip() not in values]
    lines.extend(f'{key}={value}' for key, value in values.items())
    descriptor, temporary = tempfile.mkstemp(prefix='.voice-env-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as output:
            output.write('\n'.join(lines) + '\n')
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--gateway', required=True, type=Path)
    parser.add_argument('--worker', required=True, type=Path)
    parser.add_argument('--activate', action='store_true')
    args = parser.parse_args()
    gateway = args.gateway.resolve()
    worker = args.worker.resolve()
    if not gateway.is_file() or not worker.is_dir():
        raise ValueError('Existing gateway environment and worker directory are required')
    content, settings = read_env(gateway)
    if not re.fullmatch(r'sk-[A-Za-z0-9_-]{20,}', settings.get('OPENAI_API_KEY', '')):
        raise ValueError('The existing server OpenAI key is missing')
    private = worker / '.env.worker'
    private_content, worker_settings = read_env(private)
    token = worker_settings.get('TTS_WORKER_TOKEN') or secrets.token_urlsafe(48)
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,}', token):
        raise ValueError('Invalid worker route token')
    update(private, private_content, {'TTS_WORKER_TOKEN': token, 'TTS_DAILY_CHARACTER_LIMIT': '50000'})
    compose_env = worker / '.env'
    compose_content, _ = read_env(compose_env)
    update(compose_env, compose_content, {'TUTOR_ENV_FILE': str(gateway)})
    if args.activate:
        backup = gateway.with_name(gateway.name + '.before-cedar-20261029')
        if backup.is_symlink():
            raise ValueError('Refusing a symlink backup')
        if not backup.exists():
            with os.fdopen(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as output:
                output.write(content)
        update(gateway, content, {'LOCAL_TTS_URL': f'http://aiedu-voice-worker:8002/{token}/synthesize'})
    print('Cedar worker prepared' + (' and upstream URL activated.' if args.activate else '.') + ' Secret values were not logged.')


if __name__ == '__main__':
    main()
