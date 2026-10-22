import json
from apps.education.models import Lesson


def source_media_ids(config):
    ids = {file['media'] for file in config.get('source_files', [])
           if isinstance(file, dict) and type(file.get('media')) is int}
    for instruction in config.get('instructions', []):
        try:
            pending = [json.loads(instruction)]
        except (TypeError, ValueError, RecursionError):
            continue
        while pending:
            node = pending.pop()
            if not isinstance(node, dict):
                continue
            attrs = node.get('attrs', {})
            if node.get('type') == 'practiceImage' and isinstance(attrs, dict) and type(attrs.get('media')) is int:
                ids.add(attrs['media'])
            if isinstance(node.get('content'), list):
                pending.extend(node['content'])
    return ids


def practice_source_media_ids():
    from apps.activities.models import PracticeVersion
    ids = set()
    for config in PracticeVersion.objects.values_list('config', flat=True).iterator():
        ids.update(source_media_ids(config))
    return ids


def practice_source_is_used(media_id):
    return media_id in practice_source_media_ids()


def can_download_practice_source(user, media_id):
    from apps.activities.models import PracticeVersion
    from apps.education.access import can_access_item
    versions = PracticeVersion.objects.filter(status='PUBLISHED',
        practice_definition__deleted_at__isnull=True,
        practice_definition__learning_item__deleted_at__isnull=True,
        practice_definition__learning_item__status='PUBLISHED',
    ).select_related('practice_definition__learning_item__module__course')
    for version in versions.iterator():
        if media_id not in source_media_ids(version.config):
            continue
        item = version.practice_definition.learning_item
        # Historical files remain accessible to learners whose attempt uses that version.
        if (item.practice_version_id == version.pk or version.submissions.filter(user=user).exists()) and can_access_item(user, item):
            return True
    return False


def draft_media_ids():
    ids = set()
    for draft in Lesson.all_objects.exclude(draft_data=None).values_list('draft_data', flat=True).iterator():
        if not isinstance(draft, dict):
            continue
        for block in draft.get('blocks', []):
            if isinstance(block, dict) and isinstance(block.get('media'), int):
                ids.add(block['media'])
        for attachment in draft.get('attachments', []):
            media_id = attachment.get('media') if isinstance(attachment, dict) else attachment
            if isinstance(media_id, int):
                ids.add(media_id)
    return ids
