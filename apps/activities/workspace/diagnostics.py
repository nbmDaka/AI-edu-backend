"""Error-only operation diagnostics, without request/response or exception text."""
import json
import logging
import time
import traceback
from pathlib import Path

from django.conf import settings

from .ai import safe_diagnostics

logger = logging.getLogger('apps.activities.workspace.worker')


def mark_stage(operation, stage, timeout_seconds=None):
    operation._diagnostic_stage = stage
    operation._diagnostic_started = time.monotonic()
    operation._diagnostic_timeout = timeout_seconds


def log_operation_error(operation, code, duration_ms=None, diagnostics=None, error=None):
    details = {
        'document_id': operation.document_id,
        'kind': operation.kind,
        'attempt': operation.attempts,
        'duration_ms': duration_ms,
        'timeout_seconds': operation.timeout_seconds,
        'stage': getattr(operation, '_diagnostic_stage', 'unknown'),
        **safe_diagnostics(diagnostics, settings.AI_API_KEY),
    }
    started = getattr(operation, '_diagnostic_started', None)
    if started is not None:
        details['stage_duration_ms'] = max(0, int((time.monotonic() - started) * 1000))
    budget = getattr(operation, '_diagnostic_timeout', None)
    if budget is not None:
        details['stage_timeout_seconds'] = budget
    if error is not None:
        details['exception_type'] = type(error).__name__
        # No exception message, source lines, locals, or chained exception text.
        details['traceback'] = [
            {'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
            for frame in traceback.extract_tb(error.__traceback__)[-8:]
        ]
    logger.error('Workspace operation %s: %s diagnostics=%s',
                 operation.pk, code, json.dumps(details, sort_keys=True))
