from rest_framework import serializers
from .models import LearningTrack, Course, Module, Lesson, LearningItem
from .adaptive import request_module_is_locked

class TrackSerializer(serializers.ModelSerializer):
    class Meta:
        model = LearningTrack
        fields = ['id', 'short_id', 'title', 'description', 'cover', 'is_published', 'is_active', 'is_system', 'created_at', 'updated_at']
        read_only_fields = ['id', 'short_id', 'is_system', 'created_at', 'updated_at']

class CourseSerializer(serializers.ModelSerializer):
    slug = serializers.SlugField(required=False, allow_blank=True, allow_unicode=True)
    class Meta:
        model = Course
        fields = ['id', 'short_id', 'learning_track', 'title', 'slug', 'description', 'cover', 'position', 'is_published', 'adaptive_learning_enabled', 'certificate_enabled', 'certificate_hours', 'certificate_module_count', 'created_at', 'updated_at']
        read_only_fields = ['id', 'short_id', 'created_at', 'updated_at']
    def validate(self, attrs):
        track = attrs.get('learning_track', getattr(self.instance, 'learning_track', None))
        if track is None:
            raise serializers.ValidationError({'learning_track': 'Выберите траекторию обучения'})
        slug = attrs.get('slug')
        if slug:
            matches = Course.all_objects.filter(slug=slug)
            if self.instance:
                matches = matches.exclude(pk=self.instance.pk)
            if matches.exists():
                raise serializers.ValidationError({'slug': 'Такой адрес курса уже используется'})
        return attrs

class ModuleSerializer(serializers.ModelSerializer):
    is_locked = serializers.SerializerMethodField()
    def get_is_locked(self, obj) -> bool:
        return request_module_is_locked(self.context.get('request'), obj)
    class Meta:
        model = Module
        fields = ['id', 'short_id', 'course', 'title', 'description', 'position', 'is_published', 'adaptive_threshold', 'is_locked']
        read_only_fields = ['id', 'short_id']

class LessonSerializer(serializers.ModelSerializer):
    learning_item_short_id = serializers.CharField(source='learning_item.short_id', read_only=True, allow_null=True, default=None)
    is_locked = serializers.SerializerMethodField()
    def get_is_locked(self, obj) -> bool:
        return request_module_is_locked(self.context.get('request'), obj.module)
    class Meta:
        model = Lesson
        fields = ['id', 'short_id', 'learning_item_short_id', 'module', 'title', 'description', 'position', 'status', 'is_locked', 'created_at', 'updated_at']
        read_only_fields = ['id', 'short_id', 'created_at', 'updated_at']


class LearningItemSerializer(serializers.ModelSerializer):
    competency_keys = serializers.ListField(child=serializers.CharField(max_length=100), max_length=50, required=False)

    def validate_competency_keys(self, values):
        return sorted(set(value.strip().casefold() for value in values))

    is_locked = serializers.SerializerMethodField()
    def get_is_locked(self, obj) -> bool:
        return request_module_is_locked(self.context.get('request'), obj.module)
    lesson_short_id = serializers.CharField(source='lesson.short_id', read_only=True, allow_null=True)
    module_short_id = serializers.CharField(source='module.short_id', read_only=True)
    module_title = serializers.CharField(source='module.title', read_only=True)
    module_position = serializers.IntegerField(source='module.position', read_only=True)
    course_id = serializers.IntegerField(source='module.course.id', read_only=True)
    course_short_id = serializers.CharField(source='module.course.short_id', read_only=True)
    course_title = serializers.CharField(source='module.course.title', read_only=True)
    class Meta:
        model = LearningItem
        fields = ['id', 'short_id', 'module', 'type', 'title', 'description', 'competency_keys', 'practice_version', 'position', 'status', 'is_locked',
                  'module_short_id', 'module_title', 'module_position', 'course_id', 'course_short_id',
                  'course_title', 'lesson', 'lesson_short_id', 'test', 'practice', 'created_at', 'updated_at']
        read_only_fields = ['id', 'short_id', 'lesson', 'lesson_short_id', 'test', 'practice', 'practice_version', 'created_at', 'updated_at']
        validators = []  # Position is assigned and validated under a module lock in the view.

    def validate(self, attrs):
        if 'practice_criteria' in self.initial_data or 'practice_version' in self.initial_data:
            raise serializers.ValidationError({'detail': 'Используйте редактор версий /practice/draft/'})
        if attrs.get('competency_keys') and attrs.get('type', getattr(self.instance, 'type', None)) == 'TEST':
            raise serializers.ValidationError({'competency_keys': 'Связи рекомендаций доступны только для лекций и практик'})
        if attrs.get('type') == 'PRACTICE' and attrs.get('status') == 'PUBLISHED':
            raise serializers.ValidationError({'status': 'Сначала настройте и опубликуйте версию практики'})
        return attrs
