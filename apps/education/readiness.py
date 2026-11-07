"""The learner's own adaptive picture: readiness per module, attempt history and competency profile.

Read-only; built from records the adaptive test already stores (TestAttempt.adaptive_result,
UserModuleProgress, UserCompetencyProgress), so it adds no new state.
"""
from collections import defaultdict

from apps.assessments.models import Question, TestAttempt
from .adaptive import module_states
from .models import Module, UserCompetencyProgress, UserModuleProgress
from .recommendations import WEAK_TOPIC_LIMIT

HISTORY_LIMIT = 10


def competency_titles(course_ids):
    """Author spelling of each competency key, per course."""
    rows = (Question.objects.filter(test__deleted_at__isnull=True, test__learning_item__module__course_id__in=course_ids)
            .order_by('pk').values_list('test__learning_item__module__course_id', 'competency'))
    titles = defaultdict(dict)
    for course_id, label in rows:
        label = label.strip()
        titles[course_id].setdefault(label.casefold(), label)
    return titles


def my_readiness(user, courses):
    courses = [course for course in courses if course.adaptive_learning_enabled]
    course_ids = [course.pk for course in courses]
    modules = list(Module.objects.filter(course_id__in=course_ids, is_published=True).order_by('position', 'id'))
    by_course = defaultdict(list)
    for module in modules:
        by_course[module.course_id].append(module)
    progress = defaultdict(list)
    for row in UserModuleProgress.objects.filter(user=user, module__in=modules):
        progress[row.module_id].append(row)
    history = defaultdict(list)
    attempts = (TestAttempt.objects.filter(user=user, adaptive_result__isnull=False, test__deleted_at__isnull=True,
                                           test__learning_item__module__in=modules)
                .order_by('completed_at', 'pk').values_list('test__learning_item__module_id', 'completed_at', 'adaptive_result'))
    for module_id, completed_at, result in attempts:
        if isinstance(result, dict) and 'readiness' in result:
            history[module_id].append((completed_at, result))
    profiles = defaultdict(list)
    for row in UserCompetencyProgress.objects.filter(user=user, course_id__in=course_ids).order_by('competency'):
        profiles[row.course_id].append(row)
    titles = competency_titles(course_ids)

    output = []
    for course in courses:
        course_modules = by_course[course.pk]
        states = module_states(user, course, modules=course_modules,
                               progress_rows=[row for module in course_modules for row in progress[module.pk]])
        rows = []
        for module in course_modules:
            state = states.get(module.short_id, {})
            entries = history[module.pk]
            latest = entries[-1][1] if entries else None
            rows.append({
                'id': module.pk, 'short_id': module.short_id, 'title': module.title,
                'status': state.get('status', 'available'), 'threshold': module.adaptive_threshold,
                'attempts': len(entries),
                'readiness': latest['readiness'] if latest else None,
                'best_readiness': max(result['readiness'] for _, result in entries) if entries else None,
                'history': [{'completed_at': at, 'readiness': result['readiness'], 'passed': bool(result.get('module_passed'))}
                            for at, result in entries[-HISTORY_LIMIT:]],
                'recommendation': latest.get('recommendation') if latest else None,
            })
        competencies = [{'competency': row.competency, 'title': titles[course.pk].get(row.competency, row.competency),
                         'score': round(row.score, 4), 'is_weak': row.score < WEAK_TOPIC_LIMIT}
                        for row in profiles[course.pk]]
        competencies.sort(key=lambda row: (row['score'], row['title'].casefold()))
        tested = [row for row in rows if row['attempts']]
        output.append({
            'id': course.pk, 'short_id': course.short_id, 'slug': course.slug, 'title': course.title,
            'passed_modules': sum(row['status'] == 'completed' for row in rows), 'total_modules': len(rows),
            'average_readiness': round(sum(row['readiness'] for row in tested) / len(tested), 2) if tested else None,
            'modules': rows, 'competencies': competencies, 'weak_topic_limit': WEAK_TOPIC_LIMIT,
        })
    return {'courses': output}
