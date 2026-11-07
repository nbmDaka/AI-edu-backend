import math
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError, NotFound

from apps.accounts.models import User
from apps.education.models import LearningItem, PracticeSubmission, UserLearningItemProgress
from apps.education.progress import complete_item_from_server
from .models import PracticeDefinition, PracticeVersion, PracticeResult
from .registry import registry
from .serializers import VersionOutput, ResultOutput


@transaction.atomic
def save_draft(item, user, data):
    item = LearningItem.objects.select_for_update().get(pk=item.pk)
    plugin = registry.get(data['practice_type'], data['plugin_version'])
    plugin.validate_config(data['config'])
    definition = item.practice
    if not definition:
        definition = PracticeDefinition.objects.create(title=item.title, created_by=user)
        item.practice = definition
        item.save(update_fields=['practice'])
    if definition.practice_type and definition.practice_type != data['practice_type']:
        # Changing the assignment must not rewrite the type of pinned versions.
        definition = PracticeDefinition.objects.create(title=item.title, created_by=user)
        item.practice = definition
        item.save(update_fields=['practice'])
    definition.practice_type = data['practice_type']
    definition.title = data['title']
    definition.description = data['description']
    definition.save()
    draft = definition.versions.filter(status='DRAFT').last()
    if not draft:
        latest = definition.versions.last()
        draft = PracticeVersion(practice_definition=definition, version_number=latest.version_number + 1 if latest else 1)
    draft.title = data['title']
    draft.description = data['description']
    draft.config = data['config']
    draft.plugin_version = data['plugin_version']
    draft.save()
    draft.criteria.all().delete()
    # Type-specific configuration materialization belongs to the plugin.
    plugin.materialize_config(draft)
    return draft


@transaction.atomic
def publish_draft(item):
    item = LearningItem.objects.select_for_update().get(pk=item.pk)
    if not item.practice_id:
        raise ValidationError({'detail': 'Сначала сохраните черновик практики'})
    draft = item.practice.versions.select_for_update().filter(status='DRAFT').last()
    if not draft:
        if item.practice_version_id:
            return item.practice_version  # A repeated publish is idempotent.
        raise ValidationError({'detail': 'Сначала сохраните черновик практики'})
    plugin = registry.get(item.practice.practice_type, draft.plugin_version)
    plugin.validate_config(draft.config)
    draft.status = 'PUBLISHED'
    draft.save()
    item.practice_version = draft
    item.title, item.description, item.status = draft.title, draft.description, 'PUBLISHED'
    item.save(update_fields=['practice_version', 'title', 'description', 'status', 'updated_at'])
    return draft


def current_submission(user, item):
    return PracticeSubmission.objects.select_related('practice_version__practice_definition', 'result').filter(
        user=user, item=item, is_current=True).order_by('-started_at', '-pk').first()


@transaction.atomic
def start_submission(user, item):
    # Same user lock as test attempts, including adaptive reset. Item lock serializes publication.
    User.objects.select_for_update().get(pk=user.pk)
    item = LearningItem.objects.select_for_update(of=('self',)).select_related('practice_version').get(pk=item.pk)
    version = item.practice_version
    if not version or version.status != 'PUBLISHED':
        raise ValidationError({'detail': 'Практическая работа ещё не настроена'})
    active = PracticeSubmission.objects.filter(user=user, item=item, status='IN_PROGRESS').first()
    if active:
        return active
    PracticeSubmission.objects.filter(user=user, item=item, is_current=True).update(is_current=False)
    return PracticeSubmission.objects.create(user=user, item=item, practice_version=version)


@transaction.atomic
def submit(user, item, submission_id, data):
    User.objects.select_for_update().get(pk=user.pk)
    submission = PracticeSubmission.objects.select_for_update().select_related('practice_version__practice_definition').filter(
        pk=submission_id, user=user, item=item).first()
    if not submission:
        raise NotFound()
    version = submission.practice_version
    plugin = registry.get(version.practice_definition.practice_type, version.plugin_version)
    is_workspace = bool(getattr(plugin, 'workspace_type', None))
    if submission.status != 'IN_PROGRESS':
        saved_data = submission.submission_data
        if is_workspace:
            saved_data = {key: value for key, value in saved_data.items() if key != 'document'}
            if data == {'completed': True} and saved_data.get('completed') is True and submission.is_current:
                return submission
        if saved_data == data and submission.is_current:
            return submission  # Network retry: preserve the existing snapshot and score.
        raise ValidationError({'detail': 'Попытка уже сдана. Начните новую попытку'})
    if is_workspace:
        from .workspace.services import prepare_submission
        data = prepare_submission(user, submission, data)
    evaluated = plugin.evaluate(version, data)
    if (not math.isfinite(evaluated.score) or not 0 <= evaluated.score <= 1 or
            evaluated.source not in PracticeResult.Source.values):
        raise ValidationError({'detail': 'Некорректный результат оценивания практики'})
    submission.submission_data = evaluated.data
    submission.criteria_snapshot = evaluated.snapshot
    submission.status = 'SUBMITTED'
    submission.submitted_at = timezone.now()
    submission.completed_at = submission.submitted_at if evaluated.completed else None
    submission.save()
    PracticeResult.objects.create(submission=submission,
        self_assessment_score=evaluated.score if evaluated.source == 'SELF_ASSESSMENT' else None,
        automatic_score=evaluated.score if evaluated.source in ('AUTO_GRADING', 'AI_GRADING') else None,
        teacher_score=evaluated.score if evaluated.source == 'TEACHER' else None,
        effective_score=evaluated.score, grading_source=evaluated.source,
        psi_value=evaluated.score, psi_source=evaluated.source)
    from apps.education.activity import log_event
    log_event(user, 'PRACTICE_SUBMITTED', item=item, score=round(evaluated.score, 4), completed=bool(evaluated.completed),
              practice_type=version.practice_definition.practice_type)
    if evaluated.completed:
        complete_item_from_server(user, item)
    else:
        # Preserve the existing checklist methodology: partial checks yield partial progress.
        UserLearningItemProgress.objects.update_or_create(user=user, learning_item=item,
            defaults={'progress_percent': min(99, round(evaluated.score * 100)), 'is_completed': False, 'completed_at': None})
    return submission


def runtime_state(user, item, submission=None):
    submission = submission or current_submission(user, item)
    version = submission.practice_version if submission else item.practice_version
    result = getattr(submission, 'result', None) if submission else None
    progress = UserLearningItemProgress.objects.filter(user=user, learning_item=item).first()
    return {
        'version': VersionOutput(version).data if version else None,
        'current_version_id': item.practice_version_id,
        'submission': {'id': submission.pk, 'status': submission.status,
            'document_id': getattr(getattr(submission, 'workspace_document', None), 'pk', None),
            'practice_version': submission.practice_version_id, 'submission_data': submission.submission_data,
            'criteria_snapshot': submission.criteria_snapshot, 'started_at': submission.started_at,
            'submitted_at': submission.submitted_at, 'completed_at': submission.completed_at} if submission else None,
        'result': ResultOutput(result).data if result else None,
        'progress_percent': progress.progress_percent if progress else 0,
        'is_completed': bool(progress and progress.is_completed),
    }
