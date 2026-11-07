"""Точки входа LTI 1.3. Это обычные представления Django (не DRF): LMS обращается к ним
без сессии и CSRF-токена платформы, подлинность запуска проверяется подписью id_token."""
from django.contrib.auth import login
from django.http import HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ..lab_settings import conf
from ..services import lti
from ..throttling import LabThrottle


def _base(request):
    return (conf('LTI_TOOL_URL') or request.build_absolute_uri('/')).rstrip('/')


def tool_urls(request):
    base = _base(request)
    return {'toolUrl': base + conf('LTI_FRONTEND_PATH'), 'loginUrl': base + reverse('virtual_labs:lti-login'),
            'redirectUrl': base + reverse('virtual_labs:lti-launch'), 'jwksUrl': base + reverse('virtual_labs:lti-jwks'),
            'customParameters': 'work=vw05  (необязательно: сразу открыть нужную работу)'}


def _error(message, status=400):
    return HttpResponseBadRequest(f'<!doctype html><meta charset="utf-8"><title>LTI</title>'
                                  f'<p style="font-family:sans-serif">Ошибка LTI: {escape(message)}</p>', status=status)


def _throttled(request):
    view = type('V', (), {'lab_throttle': 'lti'})()
    throttle = LabThrottle()
    request.user = getattr(request, 'user', None)
    return not throttle.allow_request(request, view)


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def lti_login(request):
    if _throttled(request):
        return _error('Слишком много запросов', 429)
    params = request.POST if request.method == 'POST' else request.GET
    try:
        url = lti.login_redirect(params, _base(request) + reverse('virtual_labs:lti-launch'))
    except lti.LtiError as exc:
        return _error(str(exc))
    return HttpResponseRedirect(url)


@csrf_exempt
@require_http_methods(['POST'])
def lti_launch(request):
    if _throttled(request):
        return _error('Слишком много запросов', 429)
    try:
        platform, claims, _target = lti.verify_launch(request.POST.get('id_token', ''), request.POST.get('state', ''))
        user = lti.user_for_launch(platform, claims)
    except lti.LtiError as exc:
        return _error(str(exc), 401)
    backend = 'django.contrib.auth.backends.ModelBackend'
    login(request, user, backend=backend)
    path = conf('LTI_FRONTEND_PATH')
    work = lti.target_work(claims)
    if work:
        path = path.rstrip('/') + '/lab/' + work
    return HttpResponseRedirect(path)


@require_http_methods(['GET'])
def lti_jwks(request):
    try:
        return JsonResponse(lti.jwks())
    except lti.LtiError as exc:
        return JsonResponse({'detail': str(exc)}, status=501)
