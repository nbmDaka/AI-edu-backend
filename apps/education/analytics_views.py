from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions import IsPlatformAdmin
from .analytics import learning_analytics
from .models import Course, LearningTrack


class AnalyticsFiltersSerializer(serializers.Serializer):
    period = serializers.ChoiceField(choices=['7d', '30d', '90d', 'all'], default='30d')
    track = serializers.IntegerField(min_value=1, required=False)
    course = serializers.IntegerField(min_value=1, required=False)


class AnalyticsUsageSerializer(serializers.Serializer):
    started_students = serializers.IntegerField()
    active_students = serializers.IntegerField()
    completed_students = serializers.IntegerField()
    completion_rate = serializers.FloatField()


class AnalyticsTrackOptionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()


class AnalyticsCourseOptionSerializer(AnalyticsTrackOptionSerializer):
    short_id = serializers.CharField()
    learning_track_id = serializers.IntegerField()


class AnalyticsCourseSerializer(AnalyticsCourseOptionSerializer, AnalyticsUsageSerializer):
    module_count = serializers.IntegerField()
    item_count = serializers.IntegerField()


class AnalyticsModuleSerializer(AnalyticsTrackOptionSerializer, AnalyticsUsageSerializer):
    short_id = serializers.CharField()
    course_id = serializers.IntegerField()
    item_count = serializers.IntegerField()


class AnalyticsTrackSerializer(AnalyticsTrackOptionSerializer):
    assigned_students = serializers.IntegerField()
    started_students = serializers.IntegerField()
    active_students = serializers.IntegerField()


class AnalyticsSummarySerializer(serializers.Serializer):
    total_students = serializers.IntegerField()
    started_students = serializers.IntegerField()
    active_students = serializers.IntegerField()
    completed_students = serializers.IntegerField()
    course_count = serializers.IntegerField()
    module_count = serializers.IntegerField()
    item_count = serializers.IntegerField()


class AnalyticsOptionsSerializer(serializers.Serializer):
    tracks = AnalyticsTrackOptionSerializer(many=True)
    courses = AnalyticsCourseOptionSerializer(many=True)


class AnalyticsSelectionSerializer(serializers.Serializer):
    track = serializers.IntegerField(allow_null=True)
    course = serializers.IntegerField(allow_null=True)


class LearningAnalyticsSerializer(serializers.Serializer):
    generated_at = serializers.DateTimeField()
    period = serializers.CharField()
    since = serializers.DateTimeField(allow_null=True)
    filters = AnalyticsSelectionSerializer()
    options = AnalyticsOptionsSerializer()
    summary = AnalyticsSummarySerializer()
    tracks = AnalyticsTrackSerializer(many=True)
    courses = AnalyticsCourseSerializer(many=True)
    modules = AnalyticsModuleSerializer(many=True)


class AdminLearningAnalyticsView(APIView):
    permission_classes = [IsPlatformAdmin]

    @extend_schema(parameters=[AnalyticsFiltersSerializer], responses=LearningAnalyticsSerializer)
    def get(self, request):
        query = AnalyticsFiltersSerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        filters = query.validated_data
        track, course = filters.get('track'), filters.get('course')
        if track and not LearningTrack.objects.filter(pk=track, is_active=True, is_published=True).exists():
            raise ValidationError({'track': 'Траектория недоступна для аналитики.'})
        if course:
            courses = Course.objects.filter(pk=course, is_published=True, learning_track__deleted_at__isnull=True,
                                            learning_track__is_active=True, learning_track__is_published=True)
            if track:
                courses = courses.filter(learning_track_id=track)
            if not courses.exists():
                raise ValidationError({'course': 'Курс недоступен или не относится к выбранной траектории.'})
        response = Response(LearningAnalyticsSerializer(learning_analytics(**filters)).data)
        response['Cache-Control'] = 'private, no-store'
        return response
