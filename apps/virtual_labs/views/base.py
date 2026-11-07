"""Общие классы представлений модуля."""
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView

from ..throttling import LabThrottle


class LabView(APIView):
    """Базовое представление: аутентификация и права берутся из настроек платформы
    (сессия + CSRF в AI edu и в автономном запуске), лимит частоты — из настроек модуля."""
    throttle_classes = [LabThrottle]
    lab_throttle = 'write'


def body(request, *keys, required=()):
    data = request.data if isinstance(request.data, dict) else {}
    for key in required:
        if data.get(key) in (None, ''):
            raise ValidationError({key: 'Обязательное поле'})
    return data


def text_field(data, key, limit, required=False, default=''):
    value = data.get(key, default)
    if value is None:
        value = default
    if not isinstance(value, str):
        raise ValidationError({key: 'Ожидается текст'})
    value = value.strip() if key != 'text' else value
    if required and not value:
        raise ValidationError({key: 'Обязательное поле'})
    if len(value) > limit:
        raise ValidationError({key: f'Не более {limit} символов'})
    return value


def int_field(data, key, minimum=None, maximum=None, default=None):
    value = data.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise ValidationError({key: 'Ожидается целое число'})
    value = int(value)
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        raise ValidationError({key: f'Допустимо от {minimum} до {maximum}'})
    return value
