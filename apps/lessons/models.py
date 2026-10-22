import uuid
from django.conf import settings
from django.db import models


def import_upload_path(instance, filename):
    from pathlib import Path
    return f'lecture-imports/{instance.id}/source{Path(filename).suffix.lower()}'

class LessonBlock(models.Model):
    class Type(models.TextChoices):
        TEXT = 'TEXT'
        IMAGE = 'IMAGE'
    lesson = models.ForeignKey('education.Lesson', on_delete=models.PROTECT, related_name='blocks')
    type = models.CharField(max_length=10, choices=Type.choices)
    position = models.PositiveIntegerField()
    content = models.TextField(blank=True)
    media = models.ForeignKey('mediafiles.MediaFile', null=True, blank=True, on_delete=models.PROTECT)
    config = models.JSONField(default=dict, blank=True)
    class Meta:
        ordering = ['position', 'id']
        constraints = [models.UniqueConstraint(fields=['lesson', 'position'], name='unique_block_position')]


class LessonAttachment(models.Model):
    lesson = models.ForeignKey('education.Lesson', on_delete=models.PROTECT, related_name='attachments')
    media = models.ForeignKey('mediafiles.MediaFile', on_delete=models.PROTECT)
    position = models.PositiveIntegerField()

    class Meta:
        ordering = ['position', 'id']
        constraints = [models.UniqueConstraint(fields=['lesson', 'position'], name='unique_attachment_position')]


class LectureImportJob(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING'
        RUNNING = 'RUNNING'
        SUCCEEDED = 'SUCCEEDED'
        FAILED = 'FAILED'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lesson = models.ForeignKey('education.Lesson', null=True, blank=True, on_delete=models.CASCADE, related_name='imports')
    practice_item = models.ForeignKey('education.LearningItem', null=True, blank=True, on_delete=models.CASCADE, related_name='instruction_imports')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    source = models.FileField(upload_to=import_upload_path)
    original_name = models.CharField(max_length=255)
    size = models.PositiveIntegerField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    stage = models.CharField(max_length=30, default='queued')
    use_ai = models.BooleanField(default=True)
    force_ocr = models.BooleanField(default=False)
    result = models.JSONField(default=dict)
    error_code = models.CharField(max_length=50, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    lease_token = models.UUIDField(null=True)
    lease_expires_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'created_at'], name='lecture_import_queue')]
        constraints = [
            models.UniqueConstraint(fields=['lesson'], condition=models.Q(status__in=['PENDING', 'RUNNING']), name='one_active_lecture_import'),
            models.UniqueConstraint(fields=['practice_item'], condition=models.Q(status__in=['PENDING', 'RUNNING']), name='one_active_instruction_import'),
            models.CheckConstraint(condition=(models.Q(lesson__isnull=False, practice_item__isnull=True)
                                              | models.Q(lesson__isnull=True, practice_item__isnull=False)), name='import_has_one_target'),
        ]


class LectureImportAsset(models.Model):
    job = models.ForeignKey(LectureImportJob, on_delete=models.CASCADE, related_name='assets')
    media = models.ForeignKey('mediafiles.MediaFile', on_delete=models.PROTECT, related_name='lecture_import_assets')
    key = models.CharField(max_length=100)
    page = models.PositiveIntegerField(null=True)
    is_page = models.BooleanField(default=False)
