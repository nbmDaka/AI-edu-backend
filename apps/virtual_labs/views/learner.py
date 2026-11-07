"""API преподавателя-слушателя: настройки модуля, данные прохождения, экспертиза, аналитика, группы."""
from django.db.models import Avg
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .. import catalog
from ..lab_settings import conf
from ..models import (Assignment, Course, EvaluationCriterion, GroupMembership, Progress, PromptTemplate, Recommendation,
                      Review, StudyGroup, TrainingSession, VirtualWork)
from ..permissions import display_name, role_of
from ..services import ai, analytics, prompt_engine, recommendations, state
from .base import LabView, body, text_field


def templates_payload():
    out = {}
    for t in PromptTemplate.objects.filter(is_active=True).select_related('work'):
        key = t.work.code if t.work_id else 'system'
        out[key] = {'role': t.role, 'task': t.task, 'constraints': [c.strip() for c in t.constraints.splitlines() if c.strip()],
                    'system': t.system}
    return out


class ConfigView(LabView):
    """Всё, что интерфейсу нужно знать при запуске: роль, курсы и доступные работы, шаблоны, критерии, ИИ."""
    def get(self, request):
        role = role_of(request.user)
        courses = [{'id': c.id, 'title': c.title, 'description': c.description,
                    'works': [w.code for w in c.works.filter(is_enabled=True).order_by('position', 'number')]}
                   for c in Course.objects.filter(is_active=True)]
        placed = {code for c in courses for code in c['works']}
        loose = list(VirtualWork.objects.filter(is_enabled=True).exclude(code__in=placed).order_by('number').values_list('code', flat=True))
        if loose:
            courses.append({'id': None, 'title': 'Другие работы', 'description': '', 'works': loose})
        return Response({
            'user': {'id': request.user.pk, 'login': request.user.get_username(), 'name': display_name(request.user),
                     'firstName': getattr(request.user, 'first_name', ''), 'role': role},
            'courses': courses,
            'enabledWorks': list(VirtualWork.objects.filter(is_enabled=True).values_list('code', flat=True)),
            'templates': templates_payload(),
            'criteria': [{'key': c.key, 'name': c.name, 'weight': c.weight, 'isActive': c.is_active, 'description': c.description}
                         for c in EvaluationCriterion.objects.all()],
            **ai.status(),
            'features': {'manageAccounts': bool(conf('MANAGE_ACCOUNTS')), 'maxUploadBytes': conf('MAX_UPLOAD_BYTES'),
                         'uploadExtensions': list(conf('UPLOAD_EXTENSIONS'))},
            'serverTime': timezone.now(),
        })


class StateView(LabView):
    def get(self, request):
        return Response(state.load_state(request.user))


class ProfileView(LabView):
    def put(self, request):
        try:
            return Response(state.save_profile(request.user, request.data))
        except ValueError as exc:
            raise ValidationError({'detail': str(exc)}) from None


class SettingsView(LabView):
    def put(self, request):
        if not isinstance(request.data, dict):
            raise ValidationError({'detail': 'Ожидается объект настроек'})
        return Response(state.save_settings(request.user, request.data))


class SessionView(LabView):
    def put(self, request, code):
        if not VirtualWork.objects.filter(code=code, is_enabled=True).exists() and not catalog.work(code):
            raise ValidationError({'detail': 'Работа недоступна'})
        try:
            s = state.save_session(request.user, code, request.data)
        except ValueError as exc:
            raise ValidationError({'detail': str(exc)}) from None
        return Response(state.session_dict(s))

    def delete(self, request, code):
        state.delete_session(request.user, code)
        return Response(status=204)


class PortfolioView(LabView):
    def post(self, request):
        try:
            item = state.save_portfolio_item(request.user, request.data)
        except ValueError as exc:
            raise ValidationError({'detail': str(exc)}) from None
        return Response(state.portfolio_dict(item), status=201)


class PortfolioItemView(LabView):
    def delete(self, request, client_id):
        request.user.lab_portfolio.filter(client_id=client_id).delete()
        return Response(status=204)


class EventsView(LabView):
    def post(self, request):
        events = request.data.get('events') if isinstance(request.data, dict) else request.data
        if not isinstance(events, list):
            raise ValidationError({'events': 'Ожидается список событий'})
        return Response({'saved': state.add_events(request.user, events)}, status=201)


class EvaluateView(LabView):
    """Экспертиза материала (ТЗ, п. 27). По коду работы описание берётся из каталога сервера."""
    def post(self, request):
        data = body(request)
        text = text_field(data, 'text', 300000)
        inputs = data.get('inputs') if isinstance(data.get('inputs'), dict) else {}
        from ..services import expertise
        if data.get('work'):
            mod = expertise.module_for(str(data['work']), inputs)
            if not mod:
                raise ValidationError({'work': 'Неизвестная работа'})
        else:
            mod = data.get('module')
            if not isinstance(mod, dict) or not isinstance(mod.get('sections'), list) or not mod['sections']:
                raise ValidationError({'module': 'Нужны разделы материала'})
        return Response(expertise.evaluate_module(mod, text, inputs))


class PromptAnalyzeView(LabView):
    def post(self, request):
        return Response(prompt_engine.analyze(text_field(body(request), 'text', 40000)))


class AnalyticsView(LabView):
    def get(self, request):
        return Response(analytics.user_analytics(request.user))


class NextWorkView(LabView):
    """Адаптивный модуль (ТЗ, п. 30): следующая рекомендуемая работа."""
    def get(self, request):
        from ..models import Evaluation
        user = request.user
        enabled = list(VirtualWork.objects.filter(is_enabled=True).values_list('code', flat=True))
        completed = set(Progress.objects.filter(user=user, is_completed=True).values_list('work__code', flat=True))
        avg_prompt = Progress.objects.filter(user=user, prompt_score__isnull=False).aggregate(v=Avg('prompt_score'))['v']
        evals = Evaluation.objects.filter(generation__session__user=user).values_list('criteria', flat=True)

        def avg(key):
            vals = [c['score'] for crit in evals for c in crit if c.get('key') == key]
            return sum(vals) / len(vals) if vals else None
        return Response(recommendations.next_work(completed, enabled, avg_prompt, avg('sources'), avg('science')))


class SessionRecommendationsView(LabView):
    def get(self, request, code):
        s = TrainingSession.objects.filter(user=request.user, work__code=code).first()
        rows = Recommendation.objects.filter(session=s, generation=s.current_generation) if s and s.current_generation_id else []
        return Response([{'kind': r.kind, 'text': r.text, 'createdAt': r.created_at} for r in rows])


# ---------- группы и задания глазами слушателя ----------
class JoinGroupView(LabView):
    def post(self, request):
        code = text_field(body(request), 'code', 20, required=True).upper().replace(' ', '')
        group = StudyGroup.objects.filter(invite_code=code).first()
        if not group:
            raise ValidationError({'code': 'Группа с таким кодом не найдена'})
        GroupMembership.objects.get_or_create(group=group, user=request.user)
        return Response({'id': group.pk, 'name': group.name}, status=201)


class MyGroupsView(LabView):
    def get(self, request):
        groups = StudyGroup.objects.filter(memberships__user=request.user).select_related('tutor')
        return Response([{'id': g.pk, 'name': g.name, 'description': g.description, 'tutor': display_name(g.tutor)} for g in groups])


class MyAssignmentsView(LabView):
    def get(self, request):
        rows = (Assignment.objects.filter(group__memberships__user=request.user)
                .select_related('group', 'work').order_by('due_at', '-created_at'))
        progress = {p.work_id: p for p in Progress.objects.filter(user=request.user)}
        return Response([{
            'id': a.pk, 'group': a.group.name, 'workCode': a.work.code, 'workNumber': a.work.number, 'title': a.title or a.work.title,
            'instructions': a.instructions, 'dueAt': a.due_at, 'createdAt': a.created_at,
            'completed': bool(progress.get(a.work_id) and progress[a.work_id].is_completed),
            'score': progress[a.work_id].last_score if a.work_id in progress else None,
        } for a in rows])


class MyReviewsView(LabView):
    def get(self, request):
        rows = Review.objects.filter(session__user=request.user).select_related('session__work', 'reviewer')
        return Response([{'id': r.pk, 'workCode': r.session.work.code, 'workTitle': r.session.work.title,
                          'reviewer': display_name(r.reviewer) if r.reviewer else '', 'comment': r.comment, 'grade': r.grade,
                          'createdAt': r.created_at, 'seen': bool(r.seen_at)} for r in rows])

    def post(self, request):
        Review.objects.filter(session__user=request.user, seen_at__isnull=True).update(seen_at=timezone.now())
        return Response(status=204)
