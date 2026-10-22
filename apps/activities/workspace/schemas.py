"""Lecture v1 and the small, safe Tiptap subset already supported by the frontend."""
import json
import re
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from apps.activities.serializers import StrictSerializer


SECTION_IDS = ('goal', 'learning_outcomes', 'outline', 'introduction', 'main_material',
               'examples', 'key_concepts', 'conclusions', 'control_questions', 'self_study')
MAX_CONTENT_BYTES = 250_000


class LectureParameters(StrictSerializer):
    discipline = serializers.CharField(max_length=200)
    topic = serializers.CharField(max_length=300)
    course = serializers.CharField(max_length=100)
    audience = serializers.CharField(max_length=500)
    duration_minutes = serializers.IntegerField(min_value=5, max_value=480)
    difficulty = serializers.ChoiceField(choices=['beginner', 'intermediate', 'advanced'])
    learning_outcomes = serializers.ListField(child=serializers.CharField(max_length=500), min_length=1, max_length=30)
    language = serializers.ChoiceField(choices=['ru', 'kk', 'en'])


def validate_parameters(value):
    serializer = LectureParameters(data=value)
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


def validate_tiptap(document):
    budget = [0]
    children_for = {
        'doc': {'paragraph', 'heading', 'bulletList', 'orderedList', 'blockquote', 'codeBlock'},
        'paragraph': {'text', 'hardBreak'}, 'heading': {'text', 'hardBreak'},
        'bulletList': {'listItem'}, 'orderedList': {'listItem'},
        'listItem': {'paragraph', 'bulletList', 'orderedList'},
        'blockquote': {'paragraph', 'heading', 'bulletList', 'orderedList', 'codeBlock'},
        'codeBlock': {'text'}, 'hardBreak': set(), 'text': set(),
    }

    def visit(node, depth=0):
        budget[0] += 1
        if depth > 12 or budget[0] > 5000 or not isinstance(node, dict):
            raise ValidationError({'content': 'Слишком сложный документ Tiptap'})
        kind = node.get('type')
        if not isinstance(kind, str) or kind not in children_for:
            raise ValidationError({'content': 'Неподдерживаемый узел Tiptap'})
        allowed = {'type', 'text', 'marks'} if kind == 'text' else {'type', 'content', 'attrs'}
        if set(node) - allowed:
            raise ValidationError({'content': 'Неизвестные поля узла Tiptap'})
        if kind == 'text':
            if not isinstance(node.get('text'), str) or not node['text']:
                raise ValidationError({'content': 'Текстовый узел должен содержать текст'})
            marks = node.get('marks', [])
            if not isinstance(marks, list) or len(marks) > 4:
                raise ValidationError({'content': 'Некорректное форматирование'})
            for mark in marks:
                if not isinstance(mark, dict) or mark.get('type') not in ('bold', 'italic', 'code', 'link'):
                    raise ValidationError({'content': 'Неподдерживаемое форматирование'})
                if mark['type'] == 'link':
                    attrs = mark.get('attrs')
                    if (set(mark) != {'type', 'attrs'} or not isinstance(attrs, dict) or
                            set(attrs) - {'href', 'target', 'rel', 'class', 'title'} or
                            not isinstance(attrs.get('href'), str) or not attrs['href'].lower().startswith(('https:', 'http:', 'mailto:'))):
                        raise ValidationError({'content': 'Некорректная ссылка'})
                    if any(value is not None and not isinstance(value, str) for value in attrs.values()):
                        raise ValidationError({'content': 'Некорректные атрибуты ссылки'})
                elif set(mark) != {'type'}:
                    raise ValidationError({'content': 'Некорректное форматирование'})
            return
        attrs = node.get('attrs', {})
        if not isinstance(attrs, dict):
            raise ValidationError({'content': 'Некорректные атрибуты Tiptap'})
        allowed_attrs = {'heading': {'level'}, 'orderedList': {'start', 'type'}, 'codeBlock': {'language'}}.get(kind, set())
        if set(attrs) - allowed_attrs:
            raise ValidationError({'content': 'Неподдерживаемые атрибуты Tiptap'})
        if kind == 'heading' and (type(attrs.get('level')) is not int or attrs['level'] not in (2, 3)):
            raise ValidationError({'content': 'Уровень заголовка должен быть 2 или 3'})
        if kind == 'orderedList' and 'start' in attrs and (type(attrs['start']) is not int or attrs['start'] < 1):
            raise ValidationError({'content': 'Некорректное начало списка'})
        if kind == 'orderedList' and attrs.get('type') not in (None, '1', 'a', 'A', 'i', 'I'):
            raise ValidationError({'content': 'Некорректный тип списка'})
        if kind == 'codeBlock' and attrs.get('language') is not None and not isinstance(attrs['language'], str):
            raise ValidationError({'content': 'Некорректный язык блока кода'})
        children = node.get('content', [])
        if not isinstance(children, list) or kind == 'hardBreak' and ('content' in node or attrs):
            raise ValidationError({'content': 'Некорректные дочерние узлы'})
        if kind in ('doc', 'bulletList', 'orderedList', 'listItem', 'blockquote') and not children:
            raise ValidationError({'content': 'Структурный узел не может быть пустым'})
        if kind == 'listItem' and (not isinstance(children[0], dict) or children[0].get('type') != 'paragraph'):
            raise ValidationError({'content': 'Первый узел элемента списка должен быть paragraph'})
        for child in children:
            if (not isinstance(child, dict) or not isinstance(child.get('type'), str) or
                    child['type'] not in children_for[kind]):
                raise ValidationError({'content': 'Некорректная структура Tiptap'})
            visit(child, depth + 1)

    if not isinstance(document, dict) or document.get('type') != 'doc':
        raise ValidationError({'content': 'Ожидается документ Tiptap с type=doc'})
    visit(document)


def validate_document(value, section_ids=SECTION_IDS):
    if not isinstance(value, dict) or set(value) != {'title', 'sections'}:
        raise ValidationError({'content': 'Ожидается документ с title и sections'})
    if not isinstance(value['title'], str) or not value['title'].strip() or len(value['title']) > 300:
        raise ValidationError({'content': 'Некорректное название документа'})
    try:
        size = len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
    except (TypeError, ValueError, RecursionError):
        raise ValidationError({'content': 'Некорректный JSON документа'}) from None
    if size > MAX_CONTENT_BYTES:
        raise ValidationError({'content': 'Документ превышает допустимый размер'})
    sections = value['sections']
    if not isinstance(sections, list) or len(sections) != len(section_ids):
        raise ValidationError({'content': 'Документ должен содержать все разделы выбранного конструктора'})
    for section, section_id in zip(sections, section_ids):
        if (not isinstance(section, dict) or set(section) != {'id', 'title', 'content'} or section['id'] != section_id or
                not isinstance(section['title'], str) or not section['title'].strip() or len(section['title']) > 200):
            raise ValidationError({'content': 'Некорректный раздел или стабильный ID'})
        validate_tiptap(section['content'])
    def words(node):
        if node.get('type') == 'text':
            return len(re.findall(r'\S+', node['text']))
        return sum(words(child) for child in node.get('content', []))
    count = len(value['title'].split()) + sum(len(section['title'].split()) + words(section['content']) for section in sections)
    if count > 3000:
        raise ValidationError({'content': 'Документ должен содержать не более 3000 слов'})
    return value


def validate_lecture(value):
    return validate_document(value, SECTION_IDS)
