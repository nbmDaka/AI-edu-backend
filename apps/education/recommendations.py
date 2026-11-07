"""Explains a module test result to the learner: what to do next and why.

The pass rule itself stays in adaptive.evaluate_module_attempt (readiness >= module threshold);
this module only turns the numbers of one attempt into a recommendation stored with it.
"""

WEAK_TOPIC_LIMIT = 0.6
# Levels are measured from the module threshold in readiness points, so they keep working
# whatever threshold an administrator picks and if the readiness scale is normalised later.
LEVEL_MARGIN = 15
INDICATORS = ('theory', 'practice', 'competency')
REVIEW_ITEMS_PER_TOPIC = 3
TYPE_ORDER = {'LECTURE': 0, 'PRACTICE': 1}


def recommendation_level(gap):
    if gap < -LEVEL_MARGIN:
        return 'repeat'
    if gap < 0:
        return 'extra_practice'
    if gap < LEVEL_MARGIN:
        return 'next'
    return 'advanced'


def weak_topics(question_results, limit=WEAK_TOPIC_LIMIT):
    """Competencies of this attempt answered below `limit`, weakest first, with the author's spelling."""
    grouped = {}
    for question, correct in question_results:
        label = question.competency.strip()
        key = label.casefold()
        row = grouped.setdefault(key, {'competency': key, 'title': label, 'correct': 0, 'total': 0})
        row['correct'] += int(bool(correct))
        row['total'] += 1
    topics = []
    for row in grouped.values():
        row['score'] = round(row['correct'] / row['total'], 4)
        if row['score'] < limit:
            topics.append(row)
    return sorted(topics, key=lambda row: (row['score'], row['title'].casefold()))


def review_plan(topics, items):
    """Materials of the module to review for each weak topic.

    An item matches a topic when the author tagged it with that competency; otherwise when the
    topic name appears in its title. Lectures come before practicals, then module order.
    """
    plan = []
    ordered = sorted(items, key=lambda item: (TYPE_ORDER.get(item.type, 9), item.position, item.pk))
    for topic in topics:
        key = topic['competency']
        tagged = [item for item in ordered if key in {tag.strip().casefold() for tag in (item.competencies or [])}]
        matched = tagged or [item for item in ordered if key in item.title.casefold()]
        plan.append({'competency': key, 'title': topic['title'], 'items': [
            {'short_id': item.short_id, 'title': item.title, 'type': item.type, 'match': 'tag' if tagged else 'title'}
            for item in matched[:REVIEW_ITEMS_PER_TOPIC]]})
    return plan


def build_recommendation(result, question_results, review_items=()):
    passed = bool(result['module_passed'])
    gap = round(result['readiness'] - result['threshold'], 2)
    if passed:
        action = 'next_module' if result['next_item_short_id'] else 'course_completed'
    else:
        action = 'repeat_module'
    weakest = min(INDICATORS, key=lambda name: (result[name], INDICATORS.index(name)))
    topics = weak_topics(question_results)
    return {
        'status': 'passed' if passed else 'repeat',
        'action': action,
        'level': recommendation_level(gap),
        'gap': gap,
        'weakest_indicator': weakest,
        'weak_topics': topics,
        'weak_topic_limit': WEAK_TOPIC_LIMIT,
        'review': review_plan(topics, review_items),
    }


def review_route(recommendation, content):
    """What a learner studies again before the next attempt.

    Close to the threshold (level `extra_practice`) only the materials recommended for the weak
    topics are reopened, in module order. Far below it, or when no material matches a weak topic,
    the whole module is studied again.
    """
    if recommendation['level'] == 'extra_practice':
        wanted = {entry['short_id'] for topic in recommendation['review'] for entry in topic['items']}
        items = [item for item in content if item.short_id in wanted]
        if items:
            return {'scope': 'topics', 'items': items}
    return {'scope': 'module', 'items': []}
