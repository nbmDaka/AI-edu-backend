"""Current learning analytics; bounded query count, no personal data in output.

Activity = saved progress, a test attempt (including failure), or practice.
Completion follows learning_summaries: every currently published item completed.
"""
from collections import Counter, defaultdict
from datetime import timedelta

from django.db.models import Count, Max
from django.utils import timezone

from apps.accounts.models import User
from apps.assessments.models import TestAttempt
from .models import Course, LearningItem, LearningTrack, Module, PracticeSubmission, UserLearningItemProgress


def learning_analytics(*, period='30d', track=None, course=None):
    now = timezone.now()
    since = None if period == 'all' else now - timedelta(days=int(period[:-1]))
    students = User.objects.filter(role=User.Role.STUDENT)
    tracks = list(LearningTrack.objects.filter(is_active=True, is_published=True).order_by('id').values('id', 'title'))
    catalog = Course.objects.filter(is_published=True, learning_track__deleted_at__isnull=True,
                                   learning_track__is_active=True, learning_track__is_published=True)
    options = list(catalog.values('id', 'short_id', 'title', 'learning_track_id'))
    selected = [row for row in options if (not track or row['learning_track_id'] == track)
                and (not course or row['id'] == course)]
    modules = list(Module.objects.filter(course_id__in=[row['id'] for row in selected], is_published=True)
                   .values('id', 'short_id', 'title', 'course_id'))
    items = LearningItem.objects.filter(module_id__in=[row['id'] for row in modules], status='PUBLISHED')
    item_counts = dict(items.order_by().values('module_id').annotate(n=Count('id')).values_list('module_id', 'n'))
    progress = UserLearningItemProgress.objects.filter(user__in=students, learning_item__in=items)
    # Aggregate each source in SQL before merging unique learner/module pairs.
    sources = [
        (progress, 'learning_item__module_id', 'updated_at'),
        (TestAttempt.objects.filter(user__in=students, test__deleted_at__isnull=True,
                                   test__learning_item__in=items), 'test__learning_item__module_id', 'completed_at'),
        (PracticeSubmission.objects.filter(user__in=students, item__in=items), 'item__module_id', 'updated_at'),
    ]
    module_users, module_active = defaultdict(set), defaultdict(set)
    for queryset, module_field, date_field in sources:
        rows = (queryset.filter(**{f'{date_field}__lte': now}).order_by()
                .values('user_id', module_field).annotate(last_activity=Max(date_field)))
        for row in rows.iterator():
            mid, uid = row[module_field], row['user_id']
            module_users[mid].add(uid)
            if since is None or row['last_activity'] >= since:
                module_active[mid].add(uid)

    completed_items = defaultdict(dict)
    for row in (progress.filter(is_completed=True).order_by().values('user_id', 'learning_item__module_id')
                .annotate(n=Count('learning_item_id', distinct=True)).iterator()):
        completed_items[row['learning_item__module_id']][row['user_id']] = row['n']
    course_users, course_active = defaultdict(set), defaultdict(set)
    course_completed_items = defaultdict(Counter)
    course_item_counts, course_module_counts = Counter(), Counter()

    def usage(users, active, completed):
        return {'started_students': len(users), 'active_students': len(active),
                'completed_students': len(completed),
                'completion_rate': round(len(completed) / len(users) * 100, 1) if users else 0.0}

    module_rows = []
    for row in modules:
        mid, cid = row['id'], row['course_id']
        count = item_counts.get(mid, 0)
        completed = {uid for uid, n in completed_items[mid].items() if count and n == count}
        module_rows.append({**row, 'item_count': count, **usage(module_users[mid], module_active[mid], completed)})
        course_users[cid].update(module_users[mid])
        course_active[cid].update(module_active[mid])
        course_completed_items[cid].update(completed_items[mid])
        course_item_counts[cid] += count
        course_module_counts[cid] += 1

    track_users, track_active = defaultdict(set), defaultdict(set)
    all_users, all_active, all_completed = set(), set(), set()
    course_rows = []
    for row in selected:
        cid, tid = row['id'], row['learning_track_id']
        count = course_item_counts[cid]
        completed = {uid for uid, n in course_completed_items[cid].items() if count and n == count}
        course_rows.append({**row, 'module_count': course_module_counts[cid], 'item_count': count,
                            **usage(course_users[cid], course_active[cid], completed)})
        track_users[tid].update(course_users[cid])
        track_active[tid].update(course_active[cid])
        all_users.update(course_users[cid])
        all_active.update(course_active[cid])
        all_completed.update(completed)

    assigned = dict(students.order_by().values('learning_track_id').annotate(n=Count('id'))
                    .values_list('learning_track_id', 'n'))
    selected_tracks = {row['learning_track_id'] for row in selected} if course else None
    track_rows = [{**row, 'assigned_students': assigned.get(row['id'], 0),
                   'started_students': len(track_users[row['id']]), 'active_students': len(track_active[row['id']])}
                  for row in tracks if (not track or row['id'] == track)
                  and (selected_tracks is None or row['id'] in selected_tracks)]
    return {
        'generated_at': now, 'period': period, 'since': since,
        'filters': {'track': track, 'course': course},
        'options': {'tracks': tracks, 'courses': options},
        'summary': {'total_students': students.count(), 'started_students': len(all_users),
                    'active_students': len(all_active), 'completed_students': len(all_completed),
                    'course_count': len(selected), 'module_count': len(modules), 'item_count': sum(item_counts.values())},
        'tracks': track_rows, 'courses': course_rows, 'modules': module_rows,
    }
