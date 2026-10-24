from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework.validators import UniqueValidator
from .models import User
from apps.education.models import LearningTrack

class TrackRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = LearningTrack
        fields = ['id', 'short_id', 'title']

class UserSerializer(serializers.ModelSerializer):
    learning_track = TrackRefSerializer(read_only=True)
    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'role', 'learning_track', 'course_access_mode', 'date_joined']
        read_only_fields = ['id', 'email', 'role', 'course_access_mode', 'date_joined']

class RegisterSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(validators=[UniqueValidator(queryset=User.all_objects.all(), lookup='iexact')])
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    learning_track = serializers.PrimaryKeyRelatedField(queryset=LearningTrack.objects.filter(is_active=True, is_published=True), required=False)
    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name', 'password', 'learning_track']

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate(self, attrs):
        # Preserve the public registration contract: client-supplied role is ignored;
        # create() always assigns STUDENT. System privilege fields are rejected.
        forbidden = set(self.initial_data) - (set(self.Meta.fields) | {'role'})
        if forbidden:
            raise serializers.ValidationError({key: 'Это поле недоступно при регистрации' for key in forbidden})
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(**validated_data, role='STUDENT', email_verification_required=True)

class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False)

class ProfileUpdateSerializer(serializers.ModelSerializer):
    learning_track = serializers.PrimaryKeyRelatedField(queryset=LearningTrack.objects.filter(is_active=True, is_published=True), required=False, allow_null=True)
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'learning_track']

    def validate(self, attrs):
        forbidden = set(self.initial_data) - set(self.Meta.fields)
        if forbidden:
            raise serializers.ValidationError({key: 'Это поле недоступно в профиле' for key in forbidden})
        if self.instance.role == 'ADMIN' and 'learning_track' in attrs:
            raise serializers.ValidationError({'learning_track': 'Траектория назначается только студентам'})
        return attrs

    def update(self, instance, validated_data):
        from django.db import transaction
        from .services import reconcile_course_grants
        with transaction.atomic():
            instance = User.objects.select_for_update().get(pk=instance.pk)
            updated = super().update(instance, validated_data)
            reconcile_course_grants(updated)
            return updated

class PasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(trim_whitespace=False)
    new_password = serializers.CharField(trim_whitespace=False)


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField(max_length=128)
    token = serializers.CharField(max_length=128)
    new_password = serializers.CharField(trim_whitespace=False, max_length=4096)


class AuthMessageSerializer(serializers.Serializer):
    detail = serializers.CharField()


class EmailVerificationSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=1024)


class RegistrationResponseSerializer(UserSerializer):
    verification_required = serializers.BooleanField(read_only=True, default=True)
    detail = serializers.CharField(read_only=True)

    class Meta(UserSerializer.Meta):
        fields = [*UserSerializer.Meta.fields, 'verification_required', 'detail']
