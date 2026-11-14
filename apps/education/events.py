"""Append transition facts inside the caller's transaction, never reconstruct history."""
from rest_framework import generics, serializers
from common.permissions import is_admin
from .models import LearningEvent


def record_event(user, module, kind, key, *, item=None, attempt=None, submission=None, details=None):
    return LearningEvent.objects.get_or_create(deduplication_key=key, defaults={
        'user': user, 'module': module, 'kind': kind, 'item': item,
        'attempt': attempt, 'submission': submission, 'details': details or {},
    })[0]


def record_unlocks(user, course, before, key, *, item=None, attempt=None, submission=None):
    from .adaptive import module_states
    for short_id, state in module_states(user, course).items():
        if short_id in before and before[short_id]['is_locked'] and not state['is_locked']:
            record_event(user, course.modules.get(pk=state['id']), LearningEvent.Kind.MODULE_UNLOCKED,
                         f'unlock:{key}:{state["id"]}', item=item, attempt=attempt, submission=submission,
                         details={'source_module_id': item.module_id if item else None})


class LearningEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = LearningEvent
        fields = ['id', 'user', 'module', 'item', 'attempt', 'submission', 'kind', 'details', 'created_at']
        read_only_fields = fields


class LearningEventList(generics.ListAPIView):
    serializer_class = LearningEventSerializer

    def get_queryset(self):
        rows = LearningEvent.objects.all()
        if not is_admin(self.request.user):
            return rows.filter(user=self.request.user)
        user = self.request.query_params.get('user')
        if user is not None:
            value = serializers.IntegerField(min_value=1).run_validation(user)
            rows = rows.filter(user_id=value)
        return rows
