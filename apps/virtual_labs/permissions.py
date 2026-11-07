"""Роли модуля (ТЗ, п. 8, 38: Role-Based Access Control).

Администратор модуля — суперпользователь Django, администратор платформы AI edu (role == 'ADMIN')
или пользователь с ролью admin в таблице vl_role. Тьютор — роль tutor в vl_role или логин из
VIRTUAL_LABS_TUTOR_LOGINS. Остальные — преподаватели (обучающиеся тренажёра).
"""
from rest_framework.permissions import BasePermission

from .lab_settings import conf, login_set

TEACHER, TUTOR, ADMIN = 'teacher', 'tutor', 'admin'


def _login(user):
    return str(user.get_username() or '').lower()


def role_of(user):
    if not user or not user.is_authenticated or not user.is_active:
        return None
    if user.is_superuser or getattr(user, 'role', None) == 'ADMIN' or _login(user) in login_set('ADMIN_LOGINS'):
        return ADMIN
    from .models import LabRole
    row = LabRole.objects.filter(user=user).only('role').first()
    if row and row.role in (ADMIN, TUTOR):
        return row.role
    if _login(user) in login_set('TUTOR_LOGINS'):
        return TUTOR
    return TEACHER


def ensure_first_admin(user):
    """Автономный запуск: первый пользователь пустой базы получает роль администратора."""
    if not conf('FIRST_USER_ADMIN'):
        return
    from django.contrib.auth import get_user_model
    from .models import LabRole
    if get_user_model().objects.count() == 1 and not LabRole.objects.filter(role=ADMIN).exists():
        LabRole.objects.update_or_create(user=user, defaults={'role': ADMIN})


class IsTutor(BasePermission):
    message = 'Раздел доступен тьютору или администратору.'

    def has_permission(self, request, view):
        return role_of(request.user) in (TUTOR, ADMIN)


class IsLabAdmin(BasePermission):
    message = 'Раздел доступен администратору.'

    def has_permission(self, request, view):
        return role_of(request.user) == ADMIN


def display_name(user):
    profile = getattr(user, 'lab_profile', None)
    try:
        fio = profile.fio if profile else ''
    except Exception:  # профиль ещё не создан
        fio = ''
    full = ''
    if hasattr(user, 'get_full_name'):
        full = user.get_full_name()
    return fio or full or user.get_username()
