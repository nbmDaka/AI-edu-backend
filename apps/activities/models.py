from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone
from common.soft_delete import SoftDeleteModel


class PracticeDefinition(SoftDeleteModel):
    soft_delete_cascade = ('learning_item',)
    # Legacy fields are retained for lossless upgrades, not used by the engine.
    lesson = models.ForeignKey('education.Lesson', null=True, blank=True, on_delete=models.PROTECT, related_name='practices')
    kind = models.CharField(max_length=100, blank=True)
    version = models.PositiveIntegerField(default=1)
    config = models.JSONField(default=dict)
    is_enabled = models.BooleanField(default=False)
    practice_type = models.CharField(max_length=100, blank=True)
    title = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(default=timezone.now)

    def save(self, *args, **kwargs):
        if self.pk and self.versions.exists():
            original = type(self).objects.get(pk=self.pk)
            if original.practice_type != self.practice_type:
                raise ValidationError('Practice type cannot change after version creation')
        self.updated_at = timezone.now()
        return super().save(*args, **kwargs)


class VersionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if self.filter(status='PUBLISHED').exists():
            raise ValidationError('Published practice versions are immutable')
        return super().update(**kwargs)

    def delete(self):
        if self.filter(status='PUBLISHED').exists():
            raise ValidationError('Published practice versions are immutable')
        return super().delete()


class PracticeVersion(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT'
        PUBLISHED = 'PUBLISHED'

    practice_definition = models.ForeignKey(PracticeDefinition, on_delete=models.CASCADE, related_name='versions')
    version_number = models.PositiveIntegerField()
    plugin_version = models.CharField(max_length=30, default='1.0.0')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    config = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    published_at = models.DateTimeField(null=True, blank=True)
    objects = VersionQuerySet.as_manager()

    class Meta:
        ordering = ['version_number']
        constraints = [models.UniqueConstraint(fields=['practice_definition', 'version_number'], name='unique_practice_version')]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk, status='PUBLISHED').exists():
            raise ValidationError('Published practice versions are immutable; create a new draft')
        from .registry import registry
        registry.get(self.practice_definition.practice_type, self.plugin_version).validate_config(self.config)
        if self.status == self.Status.PUBLISHED:
            from django.utils import timezone
            self.published_at = self.published_at or timezone.now()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if type(self).objects.filter(pk=self.pk, status='PUBLISHED').exists():
            raise ValidationError('Published practice versions are immutable')
        return super().delete(*args, **kwargs)


class CriterionQuerySet(models.QuerySet):
    def bulk_create(self, objs, **kwargs):
        objs = list(objs)
        if PracticeVersion.objects.filter(pk__in=[row.practice_version_id for row in objs], status='PUBLISHED').exists():
            raise ValidationError('Published criteria are immutable')
        return super().bulk_create(objs, **kwargs)

    def update(self, **kwargs):
        self.ensure_mutable()
        return super().update(**kwargs)

    def delete(self):
        self.ensure_mutable()
        return super().delete()

    def ensure_mutable(self):
        if self.filter(practice_version__status='PUBLISHED').exists():
            raise ValidationError('Published criteria are immutable')


class PracticeCriterion(models.Model):
    practice_version = models.ForeignKey(PracticeVersion, on_delete=models.CASCADE, related_name='criteria')
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    position = models.PositiveIntegerField()
    is_required = models.BooleanField(default=True)
    objects = CriterionQuerySet.as_manager()

    class Meta:
        ordering = ['position', 'id']
        constraints = [models.UniqueConstraint(fields=['practice_version', 'position'], name='unique_practice_criterion_position')]

    def save(self, *args, **kwargs):
        if (PracticeVersion.objects.filter(pk=self.practice_version_id, status='PUBLISHED').exists() or
                self.pk and type(self).objects.filter(pk=self.pk, practice_version__status='PUBLISHED').exists()):
            raise ValidationError('Published criteria are immutable')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if PracticeVersion.objects.filter(pk=self.practice_version_id, status='PUBLISHED').exists():
            raise ValidationError('Published criteria are immutable')
        return super().delete(*args, **kwargs)


class PracticeResult(models.Model):
    class Source(models.TextChoices):
        COMPLETION = 'COMPLETION'
        SELF_ASSESSMENT = 'SELF_ASSESSMENT'
        AUTO_GRADING = 'AUTO_GRADING'
        AI_GRADING = 'AI_GRADING'
        TEACHER = 'TEACHER'

    submission = models.OneToOneField('education.PracticeSubmission', on_delete=models.CASCADE, related_name='result')
    self_assessment_score = models.FloatField(null=True, blank=True)
    automatic_score = models.FloatField(null=True, blank=True)
    teacher_score = models.FloatField(null=True, blank=True)
    effective_score = models.FloatField(validators=[MinValueValidator(0), MaxValueValidator(1)])
    grading_source = models.CharField(max_length=20, choices=Source.choices)
    psi_value = models.FloatField(validators=[MinValueValidator(0), MaxValueValidator(1)])
    psi_source = models.CharField(max_length=20, choices=Source.choices)
    created_at = models.DateTimeField(default=timezone.now, editable=False)


class WorkspaceDocument(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='workspace_documents')
    submission = models.OneToOneField('education.PracticeSubmission', on_delete=models.CASCADE, related_name='workspace_document')
    document_type = models.CharField(max_length=100)
    schema_version = models.PositiveIntegerField(default=1)
    parameters = models.JSONField(default=dict)
    content = models.JSONField(default=dict)
    conversation_state = models.JSONField(default=dict)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class WorkspaceOperation(models.Model):
    class Kind(models.TextChoices):
        GENERATE = 'generate'
        IMPROVE_PROMPT = 'improve_prompt'
        CHAT = 'chat'

    class Status(models.TextChoices):
        PENDING = 'PENDING'
        RUNNING = 'RUNNING'
        SUCCEEDED = 'SUCCEEDED'
        FAILED = 'FAILED'

    document = models.ForeignKey(WorkspaceDocument, on_delete=models.CASCADE, related_name='operations')
    request_id = models.UUIDField()
    kind = models.CharField(max_length=30, choices=Kind.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    base_revision = models.PositiveIntegerField()
    input_data = models.JSONField()
    result = models.JSONField(default=dict)
    error_code = models.CharField(max_length=50, blank=True)
    model = models.CharField(max_length=200)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    total_tokens = models.PositiveIntegerField(null=True, blank=True)
    cost = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    cost_currency = models.CharField(max_length=10, blank=True)
    timeout_seconds = models.PositiveIntegerField()
    attempts = models.PositiveSmallIntegerField(default=0)
    lease_token = models.UUIDField(null=True, blank=True, editable=False)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at', 'id']
        indexes = [models.Index(fields=['status', 'created_at'], name='workspace_queue_idx'),
                   models.Index(fields=['status', 'lease_expires_at'], name='workspace_lease_idx')]
        constraints = [
            models.UniqueConstraint(fields=['document', 'request_id'], name='unique_workspace_request'),
            models.UniqueConstraint(fields=['document'], condition=models.Q(status__in=['PENDING', 'RUNNING']),
                                    name='unique_active_workspace_op'),
        ]


class WorkspaceMessage(models.Model):
    class Role(models.TextChoices):
        USER = 'user'
        ASSISTANT = 'assistant'

    document = models.ForeignKey(WorkspaceDocument, on_delete=models.CASCADE, related_name='messages')
    operation = models.ForeignKey(WorkspaceOperation, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=12, choices=Role.choices)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']
        constraints = [models.UniqueConstraint(fields=['operation', 'role'], name='unique_workspace_op_message')]


class WorkspaceUsage(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    operation = models.ForeignKey(WorkspaceOperation, on_delete=models.CASCADE, related_name='usage_reservations')
    category = models.CharField(max_length=20)
    reserved_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=['user', 'category', 'reserved_at'], name='workspace_usage_window')]
        constraints = [models.UniqueConstraint(fields=['operation', 'category'], name='workspace_usage_once')]


class WorkspaceArtifact(models.Model):
    document = models.ForeignKey(WorkspaceDocument, on_delete=models.CASCADE, related_name='artifacts')
    operation = models.OneToOneField(WorkspaceOperation, on_delete=models.CASCADE, related_name='artifact')
    revision = models.PositiveIntegerField()
    name = models.CharField(max_length=180)
    # Small, bounded immutable DOCX snapshots; no public storage URL or mutable regeneration on download.
    data = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)
