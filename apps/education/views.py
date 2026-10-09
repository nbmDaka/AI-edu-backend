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
from .models import LearningTrack, Course, Module, Lesson, LearningItem, PracticeSubmission
from .access import can_access_item, can_access_lesson
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
        return q.filter(is_published=True, learning_track_id=self.request.user.learning_track_id,
                        learning_track__is_published=True, learning_track__is_active=True)

class CourseDetail(AdminWriteMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = CourseSerializer
    lookup_field = 'slug'
    def get_queryset(self):
        q = Course.objects.select_related('learning_track').all()
        if is_admin(self.request.user):
            return q
        return q.filter(is_published=True, learning_track_id=self.request.user.learning_track_id,
                        learning_track__is_published=True, learning_track__is_active=True)

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
        return q.filter(is_published=True, course__is_published=True,
                        course__learning_track_id=self.request.user.learning_track_id,
                        course__learning_track__is_published=True, course__learning_track__is_active=True)

class ModuleDetail(AdminWriteMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ModuleSerializer
    lookup_field = 'short_id'
    def get_queryset(self):
        q = Module.objects.select_related('course__learning_track').all()
        if is_admin(self.request.user):
            return q
        return q.filter(is_published=True, course__is_published=True,
                        course__learning_track_id=self.request.user.learning_track_id,
                        course__learning_track__is_published=True, course__learning_track__is_active=True)

class LessonList(AdminWriteMixin, generics.ListCreateAPIView):
    serializer_class = LessonSerializer
    def get_queryset(self):
        q = Lesson.objects.select_related('module__course__learning_track').all()
        module_id = integer_query(self.request, 'module')
        if module_id:
            q = q.filter(module_id=module_id)
        if is_admin(self.request.user):
            return q
        return q.filter(status='PUBLISHED', module__is_published=True, module__course__is_published=True,
                        module__course__learning_track_id=self.request.user.learning_track_id,
                        module__course__learning_track__is_published=True, module__course__learning_track__is_active=True)

class LessonDetail(AdminWriteMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = LessonSerializer
    lookup_field = 'short_id'

    def get_object(self):
        obj = super().get_object()
        if not can_access_lesson(self.request.user, obj):
            raise PermissionDenied('Сначала завершите предыдущий модуль')
        return obj
    def get_queryset(self):
        q = Lesson.objects.select_related('module__course__learning_track').all()
        if is_admin(self.request.user):
            return q
        return q.filter(status='PUBLISHED', module__is_published=True, module__course__is_published=True,
                        module__course__learning_track_id=self.request.user.learning_track_id,
                        module__course__learning_track__is_published=True, module__course__learning_track__is_active=True)


def item_queryset(request):
    q = LearningItem.objects.select_related('module__course__learning_track', 'lesson', 'test', 'practice')
    if is_admin(request.user):
        return q
    return q.filter(status='PUBLISHED', module__is_published=True, module__course__is_published=True,
                    module__course__learning_track_id=request.user.learning_track_id,
                    module__course__learning_track__is_published=True,
                    module__course__learning_track__is_active=True)


class LectureProgressInput(serializers.Serializer):
    progress_percent = serializers.IntegerField(min_value=0, max_value=100)


class ItemProgressOutput(serializers.Serializer):
    progress_percent = serializers.IntegerField(min_value=0, max_value=100)
    is_completed = serializers.BooleanField()


class CourseProgressOutput(serializers.Serializer):
    percent = serializers.IntegerField(min_value=0, max_value=100)
    items = serializers.DictField(child=ItemProgressOutput())
    modules = serializers.DictField(child=serializers.DictField())


class CourseProgressView(generics.GenericAPIView):
    serializer_class = CourseProgressOutput
    def get(self, request, slug):
        courses = Course.objects.all() if is_admin(request.user) else Course.objects.filter(
            is_published=True, learning_track_id=request.user.learning_track_id,
            learning_track__is_published=True, learning_track__is_active=True)
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
            content = PracticeDefinition.objects.create(kind='PLACEHOLDER', config={})
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


class PracticeSubmissionInput(serializers.Serializer):
    checks = serializers.ListField(child=serializers.BooleanField(), max_length=30)

    def validate_checks(self, value):
        if any(type(check) is not bool for check in self.initial_data.get('checks', [])):
            raise serializers.ValidationError('Ожидается список логических значений')
        return value


class PracticeSubmissionOutput(serializers.Serializer):
    criteria = serializers.ListField(child=serializers.CharField())
    checks = serializers.ListField(child=serializers.BooleanField(), allow_null=True)
    progress_percent = serializers.IntegerField(required=False)


class ItemPracticeView(generics.GenericAPIView):
    serializer_class = PracticeSubmissionInput

    def get_item(self, request, short_id):
        item = item_queryset(request).filter(short_id=short_id, type='PRACTICE').first()
        if not item:
            raise NotFound()
        if not can_access_item(request.user, item):
            raise PermissionDenied('Сначала завершите предыдущий модуль')
        return item

    @extend_schema(responses=PracticeSubmissionOutput)
    def get(self, request, short_id):
        item = self.get_item(request, short_id)
        submission = PracticeSubmission.objects.filter(user=request.user, item=item).first()
        checks = submission.checks if submission and submission.criteria == item.practice_criteria else None
        return Response({'criteria': item.practice_criteria, 'checks': checks})

    @extend_schema(responses=PracticeSubmissionOutput)
    @transaction.atomic
    def post(self, request, short_id):
        from apps.accounts.models import User
        User.objects.select_for_update().get(pk=request.user.pk)
        item = self.get_item(request, short_id)
        if not item.practice_criteria:
            raise ValidationError({'detail': 'Администратор ещё не настроил критерии практической работы.'})
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        checks = serializer.validated_data['checks']
        if len(checks) != len(item.practice_criteria):
            raise ValidationError({'checks': 'Заполните все критерии практической работы'})
        PracticeSubmission.objects.update_or_create(user=request.user, item=item,
            defaults={'criteria': item.practice_criteria, 'checks': checks})
        from .models import UserLearningItemProgress
        from django.utils import timezone
        percent = round(sum(checks) / len(checks) * 100)
        UserLearningItemProgress.objects.update_or_create(user=request.user, learning_item=item,
            defaults={'progress_percent': percent, 'is_completed': all(checks),
                      'completed_at': timezone.now() if all(checks) else None})
        return Response({'criteria': item.practice_criteria, 'checks': checks, 'progress_percent': percent})
