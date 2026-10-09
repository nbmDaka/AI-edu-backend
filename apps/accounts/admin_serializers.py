from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.db.models import Q
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from common.permissions import is_admin
from apps.education.models import Course, LearningTrack, UserLearningItemProgress
from .models import User
from .services import reconcile_course_grants


def selected_course_ids(user):
    return [grant.course_id for grant in user.course_grants.all()
            if grant.is_active and grant.course.learning_track_id == user.learning_track_id and grant.course.is_published]


class CourseIDsField(serializers.ListField):
    def get_attribute(self, instance):
        return selected_course_ids(instance)


class AdminUserSerializer(serializers.ModelSerializer):
    learning_track = serializers.PrimaryKeyRelatedField(
        queryset=LearningTrack.objects.filter(is_active=True, is_published=True), required=False, allow_null=True)
    learning_track_title = serializers.CharField(source='learning_track.title', read_only=True, allow_null=True)
    password = serializers.CharField(write_only=True, required=False, min_length=8, trim_whitespace=False)
    course_ids = CourseIDsField(child=serializers.IntegerField(min_value=1), required=False, max_length=1000)
    accessible_course_count = serializers.SerializerMethodField()
    is_protected = serializers.BooleanField(source='is_superuser', read_only=True)

    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'role', 'is_active', 'learning_track',
                  'learning_track_title', 'course_access_mode', 'course_ids', 'accessible_course_count',
                  'is_protected', 'date_joined', 'last_login', 'password']
        read_only_fields = ['id', 'date_joined', 'last_login']

    def get_accessible_course_count(self, obj) -> int:
        if obj.role != 'STUDENT' or not obj.learning_track_id or not obj.learning_track.is_active or not obj.learning_track.is_published:
            return 0
        if obj.course_access_mode == 'CUSTOM':
            return len(selected_course_ids(obj))
        count = getattr(obj, 'track_course_count', None)
        return count if count is not None else obj.learning_track.courses.filter(is_published=True).count()

    def to_internal_value(self, data):
        if isinstance(data, dict):
            writable = {name for name, field in self.fields.items() if not field.read_only}
            forbidden = set(data) - writable
            if forbidden:
                raise serializers.ValidationError({name: 'Это поле нельзя изменять через API платформы' for name in forbidden})
        return super().to_internal_value(data)

    def validate_email(self, value):
        value = value.strip().lower()
        duplicates = User.objects.filter(email__iexact=value)
        if self.instance:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError('Пользователь с таким email уже существует')
        return value

    def validate(self, attrs):
        current = self.instance
        actor = self.context['request'].user
        if current and current.is_superuser:
            raise serializers.ValidationError({'detail': 'Этот системный аккаунт доступен только для просмотра'})
        if current and current.pk == actor.pk and (
                attrs.get('is_active') is False or attrs.get('role', current.role) != current.role):
            raise serializers.ValidationError({'detail': 'Нельзя деактивировать свой аккаунт или изменить собственную роль'})
        role = attrs.get('role', current.role if current else 'STUDENT')
        track = attrs.get('learning_track', current.learning_track if current else None)
        mode = attrs.get('course_access_mode', current.course_access_mode if current else 'TRACK_DEFAULT')
        course_ids = attrs.get('course_ids')
        if role == 'STUDENT' and not track:
            raise serializers.ValidationError({'learning_track': 'Для студента обязательна траектория обучения'})
        if role == 'ADMIN':
            if attrs.get('learning_track') is not None or attrs.get('course_access_mode', 'TRACK_DEFAULT') != 'TRACK_DEFAULT' or course_ids:
                raise serializers.ValidationError({'learning_track': 'Доступ к обучению настраивается только для студентов'})
            attrs.update(learning_track=None, course_access_mode='TRACK_DEFAULT')
        if course_ids is not None:
            if len(set(course_ids)) != len(course_ids):
                raise serializers.ValidationError({'course_ids': 'Курсы не должны повторяться'})
            if course_ids and mode != 'CUSTOM':
                raise serializers.ValidationError({'course_ids': 'Для назначения отдельных курсов выберите режим CUSTOM'})
            available = Course.objects.filter(pk__in=course_ids, learning_track=track, is_published=True,
                learning_track__is_active=True, learning_track__is_published=True).count()
            if available != len(course_ids):
                raise serializers.ValidationError({'course_ids': 'Выберите опубликованные курсы текущей доступной траектории'})
        if not current and 'password' not in attrs:
            raise serializers.ValidationError({'password': 'Укажите пароль нового пользователя'})
        if 'password' in attrs:
            candidate = User(email=attrs.get('email', current.email if current else ''),
                             first_name=attrs.get('first_name', ''), last_name=attrs.get('last_name', ''))
            validate_password(attrs['password'], candidate)
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        course_ids = validated_data.pop('course_ids', [])
        user = User.objects.create_user(**validated_data, is_staff=False, is_superuser=False)
        reconcile_course_grants(user, course_ids, self.context['request'].user)
        return user

    @transaction.atomic
    def update(self, instance, validated_data):
        # Serialize admin changes and recheck the acting account after acquiring the lock.
        list(User.objects.select_for_update().filter(Q(role='ADMIN') | Q(is_superuser=True)).order_by('pk'))
        actor = User.objects.get(pk=self.context['request'].user.pk)
        if not is_admin(actor):
            raise PermissionDenied()
        instance = User.objects.select_for_update().get(pk=instance.pk)
        self.instance = instance
        validated_data = self.validate(validated_data)
        course_ids = validated_data.pop('course_ids', None)
        password = validated_data.pop('password', None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if password:
            instance.set_password(password)
        instance.save()
        reconcile_course_grants(instance, course_ids, actor)
        instance._prefetched_objects_cache = {}
        return instance


class AdminLearningProgressSerializer(serializers.ModelSerializer):
    item_title = serializers.CharField(source='learning_item.title', read_only=True)
    item_type = serializers.CharField(source='learning_item.type', read_only=True)
    course_title = serializers.CharField(source='learning_item.module.course.title', read_only=True)
    module_title = serializers.CharField(source='learning_item.module.title', read_only=True)
    class Meta:
        model = UserLearningItemProgress
        fields = ['id', 'item_title', 'item_type', 'course_title', 'module_title',
                  'progress_percent', 'is_completed', 'completed_at', 'updated_at']
