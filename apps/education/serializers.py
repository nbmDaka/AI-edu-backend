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
        fields = ['id', 'short_id', 'learning_track', 'title', 'slug', 'description', 'cover', 'position', 'is_published', 'adaptive_learning_enabled', 'created_at', 'updated_at']
        read_only_fields = ['id', 'short_id', 'created_at', 'updated_at']
    def validate(self, attrs):
        track = attrs.get('learning_track', getattr(self.instance, 'learning_track', None))
        if track is None:
            raise serializers.ValidationError({'learning_track': 'Выберите траекторию обучения'})
        slug = attrs.get('slug')
        if slug:
            matches = Course.objects.filter(slug=slug)
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
    is_locked = serializers.SerializerMethodField()
    def get_is_locked(self, obj) -> bool:
        return request_module_is_locked(self.context.get('request'), obj.module)
    class Meta:
        model = Lesson
        fields = ['id', 'short_id', 'module', 'title', 'description', 'position', 'status', 'is_locked', 'created_at', 'updated_at']
        read_only_fields = ['id', 'short_id', 'created_at', 'updated_at']


class LearningItemSerializer(serializers.ModelSerializer):
    is_locked = serializers.SerializerMethodField()
    def get_is_locked(self, obj) -> bool:
        return request_module_is_locked(self.context.get('request'), obj.module)
    practice_criteria = serializers.ListField(child=serializers.CharField(max_length=300), max_length=30, required=False)
    lesson_short_id = serializers.CharField(source='lesson.short_id', read_only=True, allow_null=True)
    module_short_id = serializers.CharField(source='module.short_id', read_only=True)
    module_title = serializers.CharField(source='module.title', read_only=True)
    module_position = serializers.IntegerField(source='module.position', read_only=True)
    course_id = serializers.IntegerField(source='module.course.id', read_only=True)
    course_short_id = serializers.CharField(source='module.course.short_id', read_only=True)
    course_title = serializers.CharField(source='module.course.title', read_only=True)
    class Meta:
        model = LearningItem
        fields = ['id', 'short_id', 'module', 'type', 'title', 'description', 'practice_criteria', 'position', 'status', 'is_locked',
                  'module_short_id', 'module_title', 'module_position', 'course_id', 'course_short_id',
                  'course_title', 'lesson', 'lesson_short_id', 'test', 'practice', 'created_at', 'updated_at']
        read_only_fields = ['id', 'short_id', 'lesson', 'lesson_short_id', 'test', 'practice', 'created_at', 'updated_at']
        validators = []  # Position is assigned and validated under a module lock in the view.

    def validate(self, attrs):
        kind = attrs.get('type', getattr(self.instance, 'type', None))
        if attrs.get('practice_criteria') and kind != 'PRACTICE':
            raise serializers.ValidationError({'practice_criteria': 'Критерии доступны только для практической работы'})
        return attrs

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data['is_locked']:
            data['practice_criteria'] = []
        return data
