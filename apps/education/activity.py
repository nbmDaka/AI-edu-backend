"""Activity log (журнал активности): record learner actions and read them back.

Recording is called from the existing flows (login, lecture progress, practice submit, test attempt);
it stores only what is needed to show the action later, never answers or code.
"""
from datetime import timedelta

from django.contrib.auth.signals import user_logged_in
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.dispatch import receiver
from django.utils import timezone

from .models import ActivityEvent

PERIODS = {'7d': 7, '30d': 30, '90d': 90}


def log_event(user, type, item=None, module=None, course=None, **data):
    if item is not None:
        module = module or item.module
    if module is not None:
        course = course or module.course
    return ActivityEvent.objects.create(user=user, type=type, item=item, module=module, course=course, data=data)


@receiver(user_logged_in, dispatch_uid='education.activity.login')
def log_login(sender, request, user, **kwargs):
    if getattr(user, 'role', None) == 'STUDENT':  # the log is about learners; staff sign-ins are not learning
        log_event(user, ActivityEvent.Type.LOGIN)


def since_for(period):
    if period in (None, '', 'all'):
        return None
    if period not in PERIODS:
        raise ValueError(period)
    return timezone.now() - timedelta(days=PERIODS[period])


def daily_counts(events, days=14):
    """Events per day and type for the last `days` days (oldest first), zero-filled."""
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    rows = (events.filter(created_at__date__gte=start).annotate(day=TruncDate('created_at'))
            .values('day', 'type').annotate(count=Count('id')).order_by())
    table = {start + timedelta(days=offset): {} for offset in range(days)}
    for row in rows:
        if row['day'] in table:
            table[row['day']][row['type']] = row['count']
    return [{'date': day, 'total': sum(counts.values()), 'by_type': counts} for day, counts in table.items()]
