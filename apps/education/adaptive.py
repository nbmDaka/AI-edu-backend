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


def module_states(user, course, *, modules=None, progress_rows=None):
    if course.certificate_enabled:
        modules = course.modules.order_by('position', 'id')
    if progress_rows is None:
        progress_rows = UserModuleProgress.objects.filter(user=user, module__course=course)
    if modules is None:
        modules = course.modules.filter(is_published=True).order_by('position', 'id')
    progress = {row.module_id: row for row in progress_rows}
    states = {}
    previous_completed = True
    for module in modules:
        row = progress.get(module.pk)
        completed = bool(row and row.is_completed)
        if course.certificate_enabled:
            from .journey import module_completion
            completed = module.is_published and module_completion(user, module)['completed']
        locked = bool((course.adaptive_learning_enabled or course.certificate_enabled) and not previous_completed)
        states[module.short_id] = {
            'id': module.pk, 'status': 'locked' if locked else 'completed' if completed else 'repeat' if row and row.attempts else 'available',
            'is_locked': locked, 'is_completed': completed, 'threshold': module.adaptive_threshold,
            'readiness': round(row.readiness * 100, 2) if row else None,
            'attempts': row.attempts if row else 0,
        }
        previous_completed = previous_completed and completed
    return states


def module_is_locked(user, module):
    if not (module.course.adaptive_learning_enabled or module.course.certificate_enabled):
        return False
    return module_states(user, module.course).get(module.short_id, {}).get('is_locked', True)


def request_module_is_locked(request, module):
    from common.permissions import is_admin
    if not request or is_admin(request.user) or not (module.course.adaptive_learning_enabled or module.course.certificate_enabled):
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
    submissions = {}
    for row in PracticeSubmission.objects.select_related('result').filter(
            user=user, item__in=practices, is_current=True, status='SUBMITTED').order_by('submitted_at', 'pk'):
        submissions[row.item_id] = row
    completed = total = 0
    for practice in practices:
        if not practice.practice_version_id:
            raise ValidationError({'detail': 'Практическая работа ещё не настроена. Обратитесь к администратору.'})
        submission = submissions.get(practice.pk)
        if not submission or not hasattr(submission, 'result'):
            raise ValidationError({'detail': 'Перед тестом завершите все практические работы модуля кнопкой «Сделано».'})
        weight = len(submission.criteria_snapshot) or 1
        completed += submission.result.psi_value * weight
        total += weight
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
    result = {
        'readiness': round(readiness * 100, 2), 'threshold': module.adaptive_threshold,
        'module_passed': passed, 'module_completed': progress.is_completed,
        'theory': theory, 'practice': practice, 'errors': errors, 'competency': competency,
        'competencies': competencies, 'module_short_id': module.short_id,
        'repeat_item_short_id': first.short_id if first else item.short_id,
        'next_item_short_id': next_item.short_id if next_item else None,
    }
    from .recommendations import build_recommendation, review_route
    review_items = list(module.items.filter(status='PUBLISHED', type__in=['LECTURE', 'PRACTICE']).order_by('position', 'id'))
    recommendation = build_recommendation(result, question_results, review_items)
    if not progress.is_completed:
        # The next attempt requires studying again; attempt history is kept. Close to the threshold,
        # only the materials of the weak topics are reopened; otherwise the whole module is.
        content = list(module.items.filter(status='PUBLISHED').exclude(type='TEST').order_by('position', 'id'))
        route = review_route(recommendation, content)
        reopened = route['items'] or content
        UserLearningItemProgress.objects.filter(user=user, learning_item__in=reopened).update(
            progress_percent=0, is_completed=False, completed_at=None)
        PracticeSubmission.objects.filter(user=user, item__in=reopened).update(is_current=False)
        recommendation['route'] = {'scope': route['scope'], 'items': [
            {'short_id': entry.short_id, 'title': entry.title, 'type': entry.type} for entry in reopened]}
        if reopened:
            result['repeat_item_short_id'] = reopened[0].short_id
    result['recommendation'] = recommendation
    return result
