"""Интеграция с LMS по LTI 1.3 (ТЗ, п. 37): Moodle, Open edX, Canvas, Blackboard и др.

Реализован основной сценарий LTI 1.3 Core — запуск ресурса (LtiResourceLinkRequest):
  1) LMS вызывает /lti/login/ (OIDC third-party initiated login);
  2) инструмент перенаправляет браузер на страницу авторизации LMS с state и nonce;
  3) LMS отправляет подписанный id_token на /lti/launch/;
  4) инструмент проверяет подпись по JWKS платформы, iss, aud, nonce, deployment_id,
     находит или создаёт пользователя и открывает тренажёр (или нужную работу).
Открытые ключи инструмента публикуются на /lti/jwks/.
Для работы нужны пакеты PyJWT и cryptography.
"""
import json
import secrets
import time
import urllib.request

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from ..models import LtiPlatform, LtiToolKey, LtiUserLink

CLAIM = 'https://purl.imsglobal.org/spec/lti/claim/'
STATE_TTL = 600


class LtiError(Exception):
    pass


def _jwt():
    try:
        import jwt  # PyJWT
        return jwt
    except ImportError:
        raise LtiError('Для LTI установите пакеты PyJWT и cryptography.') from None


def tool_key():
    """Ключ RSA инструмента; создаётся при первом обращении."""
    key = LtiToolKey.objects.order_by('-created_at').first()
    if key:
        return key
    jwt = _jwt()
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    kid = secrets.token_hex(8)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    public.update({'kid': kid, 'alg': 'RS256', 'use': 'sig'})
    return LtiToolKey.objects.create(kid=kid, private_pem=pem, public_jwk=public)


def jwks():
    return {'keys': [k.public_jwk for k in LtiToolKey.objects.all()] or [tool_key().public_jwk]}


def find_platform(issuer, client_id=None):
    qs = LtiPlatform.objects.filter(issuer=issuer, is_active=True)
    if client_id:
        qs = qs.filter(client_id=client_id)
    platform = qs.first()
    if not platform:
        raise LtiError('Платформа LMS не зарегистрирована в разделе «Администрирование → LTI».')
    return platform


def login_redirect(params, launch_url):
    """Шаг 1–2: проверка запроса входа и адрес перенаправления на страницу авторизации LMS."""
    issuer, login_hint = params.get('iss'), params.get('login_hint')
    if not issuer or not login_hint:
        raise LtiError('В запросе входа нет iss или login_hint.')
    platform = find_platform(issuer, params.get('client_id'))
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    cache.set(f'vl_lti_state_{state}', {'nonce': nonce, 'platform': platform.pk,
                                         'target': params.get('target_link_uri', '')}, STATE_TTL)
    query = {'scope': 'openid', 'response_type': 'id_token', 'response_mode': 'form_post', 'prompt': 'none',
             'client_id': platform.client_id, 'redirect_uri': launch_url, 'login_hint': login_hint,
             'state': state, 'nonce': nonce}
    if params.get('lti_message_hint'):
        query['lti_message_hint'] = params['lti_message_hint']
    from urllib.parse import urlencode
    sep = '&' if '?' in platform.auth_login_url else '?'
    return platform.auth_login_url + sep + urlencode(query)


def _platform_keys(platform):
    key = f'vl_lti_jwks_{platform.pk}'
    data = cache.get(key)
    if not data:
        req = urllib.request.Request(platform.jwks_url, headers={'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
        except Exception as exc:
            raise LtiError(f'Не удалось получить ключи LMS: {exc}') from None
        cache.set(key, data, 3600)
    return data


def verify_launch(id_token, state):
    """Шаг 3–4: проверка id_token. Возвращает (платформа, claims, целевая страница)."""
    jwt = _jwt()
    saved = cache.get(f'vl_lti_state_{state}') if state else None
    if not saved:
        raise LtiError('Сеанс входа LTI истёк или state неверный. Откройте ресурс в LMS ещё раз.')
    cache.delete(f'vl_lti_state_{state}')
    platform = LtiPlatform.objects.filter(pk=saved['platform'], is_active=True).first()
    if not platform:
        raise LtiError('Платформа LMS отключена.')
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError:
        raise LtiError('id_token повреждён.') from None
    keys = _platform_keys(platform).get('keys', [])
    jwk = next((k for k in keys if k.get('kid') == header.get('kid')), keys[0] if len(keys) == 1 else None)
    if not jwk:
        raise LtiError('Ключ подписи LMS не найден.')
    public = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
    try:
        claims = jwt.decode(id_token, public, algorithms=['RS256'], audience=platform.client_id, issuer=platform.issuer,
                            options={'require': ['exp', 'iat', 'sub', 'nonce']}, leeway=60)
    except jwt.PyJWTError as exc:
        raise LtiError(f'Подпись или содержимое id_token неверны: {exc}') from None
    if claims.get('nonce') != saved['nonce']:
        raise LtiError('Неверный nonce.')
    if not cache.add(f'vl_lti_nonce_{claims["nonce"]}', 1, STATE_TTL):
        raise LtiError('Повторное использование id_token.')
    if claims.get(CLAIM + 'version') != '1.3.0':
        raise LtiError('Поддерживается только LTI 1.3.')
    if claims.get(CLAIM + 'message_type') != 'LtiResourceLinkRequest':
        raise LtiError('Поддерживается только запуск ресурса (LtiResourceLinkRequest).')
    deployment = claims.get(CLAIM + 'deployment_id')
    if platform.deployment_ids and deployment not in platform.deployment_ids:
        raise LtiError('Неизвестный deployment_id.')
    return platform, claims, saved.get('target', '')


@transaction.atomic
def user_for_launch(platform, claims):
    """Пользователь платформы для запуска: найденный по связи iss+sub, по email или новый."""
    from ..models import LabRole
    from ..permissions import ADMIN
    User = get_user_model()
    link = LtiUserLink.objects.select_related('user').filter(platform=platform, subject=claims['sub']).first()
    email = (claims.get('email') or '').strip().lower()
    if link:
        user = link.user
    else:
        user = None
        if email and 'email' in [f.name for f in User._meta.get_fields()]:
            user = User.objects.filter(email__iexact=email).first()
        if user is None:
            field = User.USERNAME_FIELD
            value = email if field == 'email' and email else f'lti-{platform.pk}-{claims["sub"]}'[:150]
            if field == 'email' and not email:
                value = f'lti-{platform.pk}-{claims["sub"]}@lti.invalid'[:254]
            extra = {}
            if field != 'email' and email:
                extra['email'] = email
            user = User.objects.create_user(**{field: value}, password=None, **extra)
            user.first_name = (claims.get('given_name') or '')[:150] if hasattr(user, 'first_name') else ''
            user.last_name = (claims.get('family_name') or '')[:150] if hasattr(user, 'last_name') else ''
            user.set_unusable_password()
            user.save()
        link = LtiUserLink(platform=platform, subject=claims['sub'], user=user)
    context = claims.get(CLAIM + 'context') or {}
    link.context_title = str(context.get('title') or '')[:300]
    link.last_launch_at = timezone.now()
    link.save()
    roles = claims.get(CLAIM + 'roles') or []
    if any(r.endswith('#Instructor') or r.endswith('/Instructor') for r in roles):
        current = LabRole.objects.filter(user=user).first()
        if not current or current.role != ADMIN:
            LabRole.objects.update_or_create(user=user, defaults={'role': 'tutor'})
    return user


def target_work(claims):
    custom = claims.get(CLAIM + 'custom') or {}
    work = str(custom.get('work') or custom.get('lab') or '').strip().lower()
    return work if work.startswith('vw') and len(work) <= 8 else ''


def make_test_token(platform, private_pem, kid, nonce, sub='student-1', roles=None, custom=None, email=''):
    """Вспомогательная функция для тестов и проверки настройки: подписывает id_token как LMS."""
    jwt = _jwt()
    now = int(time.time())
    claims = {
        'iss': platform.issuer, 'aud': platform.client_id, 'sub': sub, 'iat': now, 'exp': now + 300, 'nonce': nonce,
        'email': email, 'given_name': 'Тест', 'family_name': 'LTI',
        CLAIM + 'version': '1.3.0', CLAIM + 'message_type': 'LtiResourceLinkRequest',
        CLAIM + 'deployment_id': (platform.deployment_ids or ['1'])[0],
        CLAIM + 'roles': roles or ['http://purl.imsglobal.org/vocab/lis/v2/membership#Learner'],
        CLAIM + 'context': {'id': 'c1', 'title': 'Курс повышения квалификации'},
        CLAIM + 'resource_link': {'id': 'r1'}, CLAIM + 'custom': custom or {},
    }
    return jwt.encode(claims, private_pem, algorithm='RS256', headers={'kid': kid})
