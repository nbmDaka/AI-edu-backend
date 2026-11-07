"""Learning Analytics (ТЗ, п. 33): выполненные работы, время, число генераций, исправлений и улучшений промпта,
оценки, динамика результатов и используемые инструменты. Показатели считаются по таблицам модуля."""
from django.db.models import Count

from ..models import AIRequest, LearningEvent, PortfolioItem, Progress, Review, TrainingSession
from ..permissions import display_name


def _avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values)) if values else None


def user_analytics(user):
    progress = list(Progress.objects.filter(user=user).select_related('work').order_by('work__number'))
    tools = dict(LearningEvent.objects.filter(user=user).values_list('type').annotate(n=Count('id')))
    reviews = list(Review.objects.filter(session__user=user).values_list('grade', flat=True))
    profile = getattr(user, 'lab_profile', None) if hasattr(user, 'lab_profile') else None
    return {
        'id': user.pk,
        'login': user.get_username(),
        'fio': display_name(user),
        'organization': getattr(profile, 'organization', '') if profile else '',
        'started': len(progress),
        'completed': sum(1 for p in progress if p.is_completed),
        'generations': sum(p.generations for p in progress),
        'promptImprovements': sum(p.prompt_improvements for p in progress),
        'edits': sum(p.edits for p in progress),
        'timeSpentMs': sum(p.time_spent_ms for p in progress),
        'avgScore': _avg([p.last_score for p in progress]),
        'avgFirstScore': _avg([p.first_score for p in progress]),
        'avgQuiz': _avg([p.quiz_score for p in progress]),
        'avgPrompt': _avg([p.prompt_score for p in progress]),
        'avgGrade': _avg(list(reviews)),
        'portfolio': PortfolioItem.objects.filter(user=user).count(),
        'events': sum(tools.values()),
        'tools': tools,
        'aiRequests': AIRequest.objects.filter(user=user).count(),
        'perWork': [{'modId': p.work.code, 'completed': p.is_completed, 'firstScore': p.first_score, 'score': p.last_score,
                     'bestScore': p.best_score, 'generations': p.generations, 'promptScore': p.prompt_score,
                     'quiz': p.quiz_score, 'timeSpentMs': p.time_spent_ms} for p in progress],
    }


def session_summary(s):
    gen = s.current_generation
    ev = getattr(gen, 'evaluation', None) if gen else None
    review = s.reviews.first()
    return {
        'id': s.pk, 'workCode': s.work.code, 'workNumber': s.work.number, 'workTitle': s.work.title,
        'user': {'id': s.user_id, 'login': s.user.get_username(), 'name': display_name(s.user)},
        'status': s.status, 'step': s.step, 'score': ev.total if ev else None,
        'generations': s.generations, 'updatedAt': s.updated_at, 'completedAt': s.completed_at,
        'review': {'grade': review.grade, 'comment': review.comment, 'createdAt': review.created_at} if review else None,
    }


def users_with_sessions(user_ids):
    rows = (TrainingSession.objects.filter(user_id__in=user_ids).values('user_id')
            .annotate(n=Count('id')))
    return {r['user_id']: r['n'] for r in rows}
