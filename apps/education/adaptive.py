"""Module readiness formula from ENU, independent of HTTP and persistence."""

import math

from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import (LearningItem, PracticeSubmission, UserCompetencyProgress,
                     UserLearningItemProgress, UserModuleProgress)


def calculate_readiness(theory, practice, errors, competency):
    values = (theory, practice, errors, competency)
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in values):
        raise ValueError('Adaptive indicators must be finite values from 0 to 1')
    return round(0.40 * theory + 0.25 * practice - 0.15 * errors + 0.20 * competency, 4)


def module_states(user, course):
    progress = {row.module_id: row for row in UserModuleProgress.objects.filter(user=user, module__course=course)}
    states = {}
    previous_completed = True
    for module in course.modules.filter(is_published=True).order_by('position', 'id'):
        row = progress.get(module.pk)
        completed = bool(row and row.is_completed)
        locked = bool(course.adaptive_learning_enabled and not previous_completed)
        states[module.short_id] = {
            'id': module.pk, 'status': 'locked' if locked else 'completed' if completed else 'repeat' if row and row.attempts else 'available',
            'is_locked': locked, 'is_completed': completed, 'threshold': module.adaptive_threshold,
            'readiness': round(row.readiness * 100, 2) if row else None,
            'attempts': row.attempts if row else 0,
        }
        previous_completed = previous_completed and completed
    return states


def module_is_locked(user, module):
    if not module.course.adaptive_learning_enabled:
        return False
    return module_states(user, module.course).get(module.short_id, {}).get('is_locked', True)


def request_module_is_locked(request, module):
    from common.permissions import is_admin
    if not request or is_admin(request.user) or not module.course.adaptive_learning_enabled:
        return False
    if not hasattr(request, '_adaptive_module_states'):
        request._adaptive_module_states = {}
    if module.course_id not in request._adaptive_module_states:
        request._adaptive_module_states[module.course_id] = module_states(request.user, module.course)
    return request._adaptive_module_states[module.course_id].get(module.short_id, {}).get('is_locked', True)


def practice_indicator(user, module):
    practices = list(module.items.filter(type='PRACTICE', status='PUBLISHED'))
    if not practices:
        # No practical requirements means there are no unmet practical criteria.
        return 1.0
    submissions = {row.item_id: row for row in PracticeSubmission.objects.filter(user=user, item__in=practices)}
    completed = total = 0
    for practice in practices:
        if not practice.practice_criteria:
            raise ValidationError({'detail': 'Для практической работы ещё не настроены критерии. Обратитесь к администратору.'})
        submission = submissions.get(practice.pk)
        if not submission or submission.criteria != practice.practice_criteria:
            raise ValidationError({'detail': 'Перед тестом сохраните самопроверку всех практических работ модуля.'})
        completed += sum(submission.checks)
        total += len(submission.checks)
    return round(completed / total, 4)


def prepare_module_attempt(user, item):
    module = item.module
    lectures = module.items.filter(type='LECTURE', status='PUBLISHED')
    completed = UserLearningItemProgress.objects.filter(user=user, learning_item__in=lectures, is_completed=True).count()
    if completed != lectures.count():
        raise ValidationError({'detail': 'Перед итоговым тестом пройдите все лекции модуля.'})
    return practice_indicator(user, module)


def evaluate_module_attempt(user, item, question_results, practice):
    module = item.module
    theory = round(sum(correct for _, correct in question_results) / len(question_results), 4)
    errors = round(1 - theory, 4)
    grouped = {}
    for question, correct in question_results:
        grouped.setdefault(question.competency.strip().casefold(), []).append(correct)
    competencies = {}
    for name, results in grouped.items():
        profile, _ = UserCompetencyProgress.objects.get_or_create(user=user, course=module.course, competency=name)
        current = round(sum(results) / len(results), 4)
        profile.score = round(0.40 * profile.score + 0.60 * current, 4)
        profile.save(update_fields=['score'])
        competencies[name] = profile.score
    competency = round(sum(competencies.values()) / len(competencies), 4)
    readiness = calculate_readiness(theory, practice, errors, competency)
    passed = readiness >= module.adaptive_threshold / 100
    progress, _ = UserModuleProgress.objects.get_or_create(user=user, module=module)
    progress.attempts += 1
    progress.readiness = readiness
    progress.best_readiness = max(progress.best_readiness, readiness)
    progress.is_completed = progress.is_completed or passed
    if passed and not progress.completed_at:
        progress.completed_at = timezone.now()
    progress.save()
    first = module.items.filter(status='PUBLISHED').exclude(type='TEST').order_by('position', 'id').first()
    next_item = None
    if progress.is_completed:
        ordered = list(module.course.modules.filter(is_published=True).order_by('position', 'id'))
        index = next(index for index, member in enumerate(ordered) if member.pk == module.pk)
        if index + 1 < len(ordered):
            next_item = ordered[index + 1].items.filter(status='PUBLISHED').order_by('position', 'id').first()
    else:
        # The next attempt requires studying this module again; retain attempt history.
        content = module.items.filter(status='PUBLISHED').exclude(type='TEST')
        UserLearningItemProgress.objects.filter(user=user, learning_item__in=content).update(
            progress_percent=0, is_completed=False, completed_at=None)
        PracticeSubmission.objects.filter(user=user, item__in=content).delete()
    return {
        'readiness': round(readiness * 100, 2), 'threshold': module.adaptive_threshold,
        'module_passed': passed, 'module_completed': progress.is_completed,
        'theory': theory, 'practice': practice, 'errors': errors, 'competency': competency,
        'competencies': competencies, 'module_short_id': module.short_id,
        'repeat_item_short_id': first.short_id if first else item.short_id,
        'next_item_short_id': next_item.short_id if next_item else None,
    }
