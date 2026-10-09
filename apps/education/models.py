import secrets
from django.conf import settings
from django.utils.text import slugify
from django.db import models
from django.db.models import PROTECT
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator

def generate_short_id():
    # token_urlsafe(8) returns 11 chars; truncate to keep public IDs at 10 chars.
    return secrets.token_urlsafe(8)[:10]

class ShortIdModel(models.Model):
    short_id = models.CharField(max_length=10, unique=True, default=generate_short_id, editable=False)

    class Meta:
        abstract = True

class LearningTrack(ShortIdModel):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    cover = models.ForeignKey('mediafiles.MediaFile', null=True, blank=True, on_delete=models.SET_NULL)
    is_published = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def delete(self, *args, **kwargs):
        if self.is_system:
            raise ValidationError('Базовую траекторию нельзя удалить')
        return super().delete(*args, **kwargs)
    def __str__(self): return self.title

class Course(ShortIdModel):
    learning_track = models.ForeignKey(LearningTrack, null=True, blank=True, on_delete=PROTECT, related_name='courses')
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True, allow_unicode=True)
    description = models.TextField(blank=True)
    cover = models.ForeignKey('mediafiles.MediaFile', null=True, blank=True, on_delete=models.SET_NULL)
    position = models.PositiveIntegerField(default=0)
    is_published = models.BooleanField(default=False)
    adaptive_learning_enabled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['position', 'id']
    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title, allow_unicode=True)[:200] or 'course'
            candidate = base
            suffix = 2
            while Course.objects.exclude(pk=self.pk).filter(slug=candidate).exists():
                candidate = f'{base[:210 - len(str(suffix))]}-{suffix}'
                suffix += 1
            self.slug = candidate
        super().save(*args, **kwargs)
    def __str__(self): return self.title

class Module(ShortIdModel):
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['position', 'id']
    def __str__(self): return self.title


class LearningItem(ShortIdModel):
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
    practice_criteria = models.JSONField(default=list, blank=True)
    position = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    lesson = models.OneToOneField(Lesson, null=True, blank=True, on_delete=PROTECT, related_name='learning_item')
    test = models.OneToOneField('assessments.Test', null=True, blank=True, on_delete=PROTECT, related_name='learning_item')
    practice = models.OneToOneField('activities.PracticeDefinition', null=True, blank=True, on_delete=PROTECT, related_name='learning_item')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['position', 'id']
        constraints = [models.UniqueConstraint(fields=['module', 'position'], name='unique_learning_item_position')]

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
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    item = models.ForeignKey(LearningItem, on_delete=models.CASCADE)
    criteria = models.JSONField()
    checks = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'item'], name='unique_user_practice_submission')]


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
