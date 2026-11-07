from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema
from rest_framework import generics, serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions import IsPlatformAdmin
from .activity import daily_counts, since_for
from .models import ActivityEvent

STUDENT_FEED_LIMIT = 20


class ActivityEventSerializer(serializers.ModelSerializer):
    course_title = serializers.CharField(source='course.title', default=None, read_only=True)
    module_title = serializers.CharField(source='module.title', default=None, read_only=True)
    item_short_id = serializers.CharField(source='item.short_id', default=None, read_only=True)
    item_title = serializers.CharField(source='item.title', default=None, read_only=True)

    class Meta:
        model = ActivityEvent
        fields = ['id', 'type', 'created_at', 'course', 'course_title', 'module_title', 'item_short_id', 'item_title', 'data']


class AdminActivityEventSerializer(ActivityEventSerializer):
    user_id = serializers.IntegerField(read_only=True)
    user_email = serializers.EmailField(source='user.email', read_only=True)
    user_name = serializers.SerializerMethodField()

    class Meta(ActivityEventSerializer.Meta):
        fields = ['id', 'type', 'created_at', 'user_id', 'user_email', 'user_name',
                  'course', 'course_title', 'module_title', 'item_short_id', 'item_title', 'data']

    def get_user_name(self, event) -> str:
        return ' '.join(part for part in (event.user.first_name, event.user.last_name) if part)


def positive_int(params, name):
    value = params.get(name)
    if not value:
        return None
    if not value.isdigit() or int(value) < 1:
        raise ValidationError({name: 'Ожидается положительное целое число'})
    return int(value)


def filtered_events(params):
    events = ActivityEvent.objects.filter(user__role='STUDENT').select_related('user', 'course', 'module', 'item')
    try:
        since = since_for(params.get('period', '30d'))
    except ValueError:
        raise ValidationError({'period': 'Выберите 7d, 30d, 90d или all'})
    if since:
        events = events.filter(created_at__gte=since)
    kind = params.get('type')
    if kind:
        if kind not in ActivityEvent.Type.values:
            raise ValidationError({'type': 'Неизвестный тип события'})
        events = events.filter(type=kind)
    for name, field in (('user', 'user_id'), ('course', 'course_id')):
        value = positive_int(params, name)
        if value:
            events = events.filter(**{field: value})
    for token in params.get('search', '').strip().split():
        events = events.filter(Q(user__email__icontains=token) | Q(user__first_name__icontains=token)
                               | Q(user__last_name__icontains=token))
    return events


class AdminActivityList(generics.ListAPIView):
    """Журнал активности для администратора: фильтры period, type, user, course, search."""
    permission_classes = [IsPlatformAdmin]
    serializer_class = AdminActivityEventSerializer

    def get_queryset(self):
        return filtered_events(self.request.query_params)


class DailyActivitySerializer(serializers.Serializer):
    date = serializers.DateField()
    total = serializers.IntegerField()
    by_type = serializers.DictField(child=serializers.IntegerField())


class ActivitySummarySerializer(serializers.Serializer):
    days = DailyActivitySerializer(many=True)
    totals = serializers.DictField(child=serializers.IntegerField())
    active_users = serializers.IntegerField()


class AdminActivitySummary(APIView):
    """Totals for the chosen period and a 14-day chart; both use the same filters as the list."""
    permission_classes = [IsPlatformAdmin]

    @extend_schema(responses=ActivitySummarySerializer)
    def get(self, request):
        events = filtered_events(request.query_params).order_by()
        totals = {kind: 0 for kind in ActivityEvent.Type.values}
        for row in events.values('type').annotate(count=Count('id')):
            totals[row['type']] = row['count']
        chart_params = request.query_params.copy()
        chart_params['period'] = 'all'
        return Response({'days': daily_counts(filtered_events(chart_params).order_by()), 'totals': totals,
                         'active_users': events.values('user_id').distinct().count()})


class MyActivity(APIView):
    """The student's own latest actions for the readiness dashboard."""

    @extend_schema(responses=ActivityEventSerializer(many=True))
    def get(self, request):
        if request.user.role != 'STUDENT':
            raise PermissionDenied()
        events = (ActivityEvent.objects.filter(user=request.user).exclude(type=ActivityEvent.Type.LOGIN)
                  .select_related('course', 'module', 'item')[:STUDENT_FEED_LIMIT])
        return Response(ActivityEventSerializer(events, many=True).data)
