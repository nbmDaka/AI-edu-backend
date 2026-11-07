"""Администрирование модуля (ТЗ, п. 8.3): пользователи и роли, курсы и работы, AI-модели и API,
критерии оценивания, системные шаблоны промптов, резервные копии, журнал запросов к ИИ, LTI."""
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from ..lab_settings import conf
from ..models import (AIProvider, AIRequest, BackupRecord, Course, EvaluationCriterion, LabRole, LtiPlatform,
                      PromptTemplate, VirtualWork)
from ..permissions import ADMIN, IsLabAdmin, TEACHER, TUTOR, display_name, role_of
from ..services import ai, backup
from .base import LabView, body, int_field, text_field


class AdminView(LabView):
    permission_classes = [IsLabAdmin]


# ---------- пользователи и роли ----------
def user_dict(u):
    return {'id': u.pk, 'login': u.get_username(), 'name': display_name(u), 'role': role_of(u) if u.is_active else None,
            'isActive': u.is_active, 'isPlatformAdmin': bool(u.is_superuser or getattr(u, 'role', None) == 'ADMIN'),
            'dateJoined': getattr(u, 'date_joined', None), 'lastLogin': u.last_login}


class UsersView(AdminView):
    def get(self, request):
        User = get_user_model()
        qs = User.objects.all().select_related('lab_profile')
        q = (request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(**{f'{User.USERNAME_FIELD}__icontains': q}) | Q(lab_profile__fio__icontains=q))
        role = request.query_params.get('role')
        rows = [user_dict(u) for u in qs.order_by(User.USERNAME_FIELD)[:500]]
        if role in (TEACHER, TUTOR, ADMIN):
            rows = [r for r in rows if r['role'] == role]
        return Response(rows)


class UserDetailView(AdminView):
    def patch(self, request, pk):
        User = get_user_model()
        user = get_object_or_404(User, pk=pk)
        data = body(request)
        if 'role' in data:
            role = data['role']
            if role not in (TEACHER, TUTOR, ADMIN):
                raise ValidationError({'role': 'Роли: teacher, tutor, admin'})
            if user.pk == request.user.pk and role != ADMIN:
                raise ValidationError({'role': 'Нельзя снять роль администратора с самого себя'})
            if (user.is_superuser or getattr(user, 'role', None) == 'ADMIN') and role != ADMIN:
                raise ValidationError({'role': 'Администратор платформы остаётся администратором модуля'})
            LabRole.objects.update_or_create(user=user, defaults={'role': role})
        if 'isActive' in data:
            if not conf('MANAGE_ACCOUNTS'):
                raise ValidationError({'isActive': 'Учётными записями управляет основная платформа'})
            if user.pk == request.user.pk or user.is_superuser:
                raise ValidationError({'isActive': 'Эту учётную запись нельзя заблокировать'})
            user.is_active = bool(data['isActive'])
            user.save(update_fields=['is_active'])
        return Response(user_dict(user))


# ---------- курсы и работы ----------
def course_dict(c):
    return {'id': c.pk, 'title': c.title, 'description': c.description, 'position': c.position, 'isActive': c.is_active,
            'works': [w.code for w in c.works.order_by('position', 'number')]}


class CoursesView(AdminView):
    def get(self, request):
        return Response([course_dict(c) for c in Course.objects.all()])

    def post(self, request):
        data = body(request)
        c = Course.objects.create(title=text_field(data, 'title', 300, required=True), description=text_field(data, 'description', 3000),
                                  position=int_field(data, 'position', 0, 10000, default=Course.objects.count()))
        return Response(course_dict(c), status=201)


class CourseDetailView(AdminView):
    def patch(self, request, pk):
        c = get_object_or_404(Course, pk=pk)
        data = body(request)
        if 'title' in data:
            c.title = text_field(data, 'title', 300, required=True)
        if 'description' in data:
            c.description = text_field(data, 'description', 3000)
        if 'position' in data:
            c.position = int_field(data, 'position', 0, 10000)
        if 'isActive' in data:
            c.is_active = bool(data['isActive'])
        c.save()
        return Response(course_dict(c))

    def delete(self, request, pk):
        c = get_object_or_404(Course, pk=pk)
        c.works.update(course=None)
        c.delete()
        return Response(status=204)


def work_dict(w):
    t = getattr(w, 'prompt_template', None)
    return {'code': w.code, 'number': w.number, 'title': w.title, 'courseId': w.course_id, 'position': w.position,
            'isEnabled': w.is_enabled, 'hasTemplate': bool(t and t.is_active)}


class WorksView(AdminView):
    def get(self, request):
        return Response([work_dict(w) for w in VirtualWork.objects.select_related('prompt_template').order_by('number')])


class WorkDetailView(AdminView):
    def patch(self, request, code):
        w = get_object_or_404(VirtualWork, code=code)
        data = body(request)
        if 'courseId' in data:
            w.course = get_object_or_404(Course, pk=data['courseId']) if data['courseId'] else None
        if 'position' in data:
            w.position = int_field(data, 'position', 0, 100000)
        if 'isEnabled' in data:
            w.is_enabled = bool(data['isEnabled'])
        w.save()
        return Response(work_dict(w))


# ---------- критерии экспертизы ----------
def criterion_dict(c):
    return {'key': c.key, 'name': c.name, 'description': c.description, 'weight': c.weight, 'position': c.position, 'isActive': c.is_active}


class CriteriaView(AdminView):
    def get(self, request):
        return Response([criterion_dict(c) for c in EvaluationCriterion.objects.all()])


class CriterionDetailView(AdminView):
    def patch(self, request, key):
        c = get_object_or_404(EvaluationCriterion, key=key)
        data = body(request)
        if 'name' in data:
            c.name = text_field(data, 'name', 200, required=True)
        if 'description' in data:
            c.description = text_field(data, 'description', 2000)
        if 'weight' in data:
            w = data['weight']
            if isinstance(w, bool) or not isinstance(w, (int, float)) or not 0 <= w <= 5:
                raise ValidationError({'weight': 'Вес от 0 до 5'})
            c.weight = float(w)
        if 'isActive' in data:
            c.is_active = bool(data['isActive'])
            if not c.is_active and EvaluationCriterion.objects.filter(is_active=True).exclude(pk=c.pk).count() == 0:
                raise ValidationError({'isActive': 'Должен остаться хотя бы один критерий'})
        c.save()
        return Response(criterion_dict(c))


# ---------- шаблоны промптов ----------
def template_dict(t, code):
    return {'work': code, 'role': t.role if t else '', 'task': t.task if t else '', 'constraints': t.constraints if t else '',
            'system': t.system if t else '', 'isActive': bool(t and t.is_active), 'updatedAt': t.updated_at if t else None}


class PromptTemplatesView(AdminView):
    def get(self, request):
        rows = {(t.work.code if t.work_id else 'system'): t for t in PromptTemplate.objects.select_related('work')}
        codes = ['system'] + list(VirtualWork.objects.order_by('number').values_list('code', flat=True))
        return Response([template_dict(rows.get(code), code) for code in codes])


class PromptTemplateDetailView(AdminView):
    def put(self, request, code):
        data = body(request)
        work = None if code == 'system' else get_object_or_404(VirtualWork, code=code)
        values = {'role': text_field(data, 'role', 2000), 'task': text_field(data, 'task', 5000),
                  'constraints': text_field(data, 'constraints', 5000), 'system': text_field(data, 'system', 10000),
                  'is_active': bool(data.get('isActive', True)), 'updated_by': request.user}
        if work is None:
            t = PromptTemplate.objects.filter(work__isnull=True).first()
            if t:
                for k, v in values.items():
                    setattr(t, k, v)
                t.save()
            else:
                t = PromptTemplate.objects.create(work=None, **values)
        else:
            t, _ = PromptTemplate.objects.update_or_create(work=work, defaults=values)
        return Response(template_dict(t, code))

    def delete(self, request, code):
        if code == 'system':
            PromptTemplate.objects.filter(work__isnull=True).delete()
        else:
            PromptTemplate.objects.filter(work__code=code).delete()
        return Response(status=204)


# ---------- подключения к ИИ ----------
def provider_dict(p):
    key = p.api_key or ''
    return {'id': p.pk, 'name': p.name, 'kind': p.kind, 'task': p.task, 'baseUrl': p.base_url, 'model': p.model, 'voice': p.voice,
            'isActive': p.is_active, 'hasKey': bool(key), 'keyHint': ('…' + key[-4:]) if len(key) >= 8 else ('…' if key else ''),
            'updatedAt': p.updated_at}


TTS_KINDS = {AIProvider.Kind.OPENAI_TTS, AIProvider.Kind.ELEVENLABS}


def _apply_provider(p, data, creating=False):
    if creating or 'kind' in data:
        if data.get('kind') not in AIProvider.Kind.values:
            raise ValidationError({'kind': 'Неизвестный тип подключения'})
        p.kind = data['kind']
        p.task = AIProvider.Task.TTS if p.kind in TTS_KINDS else AIProvider.Task.TEXT
    if creating or 'name' in data:
        p.name = text_field(data, 'name', 120) or dict(AIProvider.Kind.choices)[p.kind]
    for field, key, limit in (('base_url', 'baseUrl', 500), ('model', 'model', 200), ('voice', 'voice', 120)):
        if key in data:
            setattr(p, field, text_field(data, key, limit))
    if p.base_url and not p.base_url.startswith('https://') and not p.base_url.startswith('http://localhost') \
            and not p.base_url.startswith('http://127.0.0.1'):
        raise ValidationError({'baseUrl': 'Адрес API должен начинаться с https://'})
    if data.get('apiKey'):
        p.api_key = text_field(data, 'apiKey', 2000)
    if data.get('clearKey'):
        p.api_key = ''
    if 'isActive' in data:
        p.is_active = bool(data['isActive'])


class ProvidersView(AdminView):
    def get(self, request):
        return Response({'providers': [provider_dict(p) for p in AIProvider.objects.all()], 'status': ai.status(),
                         'kinds': [{'value': v, 'label': l, 'task': 'tts' if v in TTS_KINDS else 'text',
                                    'defaults': ai.DEFAULTS.get(v, {})} for v, l in AIProvider.Kind.choices]})

    def post(self, request):
        p = AIProvider()
        _apply_provider(p, body(request), creating=True)
        p.save()
        if p.is_active:
            AIProvider.objects.filter(task=p.task).exclude(pk=p.pk).update(is_active=False)
        return Response(provider_dict(p), status=201)


class ProviderDetailView(AdminView):
    def patch(self, request, pk):
        p = get_object_or_404(AIProvider, pk=pk)
        _apply_provider(p, body(request))
        p.save()
        if p.is_active:
            AIProvider.objects.filter(task=p.task).exclude(pk=p.pk).update(is_active=False)
        return Response(provider_dict(p))

    def delete(self, request, pk):
        get_object_or_404(AIProvider, pk=pk).delete()
        return Response(status=204)


class ProviderTestView(AdminView):
    lab_throttle = 'ai'

    def post(self, request, pk):
        p = get_object_or_404(AIProvider, pk=pk)
        cfg = ai.ProviderConfig(p.kind, p.name, (p.base_url or ai.DEFAULTS.get(p.kind, {}).get('base_url', '')).rstrip('/'),
                                p.model or ai.DEFAULTS.get(p.kind, {}).get('model', ''), p.api_key, p.voice)
        try:
            if p.task == 'tts':
                return Response({'ok': False, 'detail': 'Проверьте озвучивание в работе № 6: нажмите «Озвучить».'})
            text = ai.TEXT_ADAPTERS[p.kind](cfg, [{'role': 'system', 'content': 'Отвечай кратко.'},
                                                  {'role': 'user', 'content': 'Ответь одним словом: готово'}], 0)
            return Response({'ok': True, 'detail': text[:200]})
        except ai.AIError as exc:
            return Response({'ok': False, 'detail': str(exc)})


class AILogView(AdminView):
    def get(self, request):
        rows = AIRequest.objects.select_related('user')[:200]
        return Response([{'id': r.pk, 'user': r.user.get_username() if r.user else '', 'kind': r.kind, 'provider': r.provider,
                          'model': r.model, 'ok': r.ok, 'ms': r.duration_ms, 'chars': r.chars, 'error': r.error,
                          'createdAt': r.created_at} for r in rows])


# ---------- резервные копии ----------
def backup_dict(b):
    return {'id': b.pk, 'file': b.path.replace('\\', '/').rsplit('/', 1)[-1], 'method': b.method, 'size': b.size, 'ok': b.ok,
            'error': b.error, 'createdAt': b.created_at}


class BackupsView(AdminView):
    def get(self, request):
        return Response({'backups': [backup_dict(b) for b in BackupRecord.objects.all()[:50]], 'keep': conf('BACKUP_KEEP')})

    def post(self, request):
        record = backup.create_backup()
        return Response(backup_dict(record), status=201 if record.ok else 500)


# ---------- LTI ----------
def platform_dict(p):
    return {'id': p.pk, 'name': p.name, 'issuer': p.issuer, 'clientId': p.client_id, 'deploymentIds': p.deployment_ids,
            'authLoginUrl': p.auth_login_url, 'authTokenUrl': p.auth_token_url, 'jwksUrl': p.jwks_url, 'isActive': p.is_active,
            'users': p.users.count()}


def _apply_platform(p, data, creating=False):
    for field, key, limit, required in (('name', 'name', 200, True), ('issuer', 'issuer', 500, True), ('client_id', 'clientId', 255, True),
                                        ('auth_login_url', 'authLoginUrl', 500, True), ('auth_token_url', 'authTokenUrl', 500, False),
                                        ('jwks_url', 'jwksUrl', 500, True)):
        if creating or key in data:
            setattr(p, field, text_field(data, key, limit, required=required))
    for field in ('auth_login_url', 'jwks_url', 'auth_token_url'):
        value = getattr(p, field)
        if value and not value.startswith('https://') and not value.startswith('http://localhost'):
            raise ValidationError({field: 'Адрес должен начинаться с https://'})
    if 'deploymentIds' in data:
        ids = data['deploymentIds']
        if isinstance(ids, str):
            ids = [x.strip() for x in ids.split(',') if x.strip()]
        if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
            raise ValidationError({'deploymentIds': 'Список идентификаторов'})
        p.deployment_ids = ids[:50]
    if 'isActive' in data:
        p.is_active = bool(data['isActive'])


class LtiPlatformsView(AdminView):
    def get(self, request):
        from .lti import tool_urls
        return Response({'platforms': [platform_dict(p) for p in LtiPlatform.objects.all()], 'tool': tool_urls(request)})

    def post(self, request):
        p = LtiPlatform()
        _apply_platform(p, body(request), creating=True)
        p.save()
        return Response(platform_dict(p), status=201)


class LtiPlatformDetailView(AdminView):
    def patch(self, request, pk):
        p = get_object_or_404(LtiPlatform, pk=pk)
        _apply_platform(p, body(request))
        p.save()
        return Response(platform_dict(p))

    def delete(self, request, pk):
        get_object_or_404(LtiPlatform, pk=pk).delete()
        return Response(status=204)
