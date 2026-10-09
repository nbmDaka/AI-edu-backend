from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework import generics
from rest_framework.exceptions import ValidationError

from common.permissions import IsPlatformAdmin
from apps.education.models import Course, UserLearningItemProgress
from .models import User, UserCourseAccess
from .admin_serializers import AdminUserSerializer, AdminLearningProgressSerializer


def admin_user_queryset():
    return User.objects.select_related('learning_track').prefetch_related(Prefetch('course_grants',
        queryset=UserCourseAccess.objects.filter(is_active=True).select_related('course'))).annotate(
        track_course_count=Count('learning_track__courses', filter=Q(learning_track__courses__is_published=True), distinct=True))


def filter_id(params, name):
    value = params.get(name)
    if not value:
        return None
    try:
        value = int(value)
        if value < 1:
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise ValidationError({name: 'Ожидается положительный ID'})


@extend_schema(parameters=[OpenApiParameter('search', str), OpenApiParameter('role', str),
    OpenApiParameter('learning_track', int), OpenApiParameter('status', str), OpenApiParameter('course', int)])
class AdminUserList(generics.ListCreateAPIView):
    permission_classes = [IsPlatformAdmin]
    serializer_class = AdminUserSerializer

    def get_queryset(self):
        users = admin_user_queryset()
        params = self.request.query_params
        for token in params.get('search', '').strip().split():
            users = users.filter(Q(email__icontains=token) | Q(first_name__icontains=token) | Q(last_name__icontains=token))
        role = params.get('role')
        if role:
            if role not in User.Role.values:
                raise ValidationError({'role': 'Неизвестная роль'})
            users = users.filter(role=role)
        track_id = filter_id(params, 'learning_track')
        if track_id:
            users = users.filter(learning_track_id=track_id)
        status = params.get('status')
        if status:
            if status not in ('active', 'inactive'):
                raise ValidationError({'status': 'Выберите active или inactive'})
            users = users.filter(is_active=status == 'active')
        course_id = filter_id(params, 'course')
        if course_id:
            course = Course.objects.filter(pk=course_id, is_published=True,
                learning_track__is_active=True, learning_track__is_published=True).first()
            if not course:
                return users.none()
            granted = UserCourseAccess.objects.filter(course=course, is_active=True).values('user_id')
            users = users.filter(role='STUDENT', learning_track_id=course.learning_track_id).filter(
                Q(course_access_mode='TRACK_DEFAULT') | Q(course_access_mode='CUSTOM', pk__in=granted))
        return users.order_by('-date_joined', '-pk')


class AdminUserDetail(generics.RetrieveUpdateAPIView):
    permission_classes = [IsPlatformAdmin]
    serializer_class = AdminUserSerializer
    http_method_names = ['get', 'patch', 'put', 'head', 'options']
    queryset = admin_user_queryset()


class AdminUserLearningProgress(generics.ListAPIView):
    permission_classes = [IsPlatformAdmin]
    serializer_class = AdminLearningProgressSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return UserLearningItemProgress.objects.none()
        get_object_or_404(User, pk=self.kwargs['pk'])
        return UserLearningItemProgress.objects.filter(user_id=self.kwargs['pk']).select_related(
            'learning_item__module__course').order_by('-updated_at', '-pk')
