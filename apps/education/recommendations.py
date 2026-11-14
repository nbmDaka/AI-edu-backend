"""Deterministic study advice; this reference never participates in admission."""
from copy import deepcopy

from .models import LearningItem

RECOMMENDATION_REFERENCE = 0.50


def published_material(item):
    return bool(item.status == 'PUBLISHED' and item.module.is_published
                and item.module.deleted_at is None and item.module.course.is_published
                and item.type in ('LECTURE', 'PRACTICE')
                and (item.type != 'LECTURE' or (item.lesson_id and item.lesson.status == 'PUBLISHED' and item.lesson.deleted_at is None))
                and (item.type != 'PRACTICE' or (item.practice_version_id and item.practice_version.status == 'PUBLISHED')))


def recommend_materials(user, module, competencies):
    from .access import can_access_item
    items = [item for item in module.items.select_related('module__course__learning_track', 'lesson', 'practice_version')
             .filter(status='PUBLISHED', type__in=['LECTURE', 'PRACTICE']).order_by('position', 'id')
             if published_material(item) and can_access_item(user, item)]
    groups = []
    for name, score in sorted(competencies.items(), key=lambda entry: (entry[1], entry[0])):
        if score >= RECOMMENDATION_REFERENCE:
            continue
        materials = [{'item_id': item.pk, 'short_id': item.short_id, 'module_id': module.pk,
                      'title': item.title, 'type': item.type}
                     for item in items if name in item.competency_keys]
        groups.append({'competency': name, 'score': score, 'reason': 'below_reference', 'materials': materials})
    fallback = ('no_data' if not competencies else 'no_weak_competencies' if not groups
                else 'unmapped_competencies' if any(not group['materials'] for group in groups) else None)
    return {'version': 1, 'reference_score': RECOMMENDATION_REFERENCE, 'groups': groups, 'fallback': fallback}


def resolve_recommendations(snapshot, user):
    """Keep saved evidence intact; resolve each link using today's access rules."""
    from .access import can_access_item
    result = deepcopy(snapshot)
    materials = [material for group in result['groups'] for material in group['materials']]
    items = {item.pk: item for item in LearningItem.objects.select_related('module__course__learning_track', 'lesson', 'practice_version')
             .filter(pk__in=[material['item_id'] for material in materials])}
    for material in materials:
        item = items.get(material['item_id'])
        available = bool(user and item and item.module_id == material['module_id']
                         and item.type == material['type'] and published_material(item) and can_access_item(user, item))
        material['available_item_short_id'] = item.short_id if available else None
    return result
