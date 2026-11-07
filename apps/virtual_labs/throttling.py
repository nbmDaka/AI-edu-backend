"""Ограничение частоты запросов к API модуля (ТЗ, п. 38).

Лимиты задаются в VIRTUAL_LABS['THROTTLE_RATES'] и не требуют изменения глобальных
настроек REST_FRAMEWORK основной платформы. Счётчики хранятся в кэше Django
(в AI edu — Redis, в автономном запуске — память процесса или база).
"""
from rest_framework.throttling import SimpleRateThrottle

from .lab_settings import conf


class LabThrottle(SimpleRateThrottle):
    scope_attr = 'lab_throttle'

    def __init__(self):
        # rate задаётся в allow_request, когда известен scope представления
        pass

    def allow_request(self, request, view):
        self.scope = getattr(view, self.scope_attr, None)
        if not self.scope:
            return True
        if self.scope == 'write' and request.method in ('GET', 'HEAD', 'OPTIONS'):
            return True
        self.rate = conf('THROTTLE_RATES').get(self.scope)
        if not self.rate:
            return True
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        ident = request.user.pk if request.user and request.user.is_authenticated else self.get_ident(request)
        return f'vl_throttle_{self.scope}_{ident}'
