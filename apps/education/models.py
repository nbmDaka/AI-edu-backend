import secrets
import uuid
from django.conf import settings
from django.utils.text import slugify
from django.db import models
from django.utils import timezone
from django.db.models import PROTECT
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from common.soft_delete import SoftDeleteModel

def generate_short_id():
    # token_urlsafe(8) returns 11 chars; truncate to keep public IDs at 10 chars.
    return secrets.token_urlsafe(8)[:10]

class ShortIdModel(SoftDeleteModel):
    short_id = models.CharField(max_length=10, unique=True, default=generate_short_id, editable=False)

    class Meta:
        abstract = True

class LearningTrack(ShortIdModel):
    soft_delete_cascade = ('courses',)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    cover = models.ForeignKey('mediafiles.MediaFile', null=True, blank=True, on_delete=models.SET_NULL)
    is_published = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def validate_soft_delete(self):
        if self.is_system:
            raise ValidationError('Базовую траекторию нельзя удалить')
    def __str__(self): return self.title

class Course(ShortIdModel):
    soft_delete_cascade = ('modules',)
    learning_track = models.ForeignKey(LearningTrack, null=True, blank=True, on_delete=PROTECT, related_name='courses')
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True, allow_unicode=True)
    description = models.TextField(blank=True)
    cover = models.ForeignKey('mediafiles.MediaFile', null=True, blank=True, on_delete=models.SET_NULL)
    position = models.PositiveIntegerField(default=0)
    is_published = models.BooleanField(default=False)
    adaptive_learning_enabled = models.BooleanField(default=False)
    certificate_enabled = models.BooleanField(default=False)
    certificate_hours = models.PositiveSmallIntegerField(default=72, validators=[MinValueValidator(1), MaxValueValidator(1000)])
    certificate_module_count = models.PositiveSmallIntegerField(default=3, validators=[MinValueValidator(1), MaxValueValidator(50)])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['position', 'id']
    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title, allow_unicode=True)[:200] or 'course'
            candidate = base
            suffix = 2
            while Course.all_objects.exclude(pk=self.pk).filter(slug=candidate).exists():
                candidate = f'{base[:210 - len(str(suffix))]}-{suffix}'
                suffix += 1
            self.slug = candidate
        super().save(*args, **kwargs)
    def __str__(self): return self.title

class Module(ShortIdModel):
    soft_delete_cascade = ('items', 'lessons')
    course = models.ForeignKey(Course, on_delete=PROTECT, related_name='modules')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    position = models.PositiveIntegerField(default=0)
    is_published = models.BooleanField(default=False)
    adaptive_threshold = models.PositiveSmallIntegerField(
        default=60, validators=[MinValueValidator(40), MaxValueValidator(80)])
    class Meta:
        ordering = ['position', 'id']
    def __str__(self): return self.title

class Lesson(ShortIdModel):
    soft_delete_cascade = ('learning_item',)

    def soft_delete_related(self):
        yield from super().soft_delete_related()
        # Independent test/practice items survive removal of their old lecture.
        from apps.assessments.models import Test
        from apps.activities.models import PracticeDefinition
        yield from Test.objects.using(self._state.db).filter(lesson=self, learning_item__isnull=True)
        yield from PracticeDefinition.objects.using(self._state.db).filter(lesson=self, learning_item__isnull=True)

    class Status(models.TextChoices):
        DRAFT = 'DRAFT'
        PUBLISHED = 'PUBLISHED'
    module = models.ForeignKey(Module, on_delete=PROTECT, related_name='lessons')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    position = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    # Admin edits are staged here so autosave can never change student-visible content.
    draft_data = models.JSONField(null=True, blank=True, default=None)
    draft_revision = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['position', 'id']
    def __str__(self): return self.title


class LearningItem(ShortIdModel):
    soft_delete_cascade = ('lesson', 'test', 'practice')
    class Type(models.TextChoices):
        LECTURE = 'LECTURE', 'Лекция'
        TEST = 'TEST', 'Тест'
        PRACTICE = 'PRACTICE', 'Практика'

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Черновик'
        PUBLISHED = 'PUBLISHED', 'Опубликован'

    module = models.ForeignKey(Module, on_delete=PROTECT, related_name='items')
    type = models.CharField(max_length=10, choices=Type.choices)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    practice_version = models.ForeignKey('activities.PracticeVersion', null=True, blank=True, on_delete=PROTECT, related_name='learning_items')
    position = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    lesson = models.OneToOneField(Lesson, null=True, blank=True, on_delete=PROTECT, related_name='learning_item')
    test = models.OneToOneField('assessments.Test', null=True, blank=True, on_delete=PROTECT, related_name='learning_item')
    practice = models.OneToOneField('activities.PracticeDefinition', null=True, blank=True, on_delete=PROTECT, related_name='learning_item')
    # Topics (test competencies) this lecture or practice teaches; used to point a learner at what to review.
    competencies = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['position', 'id']
        constraints = [models.UniqueConstraint(fields=['module', 'position'], condition=models.Q(deleted_at__isnull=True), name='unique_learning_item_position')]

    def __str__(self): return self.title


class UserModuleProgress(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    module = models.ForeignKey(Module, on_delete=models.CASCADE)
    is_completed = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)
    readiness = models.FloatField(default=0)
    best_readiness = models.FloatField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'module'], name='unique_user_module_progress')]


class UserCompetencyProgress(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    competency = models.CharField(max_length=100)
    score = models.FloatField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'course', 'competency'], name='unique_user_course_competency')]


class PracticeSubmission(models.Model):
    class Status(models.TextChoices):
        NOT_STARTED = 'NOT_STARTED'
        IN_PROGRESS = 'IN_PROGRESS'
        SUBMITTED = 'SUBMITTED'
        PASSED = 'PASSED'
        FAILED = 'FAILED'
        NEEDS_REVISION = 'NEEDS_REVISION'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    item = models.ForeignKey(LearningItem, on_delete=models.CASCADE)
    practice_version = models.ForeignKey('activities.PracticeVersion', on_delete=PROTECT, related_name='submissions')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_PROGRESS)
    started_at = models.DateTimeField(default=timezone.now, editable=False)
    submitted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    submission_data = models.JSONField(default=dict)
    criteria_snapshot = models.JSONField(default=list)
    is_current = models.BooleanField(default=True)
    criteria = models.JSONField(default=list)  # Legacy snapshots, retained during upgrade.
    checks = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'item'], condition=models.Q(status='IN_PROGRESS'), name='unique_active_practice_submission')]


class UserLearningItemProgress(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='learning_progress')
    learning_item = models.ForeignKey(LearningItem, on_delete=models.CASCADE, related_name='user_progress')
    progress_percent = models.PositiveSmallIntegerField(default=0)
    is_completed = models.BooleanField(default=False)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'learning_item'], name='unique_user_learning_item_progress')]


class Certificate(models.Model):
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=PROTECT, related_name='certificates')
    course = models.ForeignKey(Course, on_delete=PROTECT, related_name='certificates')
    learner_name = models.CharField(max_length=301)
    course_title = models.CharField(max_length=200)
    track_title = models.CharField(max_length=200)
    hours = models.PositiveSmallIntegerField()
    completed_at = models.DateTimeField()
    issued_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'course'], name='unique_user_course_certificate')]


class ActivityEvent(models.Model):
    """Activity log: one row per meaningful learner action (not per progress tick)."""
    class Type(models.TextChoices):
        LOGIN = 'LOGIN'
        LECTURE_STARTED = 'LECTURE_STARTED'
        LECTURE_COMPLETED = 'LECTURE_COMPLETED'
        PRACTICE_SUBMITTED = 'PRACTICE_SUBMITTED'
        TEST_SUBMITTED = 'TEST_SUBMITTED'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='activity_events')
    type = models.CharField(max_length=24, choices=Type.choices)
    course = models.ForeignKey(Course, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    module = models.ForeignKey(Module, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    item = models.ForeignKey(LearningItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['user', '-created_at'], name='activity_user_created'),
                   models.Index(fields=['type', '-created_at'], name='activity_type_created')]
