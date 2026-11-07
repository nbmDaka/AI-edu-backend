from django.shortcuts import get_object_or_404
from django.db import transaction
from django.db.models import Q, Max
from django.http import Http404
from rest_framework import generics, serializers
from rest_framework.permissions import AllowAny
from rest_framework.exceptions import ValidationError, PermissionDenied, NotFound
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from common.permissions import is_admin, IsPlatformAdmin
from .models import LearningTrack, Course, Module, Lesson, LearningItem
from .access import accessible_courses, can_access_item, can_access_lesson
from .progress import course_progress, save_lecture_progress
from .serializers import TrackSerializer, CourseSerializer, ModuleSerializer, LessonSerializer, LearningItemSerializer
from .services import delete_content, delete_item, order_module_items, set_item_positions

def integer_query(request, name):
    value = request.query_params.get(name)
    if value is None:
        return None
    try:
        parsed = int(value)
        if parsed < 1:
            raise ValueError()
        return parsed
    except (TypeError, ValueError):
        raise ValidationError({name: 'Ожидается положительный целочисленный ID'})

class AdminWriteMixin:
    def get_permissions(self):
        if self.request.method not in ('GET', 'HEAD', 'OPTIONS'):
            return [IsPlatformAdmin()]
        return super().get_permissions()
    def perform_destroy(self, instance):
        delete_content(instance)

class TrackList(AdminWriteMixin, generics.ListCreateAPIView):
    serializer_class = TrackSerializer
    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [AllowAny()]
        return super().get_permissions()
    def get_queryset(self):
        q = LearningTrack.objects.all().order_by('id')
        if is_admin(self.request.user):
            return q
        return q.filter(is_published=True, is_active=True)

class TrackDetail(AdminWriteMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TrackSerializer
    lookup_field = 'short_id'
    def perform_destroy(self, instance):
        if instance.is_system:
            raise ValidationError({'detail': 'Базовую траекторию нельзя удалить'})
        super().perform_destroy(instance)
    def get_queryset(self):
        q = LearningTrack.objects.all()
        if is_admin(self.request.user):
            return q
        if self.request.user.is_authenticated:
            return q.filter(pk=self.request.user.learning_track_id, is_published=True, is_active=True)
        return q.filter(is_published=True, is_active=True)

class CourseList(AdminWriteMixin, generics.ListCreateAPIView):
    serializer_class = CourseSerializer
    def get_queryset(self):
        q = Course.objects.select_related('learning_track').all()
        if is_admin(self.request.user):
            track_id = integer_query(self.request, 'learning_track')
            if track_id:
                q = q.filter(learning_track_id=track_id)
            return q
        return accessible_courses(self.request.user)

class CourseDetail(AdminWriteMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = CourseSerializer
    lookup_field = 'slug'
    def get_queryset(self):
        q = Course.objects.select_related('learning_track').all()
        if is_admin(self.request.user):
            return q
        return accessible_courses(self.request.user)

    def get_object(self):
        queryset = self.filter_queryset(self.get_queryset())
        val = self.kwargs.get(self.lookup_url_kwarg or self.lookup_field)
        obj = queryset.filter(Q(short_id=val) | Q(slug=val)).first()
        if not obj:
            raise Http404("Курс не найден")
        self.check_object_permissions(self.request, obj)
        return obj

class ModuleList(AdminWriteMixin, generics.ListCreateAPIView):
    serializer_class = ModuleSerializer
    def get_queryset(self):
        q = Module.objects.select_related('course__learning_track').all()
        course_id = integer_query(self.request, 'course')
        if course_id:
            q = q.filter(course_id=course_id)
        if is_admin(self.request.user):
            return q
        return q.filter(is_published=True, course__in=accessible_courses(self.request.user))

class ModuleDetail(AdminWriteMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ModuleSerializer
    lookup_field = 'short_id'
    def get_queryset(self):
        q = Module.objects.select_related('course__learning_track').all()
        if is_admin(self.request.user):
            return q
        return q.filter(is_published=True, course__in=accessible_courses(self.request.user))

class LessonList(AdminWriteMixin, generics.ListCreateAPIView):
    serializer_class = LessonSerializer
    def get_queryset(self):
        q = Lesson.objects.select_related('module__course__learning_track', 'learning_item').all()
        module_id = integer_query(self.request, 'module')
        if module_id:
            q = q.filter(module_id=module_id)
        if is_admin(self.request.user):
            return q
        return q.filter(status='PUBLISHED', module__is_published=True, module__course__in=accessible_courses(self.request.user))

class LessonDetail(AdminWriteMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = LessonSerializer
    lookup_field = 'short_id'

    def get_object(self):
        obj = super().get_object()
        if not can_access_lesson(self.request.user, obj):
            raise PermissionDenied('Сначала завершите предыдущий модуль')
        return obj
    def get_queryset(self):
        q = Lesson.objects.select_related('module__course__learning_track', 'learning_item').all()
        if is_admin(self.request.user):
            return q
        return q.filter(status='PUBLISHED', module__is_published=True, module__course__in=accessible_courses(self.request.user))


def item_queryset(request):
    q = LearningItem.objects.select_related('module__course__learning_track', 'lesson', 'test', 'practice', 'practice_version')
    if is_admin(request.user):
        return q
    return q.filter(status='PUBLISHED', module__is_published=True, module__course__in=accessible_courses(request.user))


class LectureProgressInput(serializers.Serializer):
    progress_percent = serializers.IntegerField(min_value=0, max_value=100)


class ItemProgressOutput(serializers.Serializer):
    progress_percent = serializers.IntegerField(min_value=0, max_value=100)
    is_completed = serializers.BooleanField()
    is_started = serializers.BooleanField(required=False)


class ResumeLearningItemOutput(serializers.Serializer):
    short_id = serializers.CharField()
    title = serializers.CharField()
    type = serializers.ChoiceField(choices=LearningItem.Type.choices)


class LearningCourseSummaryOutput(serializers.Serializer):
    id = serializers.IntegerField()
    short_id = serializers.CharField()
    slug = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField()
    cover = serializers.IntegerField(allow_null=True)
    learning_track_title = serializers.CharField(allow_null=True)
    certificate_enabled = serializers.BooleanField()
    certificate_hours = serializers.IntegerField()
    percent = serializers.IntegerField()
    completed_items = serializers.IntegerField()
    total_items = serializers.IntegerField()
    is_started = serializers.BooleanField()
    is_completed = serializers.BooleanField()
    last_activity_at = serializers.DateTimeField(allow_null=True)
    resume_learning_item = ResumeLearningItemOutput(allow_null=True)


class CourseProgressOutput(LearningCourseSummaryOutput):
    items = serializers.DictField(child=ItemProgressOutput())
    modules = serializers.DictField(child=serializers.DictField())


class LearningDashboardOutput(serializers.Serializer):
    resume_course = LearningCourseSummaryOutput(allow_null=True)
    active_courses = LearningCourseSummaryOutput(many=True)
    completed_courses = LearningCourseSummaryOutput(many=True)
    available_courses = LearningCourseSummaryOutput(many=True)


class LearningDashboardView(generics.GenericAPIView):
    serializer_class = LearningDashboardOutput

    def get(self, request):
        if request.user.role != 'STUDENT':
            raise PermissionDenied()
        from .learning import learning_dashboard
        return Response(learning_dashboard(request.user, accessible_courses(request.user)))


class ReadinessHistoryOutput(serializers.Serializer):
    completed_at = serializers.DateTimeField()
    readiness = serializers.FloatField()
    passed = serializers.BooleanField()


class ReadinessModuleOutput(serializers.Serializer):
    id = serializers.IntegerField()
    short_id = serializers.CharField()
    title = serializers.CharField()
    status = serializers.ChoiceField(choices=['locked', 'available', 'repeat', 'completed'])
    threshold = serializers.IntegerField()
    attempts = serializers.IntegerField()
    readiness = serializers.FloatField(allow_null=True)
    best_readiness = serializers.FloatField(allow_null=True)
    history = ReadinessHistoryOutput(many=True)
    recommendation = serializers.DictField(allow_null=True)


class ReadinessCompetencyOutput(serializers.Serializer):
    competency = serializers.CharField()
    title = serializers.CharField()
    score = serializers.FloatField()
    is_weak = serializers.BooleanField()


class ReadinessCourseOutput(serializers.Serializer):
    id = serializers.IntegerField()
    short_id = serializers.CharField()
    slug = serializers.CharField()
    title = serializers.CharField()
    passed_modules = serializers.IntegerField()
    total_modules = serializers.IntegerField()
    average_readiness = serializers.FloatField(allow_null=True)
    modules = ReadinessModuleOutput(many=True)
    competencies = ReadinessCompetencyOutput(many=True)
    weak_topic_limit = serializers.FloatField()


class ReadinessDashboardOutput(serializers.Serializer):
    courses = ReadinessCourseOutput(many=True)


class ReadinessDashboardView(generics.GenericAPIView):
    """The student's own readiness per module, attempt history and competency profile (adaptive courses only)."""
    serializer_class = ReadinessDashboardOutput

    def get(self, request):
        if request.user.role != 'STUDENT':
            raise PermissionDenied()
        from .readiness import my_readiness
        return Response(my_readiness(request.user, accessible_courses(request.user)))


class CourseProgressView(generics.GenericAPIView):
    serializer_class = CourseProgressOutput
    def get(self, request, slug):
        courses = accessible_courses(request.user)
        course = courses.filter(Q(short_id=slug) | Q(slug=slug)).first()
        if not course:
            raise NotFound()
        return Response(course_progress(request.user, course))


class ItemProgressView(generics.GenericAPIView):
    serializer_class = LectureProgressInput

    def patch(self, request, short_id):
        item = item_queryset(request).filter(short_id=short_id).first()
        if not item:
            raise NotFound()
        if not can_access_item(request.user, item):
            raise PermissionDenied('Сначала завершите предыдущий модуль')
        if item.type != LearningItem.Type.LECTURE:
            raise ValidationError({'detail': 'Progress can only be submitted for a lecture'})
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        progress = save_lecture_progress(request.user, item, serializer.validated_data['progress_percent'])
        return Response({'progress_percent': progress.progress_percent, 'is_completed': progress.is_completed})


class LearningItemList(AdminWriteMixin, generics.ListCreateAPIView):
    serializer_class = LearningItemSerializer

    def get_queryset(self):
        q = item_queryset(self.request)
        module_id = integer_query(self.request, 'module')
        course_id = integer_query(self.request, 'course')
        if module_id:
            q = q.filter(module_id=module_id)
        if course_id:
            q = q.filter(module__course_id=course_id)
        return q

    @transaction.atomic
    def perform_create(self, serializer):
        module = serializer.validated_data['module']
        Module.objects.select_for_update().get(pk=module.pk)
        last_position = LearningItem.objects.filter(module=module).aggregate(Max('position'))['position__max']
        position = 0 if last_position is None else last_position + 1
        kind = serializer.validated_data['type']
        if kind == LearningItem.Type.TEST and LearningItem.objects.filter(module=module, type=kind).exists():
            raise ValidationError({'type': 'В модуле может быть только один тест'})
        title = serializer.validated_data['title']
        description = serializer.validated_data.get('description', '')
        if kind == LearningItem.Type.LECTURE:
            content = Lesson.objects.create(module=module, title=title, description=description, position=position)
            serializer.save(position=position, lesson=content)
        elif kind == LearningItem.Type.TEST:
            from apps.assessments.models import Test
            content = Test.objects.create(title=title, description=description)
            serializer.save(position=position, test=content)
        else:
            from apps.activities.models import PracticeDefinition
            content = PracticeDefinition.objects.create(title=title, description=description, created_by=self.request.user)
            serializer.save(position=position, practice=content)
        order_module_items(module.pk)
        serializer.instance.refresh_from_db()


class LearningItemDetail(AdminWriteMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = LearningItemSerializer
    lookup_field = 'short_id'

    def get_object(self):
        obj = super().get_object()
        if not can_access_item(self.request.user, obj):
            raise PermissionDenied('Сначала завершите предыдущий модуль')
        return obj

    def get_queryset(self):
        return item_queryset(self.request)

    @transaction.atomic
    def perform_update(self, serializer):
        Module.objects.select_for_update().get(pk=serializer.instance.module_id)
        item = LearningItem.objects.select_for_update().get(pk=serializer.instance.pk)
        data = serializer.validated_data
        if 'module' in data and data['module'].pk != item.module_id:
            raise ValidationError({'module': 'Перенос между модулями пока не поддерживается'})
        if 'type' in data and data['type'] != item.type:
            raise ValidationError({'type': 'Тип элемента нельзя изменить'})
        if item.type == 'PRACTICE':
            if any(key in data for key in ('title', 'description')):
                raise ValidationError({'detail': 'Редактируйте практику через /practice/draft/'})
            if data.get('status') == 'PUBLISHED' and not item.practice_version_id:
                raise ValidationError({'status': 'Сначала опубликуйте версию практики через /practice/draft/'})
        if 'status' in data and data['status'] == 'PUBLISHED':
            if item.type == 'LECTURE' and item.lesson.draft_data is not None:
                raise ValidationError({'status': 'Опубликуйте черновик лекции через /draft/'})
            if item.type == 'TEST' and (not item.test or not item.test.is_published):
                raise ValidationError({'status': 'Сначала опубликуйте тест с вопросами'})
        if 'position' in self.request.data:
            target = self.request.data['position']
            if isinstance(target, bool) or not isinstance(target, int):
                raise ValidationError({'position': 'Ожидается целое число'})
            siblings = list(LearningItem.objects.filter(module=item.module).order_by('position', 'id'))
            if target < 0 or target >= len(siblings):
                raise ValidationError({'position': 'Позиция вне модуля'})
            if item.type == LearningItem.Type.TEST and target != len(siblings) - 1:
                raise ValidationError({'position': 'Тест всегда должен быть последним элементом модуля'})
            content_count = sum(member.type != LearningItem.Type.TEST for member in siblings)
            if item.type != LearningItem.Type.TEST and target >= content_count:
                raise ValidationError({'position': 'Лекции и практические работы должны находиться перед тестом'})
            siblings.sort(key=lambda member: member.type == LearningItem.Type.TEST)
            siblings.remove(item)
            siblings.insert(target, item)
            set_item_positions(siblings)
            item.position = target
        updated = serializer.save(position=item.position)
        if updated.type == 'LECTURE':
            Lesson.objects.filter(pk=updated.lesson_id).update(
                module_id=updated.module_id, position=updated.position,
                status=updated.status, title=updated.title, description=updated.description)
        elif updated.type == 'TEST':
            from apps.assessments.models import Test
            Test.objects.filter(pk=updated.test_id).update(title=updated.title, description=updated.description,
                                                             is_published=updated.status == 'PUBLISHED')

    @transaction.atomic
    def perform_destroy(self, instance):
        delete_item(instance)


class ModuleThresholdSerializer(serializers.ModelSerializer):
    class Meta:
        model = Module
        fields = ['short_id', 'adaptive_threshold']
        read_only_fields = ['short_id']
        extra_kwargs = {'adaptive_threshold': {'required': True}}


class ModuleThreshold(generics.UpdateAPIView):
    """Administrators tune the adaptive pass threshold from analytics without editing the module itself."""
    serializer_class = ModuleThresholdSerializer
    permission_classes = [IsPlatformAdmin]
    lookup_field = 'short_id'
    http_method_names = ['patch', 'options']
    queryset = Module.objects.filter(deleted_at__isnull=True)

    def patch(self, request, *args, **kwargs):
        # The only writable field is required, so treat PATCH as a full update.
        return self.update(request, *args, **kwargs)
