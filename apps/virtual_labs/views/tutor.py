"""Кабинет тьютора (ТЗ, п. 8.2): группы, назначение заданий, отслеживание выполнения,
просмотр результатов, комментарии и оценки, аналитика.

Тьютор видит только слушателей своих групп. Администратор видит всех.
"""
from datetime import datetime, time

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_date
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from ..models import Assignment, GroupMembership, Progress, Review, StudyGroup, TrainingSession, VirtualWork
from ..permissions import ADMIN, IsTutor, display_name, role_of
from ..services import analytics, state
from .base import LabView, body, int_field, text_field


def visible_groups(user):
    qs = StudyGroup.objects.all() if role_of(user) == ADMIN else StudyGroup.objects.filter(tutor=user)
    return qs.select_related('tutor')


def visible_user_ids(user):
    """Слушатели, доступные тьютору: участники его групп (администратору — все)."""
    if role_of(user) == ADMIN:
        return None
    return set(GroupMembership.objects.filter(group__tutor=user).values_list('user_id', flat=True))


def can_see_user(viewer, user_id):
    ids = visible_user_ids(viewer)
    return ids is None or user_id in ids


def group_dict(g, with_members=False):
    out = {'id': g.pk, 'name': g.name, 'description': g.description, 'inviteCode': g.invite_code,
           'tutor': display_name(g.tutor), 'members': g.memberships.count(), 'assignments': g.assignments.count(),
           'createdAt': g.created_at}
    if with_members:
        out['memberList'] = [{'id': m.user_id, 'login': m.user.get_username(), 'name': display_name(m.user), 'joinedAt': m.joined_at}
                             for m in g.memberships.select_related('user').order_by('joined_at')]
    return out


class TutorView(LabView):
    permission_classes = [IsTutor]


class GroupsView(TutorView):
    def get(self, request):
        return Response([group_dict(g) for g in visible_groups(request.user)])

    def post(self, request):
        data = body(request)
        g = StudyGroup.objects.create(tutor=request.user, name=text_field(data, 'name', 200, required=True),
                                      description=text_field(data, 'description', 2000))
        return Response(group_dict(g, True), status=201)


class GroupDetailView(TutorView):
    def get_group(self, request, pk):
        return get_object_or_404(visible_groups(request.user), pk=pk)

    def get(self, request, pk):
        return Response(group_dict(self.get_group(request, pk), True))

    def patch(self, request, pk):
        g = self.get_group(request, pk)
        data = body(request)
        if 'name' in data:
            g.name = text_field(data, 'name', 200, required=True)
        if 'description' in data:
            g.description = text_field(data, 'description', 2000)
        g.save()
        return Response(group_dict(g, True))

    def delete(self, request, pk):
        self.get_group(request, pk).delete()
        return Response(status=204)


class GroupMembersView(TutorView):
    def post(self, request, pk):
        g = get_object_or_404(visible_groups(request.user), pk=pk)
        login = text_field(body(request), 'login', 254, required=True)
        User = get_user_model()
        user = User.objects.filter(**{f'{User.USERNAME_FIELD}__iexact': login}).first()
        if not user:
            raise ValidationError({'login': 'Пользователь не найден'})
        GroupMembership.objects.get_or_create(group=g, user=user)
        return Response(group_dict(g, True), status=201)


class GroupMemberDetailView(TutorView):
    def delete(self, request, pk, user_id):
        g = get_object_or_404(visible_groups(request.user), pk=pk)
        GroupMembership.objects.filter(group=g, user_id=user_id).delete()
        return Response(status=204)


def assignment_dict(a):
    members = list(a.group.memberships.values_list('user_id', flat=True))
    done = Progress.objects.filter(work=a.work, user_id__in=members, is_completed=True).count()
    return {'id': a.pk, 'groupId': a.group_id, 'group': a.group.name, 'workCode': a.work.code, 'workNumber': a.work.number,
            'workTitle': a.work.title, 'title': a.title or a.work.title, 'instructions': a.instructions, 'dueAt': a.due_at,
            'createdAt': a.created_at, 'members': len(members), 'completed': done}


def parse_due(value):
    if value in (None, ''):
        return None
    if not isinstance(value, str):
        raise ValidationError({'dueAt': 'Ожидается дата'})
    value = value.strip()
    # Дата без времени означает конец дня. Проверяется до parse_datetime: в Python 3.11+
    # datetime.fromisoformat принимает «ГГГГ-ММ-ДД» и вернул бы полночь, то есть начало дня.
    try:
        d = parse_date(value) if len(value) == 10 else None
    except ValueError:
        d = None
    if d is not None:
        dt = datetime.combine(d, time(23, 59, 59))
    else:
        try:
            dt = parse_datetime(value)
        except ValueError:
            dt = None
        if dt is None:
            raise ValidationError({'dueAt': 'Ожидается дата в формате ГГГГ-ММ-ДД'})
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt


class AssignmentsView(TutorView):
    def get(self, request):
        qs = Assignment.objects.filter(group__in=visible_groups(request.user)).select_related('group', 'work')
        if request.query_params.get('group'):
            qs = qs.filter(group_id=request.query_params['group'])
        return Response([assignment_dict(a) for a in qs])

    def post(self, request):
        data = body(request)
        group = get_object_or_404(visible_groups(request.user), pk=int_field(data, 'groupId', 1) or 0)
        work = VirtualWork.objects.filter(code=data.get('workCode'), is_enabled=True).first()
        if not work:
            raise ValidationError({'workCode': 'Выберите виртуальную работу'})
        a = Assignment.objects.create(group=group, work=work, title=text_field(data, 'title', 300),
                                      instructions=text_field(data, 'instructions', 5000), due_at=parse_due(data.get('dueAt')),
                                      created_by=request.user)
        return Response(assignment_dict(a), status=201)


class AssignmentDetailView(TutorView):
    def delete(self, request, pk):
        get_object_or_404(Assignment, pk=pk, group__in=visible_groups(request.user)).delete()
        return Response(status=204)


class OverviewView(TutorView):
    """Аналитика по слушателям (ТЗ, п. 33): тьютору — по его группам, администратору — по всем."""
    def get(self, request):
        ids = visible_user_ids(request.user)
        group_id = request.query_params.get('group')
        if group_id:
            g = get_object_or_404(visible_groups(request.user), pk=group_id)
            ids = set(g.memberships.values_list('user_id', flat=True))
        User = get_user_model()
        users = User.objects.filter(is_active=True)
        if ids is not None:
            users = users.filter(pk__in=ids)
        else:
            users = users.filter(Q(lab_sessions__isnull=False) | Q(lab_memberships__isnull=False)).distinct()
        return Response([analytics.user_analytics(u) for u in users.order_by(User.USERNAME_FIELD)[:500]])


class SessionsView(TutorView):
    """Результаты слушателей для проверки: фильтр по группе, работе и статусу."""
    def get(self, request):
        qs = TrainingSession.objects.select_related('user', 'work', 'current_generation__evaluation').prefetch_related('reviews')
        ids = visible_user_ids(request.user)
        if request.query_params.get('group'):
            g = get_object_or_404(visible_groups(request.user), pk=request.query_params['group'])
            ids = set(g.memberships.values_list('user_id', flat=True))
        if ids is not None:
            qs = qs.filter(user_id__in=ids)
        if request.query_params.get('work'):
            qs = qs.filter(work__code=request.query_params['work'])
        if request.query_params.get('status') in TrainingSession.Status.values:
            qs = qs.filter(status=request.query_params['status'])
        if request.query_params.get('unreviewed') == '1':
            qs = qs.filter(reviews__isnull=True)
        return Response([analytics.session_summary(s) for s in qs.order_by('-updated_at')[:300]])


class SessionDetailView(TutorView):
    def get_session(self, request, pk):
        s = get_object_or_404(TrainingSession.objects.select_related('user', 'work'), pk=pk)
        if not can_see_user(request.user, s.user_id):
            raise PermissionDenied('Слушатель не состоит в ваших группах.')
        return s

    def get(self, request, pk):
        s = self.get_session(request, pk)
        data = state.session_dict(s)
        data.update({'summary': analytics.session_summary(s),
                     'recommendations': [{'kind': r.kind, 'text': r.text} for r in s.recommendations.filter(generation=s.current_generation)],
                     'reviews': [{'id': r.pk, 'grade': r.grade, 'comment': r.comment, 'reviewer': display_name(r.reviewer) if r.reviewer else '',
                                  'createdAt': r.created_at} for r in s.reviews.select_related('reviewer')]})
        return Response(data)


class ReviewsView(SessionDetailView):
    def post(self, request, pk):
        s = self.get_session(request, pk)
        data = body(request)
        comment = text_field(data, 'comment', 5000)
        grade = int_field(data, 'grade', 0, 100)
        if not comment and grade is None:
            raise ValidationError({'comment': 'Напишите комментарий или поставьте оценку'})
        assignment = Assignment.objects.filter(work=s.work, group__memberships__user=s.user,
                                               group__in=visible_groups(request.user)).order_by('-created_at').first()
        r = Review.objects.create(session=s, reviewer=request.user, comment=comment, grade=grade, assignment=assignment)
        return Response({'id': r.pk, 'grade': r.grade, 'comment': r.comment, 'reviewer': display_name(request.user),
                         'createdAt': r.created_at}, status=201)


class UserSearchView(TutorView):
    def get(self, request):
        q = (request.query_params.get('q') or '').strip()
        if len(q) < 2:
            return Response([])
        User = get_user_model()
        field = User.USERNAME_FIELD
        cond = Q(**{f'{field}__icontains': q}) | Q(lab_profile__fio__icontains=q)
        users = User.objects.filter(cond, is_active=True).distinct()[:20]
        return Response([{'id': u.pk, 'login': u.get_username(), 'name': display_name(u)} for u in users])
