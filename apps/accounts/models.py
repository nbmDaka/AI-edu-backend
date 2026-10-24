from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from common.soft_delete import SoftDeleteManager, SoftDeleteModel

class UserManager(SoftDeleteManager, BaseUserManager):
    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError('Email is required')
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.update(role='ADMIN', is_staff=True, is_superuser=True, is_active=True)
        return self.create_user(email, password, **extra)

class User(SoftDeleteModel, AbstractBaseUser, PermissionsMixin):
    soft_delete_updates = {'is_active': False}
    class Role(models.TextChoices):
        STUDENT = 'STUDENT'
        ADMIN = 'ADMIN'
    class CourseAccessMode(models.TextChoices):
        TRACK_DEFAULT = 'TRACK_DEFAULT'
        CUSTOM = 'CUSTOM'
    email = models.EmailField(unique=True)
    email_verification_required = models.BooleanField(default=False, editable=False)
    email_verified_at = models.DateTimeField(null=True, blank=True, editable=False)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.STUDENT)
    learning_track = models.ForeignKey('education.LearningTrack', null=True, blank=True, on_delete=models.SET_NULL, related_name='students')
    course_access_mode = models.CharField(max_length=20, choices=CourseAccessMode.choices, default=CourseAccessMode.TRACK_DEFAULT)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []
    objects = UserManager()

    def __str__(self):
        return self.email


class UserCourseAccess(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='course_grants')
    course = models.ForeignKey('education.Course', on_delete=models.CASCADE, related_name='access_grants')
    granted_at = models.DateTimeField(auto_now_add=True)
    granted_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='issued_course_grants')
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'course'], name='unique_user_course_access')]
