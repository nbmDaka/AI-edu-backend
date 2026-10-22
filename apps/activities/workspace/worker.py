import time
import uuid
import json
import logging
from datetime import timedelta
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from apps.accounts.models import User
from apps.education.access import can_access_item
from apps.education.models import PracticeSubmission
from apps.activities.models import WorkspaceDocument, WorkspaceMessage, WorkspaceOperation, WorkspaceArtifact
from .ai import AIError, run_ai
from .services import workspace_plugin

MAX_ATTEMPTS = 3
LEASE_GRACE_SECONDS = 30
logger = logging.getLogger(__name__)


def apply_chat_result(operation, document, response):
    payload = response['payload']
    action = payload['action']
    if action in ('generate', 'edit'):
        workspace_plugin(document.submission).validate_workspace_content(payload['content'])
        document.content = payload['content']
    if 'parameters' in payload:
        document.parameters = payload['parameters']
    document.conversation_state = payload['state']
    document.revision += 1
    document.save(update_fields=['content', 'parameters', 'conversation_state', 'revision', 'updated_at'])
    operation.result = {'action': action, 'revision': document.revision, 'choices': payload.get('choices', [])}
    if action in ('generate', 'edit'):
        operation.result['content'] = document.content
    if payload.get('docx'):
        from .docx_export import filename, MAX_DOCX_BYTES
        data = response['docx_data']
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_DOCX_BYTES:
            raise ValueError('invalid_docx')
        artifact = WorkspaceArtifact.objects.create(document=document, operation=operation,
            revision=document.revision, name=filename(document.content['title']), data=data)
        operation.result['artifact'] = {'id': artifact.pk, 'name': artifact.name, 'size': len(data),
            'url': f'/api/v1/workspace/artifacts/{artifact.pk}/download/', 'revision': artifact.revision}
    language = document.parameters.get('language', 'ru')
    if action in ('generate', 'edit', 'export'):
        return {'ru': 'Лекция готова. Файл Word прикреплён к сообщению.' if payload.get('docx') else 'Лекция готова. Что сделаем дальше?',
                'kk': 'Дәріс дайын. Word файлы хабарламаға тіркелді.' if payload.get('docx') else 'Дәріс дайын. Келесі қадам қандай?',
                'en': 'Your lecture is ready. The Word file is attached.' if payload.get('docx') else 'Your lecture is ready. What would you like to do next?'}[language]
    return payload['message']


def locked_queue(queryset):
    # PostgreSQL supports concurrent workers; SQLite is for isolated --once tests only.
    return queryset.select_for_update(skip_locked=True) if connection.vendor == 'postgresql' else queryset


def recover_expired_operations():
    recovered = 0
    with transaction.atomic():
        now = timezone.now()
        operations = locked_queue(WorkspaceOperation.objects.filter(
            status='RUNNING', lease_expires_at__lte=now)).order_by('id')[:100]
        for operation in operations:
            retry = operation.attempts < MAX_ATTEMPTS
            elapsed = min(2_147_483_647, max(0, int((now - operation.started_at).total_seconds() * 1000))) if operation.started_at else None
            changed = WorkspaceOperation.objects.filter(pk=operation.pk, status='RUNNING',
                lease_token=operation.lease_token, lease_expires_at__lte=now).update(
                status='PENDING' if retry else 'FAILED', lease_token=None, lease_expires_at=None,
                error_code='' if retry else 'operation_timeout', finished_at=None if retry else now,
                duration_ms=elapsed)
            recovered += changed
    return recovered


def claim_next_operation():
    with transaction.atomic():
        operation = locked_queue(WorkspaceOperation.objects.filter(status='PENDING')).first()
        if operation is None:
            return None
        now, token = timezone.now(), uuid.uuid4()
        if not WorkspaceOperation.objects.filter(pk=operation.pk, status='PENDING').update(
                status='RUNNING', lease_token=token, started_at=now, attempts=F('attempts') + 1,
                duration_ms=None,
                lease_expires_at=now + timedelta(seconds=operation.timeout_seconds + LEASE_GRACE_SECONDS)):
            return None
        operation.refresh_from_db()
        return operation


def context_error(operation, document, submission, user):
    if not user.is_active or not can_access_item(user, submission.item):
        return 'access_revoked'
    if submission.status != 'IN_PROGRESS':
        return 'submission_closed'
    if document.revision != operation.base_revision:
        return 'revision_conflict'
    return ''


@transaction.atomic
def finish_operation(claim, response=None, error_code='', duration_ms=0):
    # Same lock order as document editing / Practice Engine submit. No HTTP under DB locks.
    context = WorkspaceDocument.objects.filter(pk=claim.document_id).values('owner_id', 'submission_id').first()
    if context is None:
        return False
    user = User.objects.select_for_update().get(pk=context['owner_id'])
    submission = PracticeSubmission.objects.select_for_update(of=('self',)).select_related(
        'item__module__course__learning_track', 'practice_version__practice_definition').get(pk=context['submission_id'])
    document = WorkspaceDocument.objects.select_for_update().get(pk=claim.document_id)
    operation = WorkspaceOperation.objects.select_for_update().get(pk=claim.pk)
    if operation.status != 'RUNNING' or operation.lease_token != claim.lease_token:
        return False  # An old worker cannot apply a result after recovery/reclaim.
    now = timezone.now()
    operation.duration_ms = duration_ms
    if response:
        for name in ('model', 'input_tokens', 'output_tokens', 'total_tokens', 'cost', 'cost_currency'):
            setattr(operation, name, response.get(name))
    error_code = error_code or ('operation_timeout' if operation.lease_expires_at <= now else '')
    error_code = error_code or context_error(operation, document, submission, user)
    if not error_code:
        try:
            payload = response['payload']
            if operation.kind == 'chat':
                with transaction.atomic():
                    text = apply_chat_result(operation, document, response)
            elif operation.kind == 'generate':
                workspace_plugin(submission).validate_workspace_content(payload)
                document.content = payload
                document.revision += 1
                document.save(update_fields=['content', 'revision', 'updated_at'])
                operation.result = {'content': payload, 'revision': document.revision}
                text = f"Лекция «{payload['title']}» создана."
            else:
                if (not isinstance(payload, dict) or set(payload) != {'improved_prompt'} or
                        not isinstance(payload['improved_prompt'], str) or not payload['improved_prompt'].strip() or
                        len(payload['improved_prompt']) > 8000):
                    raise ValidationError('Некорректный улучшенный запрос')
                operation.result = payload
                text = payload['improved_prompt']
            WorkspaceMessage.objects.create(document=document, operation=operation, role='assistant', text=text)
        except (ValidationError, KeyError, TypeError, ValueError, RecursionError):
            error_code = 'ai_invalid_content'
            operation.result = {}
    operation.error_code = error_code
    operation.status = 'FAILED' if error_code else 'SUCCEEDED'
    operation.finished_at = now
    operation.lease_token = None
    operation.lease_expires_at = None
    operation.save()
    return True


def execute_operation(operation):
    started = time.monotonic()
    response, error_code = None, ''
    try:
        document = WorkspaceDocument.objects.select_related(
            'owner', 'submission__item__module__course__learning_track').get(pk=operation.document_id)
        error_code = context_error(operation, document, document.submission, document.owner)
        if not error_code:
            if operation.kind == 'chat':
                from .conversation import execute_chat
                response = execute_chat(operation, document, workspace_plugin(document.submission))
                if response['payload'].get('docx'):
                    from .docx_export import render_docx
                    response['docx_data'] = render_docx(response['payload'].get('content', document.content))
            else:
                response = run_ai(operation)
    except AIError as error:
        error_code = error.code
        logger.warning('Workspace operation %s: %s diagnostics=%s',
                       operation.pk, error.code, json.dumps(error.diagnostics, sort_keys=True))
    except ValidationError:
        error_code = 'ai_invalid_content'
    except Exception as error:
        from .limits import WorkspaceLimit
        error_code = 'workspace_limit' if isinstance(error, WorkspaceLimit) else 'worker_error'
    return finish_operation(operation, response, error_code, int((time.monotonic() - started) * 1000))
