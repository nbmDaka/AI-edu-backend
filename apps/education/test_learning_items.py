import importlib
from types import SimpleNamespace

from django.apps import apps
from django.db import connection
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.activities.models import PracticeDefinition
from apps.assessments.models import Option, Question, Test, TestAttempt
from apps.education.models import Course, LearningItem, LearningTrack, Lesson, Module
from apps.lessons.models import LessonBlock


class LearningItemFlowTests(TestCase):
    def setUp(self):
        self.track = LearningTrack.objects.create(title='Track', is_published=True)
        self.course = Course.objects.create(learning_track=self.track, title='Course', is_published=True)
        self.module = Module.objects.create(course=self.course, title='Module', is_published=True)
        self.admin = User.objects.create_user(email='item-admin@example.test', password='Password1234!', role='ADMIN')
        self.student = User.objects.create_user(email='item-student@example.test', password='Password1234!', learning_track=self.track)
        self.admin_client = APIClient()
        self.admin_client.force_login(self.admin)
        self.student_client = APIClient()
        self.student_client.force_login(self.student)

    def test_order_publication_and_test_permissions(self):
        paths = []
        for kind, title in [('LECTURE', 'Lecture A'), ('LECTURE', 'Lecture B'),
                            ('PRACTICE', 'Practice'), ('TEST', 'Test')]:
            response = self.admin_client.post('/api/v1/items/', {'module': self.module.pk, 'type': kind, 'title': title}, format='json')
            self.assertEqual(response.status_code, 201, response.data)
            paths.append(response.data)
        self.assertEqual([item['position'] for item in paths], [0, 1, 2, 3])
        self.assertEqual(self.student_client.get('/api/v1/items/').data['results'], [])
        moved = self.admin_client.patch(f"/api/v1/items/{paths[2]['short_id']}/", {'position': 1}, format='json')
        self.assertEqual(moved.status_code, 200, moved.data)
        self.assertEqual(list(LearningItem.objects.filter(module=self.module).values_list('type', flat=True)),
                         ['LECTURE', 'PRACTICE', 'LECTURE', 'TEST'])
        lecture = paths[0]
        draft_path = f"/api/v1/lessons/{lecture['lesson_short_id']}/draft/"
        self.assertEqual(self.admin_client.put(draft_path, {'title': 'Published lecture', 'description': '',
            'blocks': [{'type': 'TEXT', 'position': 0, 'content': 'Live', 'media': None, 'config': {}}]}, format='json').status_code, 200)
        self.assertEqual(self.student_client.get(f"/api/v1/items/{lecture['short_id']}/").status_code, 404)
        self.assertEqual(self.admin_client.post(draft_path).status_code, 200)
        self.assertEqual(self.student_client.get(f"/api/v1/items/{lecture['short_id']}/").data['title'], 'Published lecture')
        practice = paths[2]
        self.assertEqual(self.admin_client.patch(f"/api/v1/items/{practice['short_id']}/", {'status': 'PUBLISHED'}, format='json').status_code, 200)
        test = paths[3]
        payload = {'title': 'Test', 'is_published': True, 'questions': [{'text': 'One?', 'position': 0, 'points': 1,
            'options': [{'text': 'Yes', 'position': 0, 'is_correct': True}, {'text': 'No', 'position': 1, 'is_correct': False}]}]}
        self.assertEqual(self.admin_client.put(f"/api/v1/items/{test['short_id']}/test/", payload, format='json').status_code, 200)
        public = self.student_client.get(f"/api/v1/items/{test['short_id']}/test/")
        self.assertEqual(public.status_code, 200)
        self.assertNotIn('is_correct', str(public.data))
        question = public.data['questions'][0]
        answer = [{'question': question['id'], 'option': question['options'][0]['id']}]
        self.assertEqual(self.student_client.post(f"/api/v1/items/{test['short_id']}/attempts/", {'answers': answer}, format='json').status_code, 201)
        self.assertEqual(self.admin_client.delete(f"/api/v1/items/{test['short_id']}/").status_code, 400)
        self.assertEqual([row['type'] for row in self.student_client.get('/api/v1/items/').data['results']],
                         ['LECTURE', 'PRACTICE', 'TEST'])
        other_track = LearningTrack.objects.create(title='Other', is_published=True)
        outsider = User.objects.create_user(email='item-outsider@example.test', password='Password1234!', learning_track=other_track)
        outsider_client = APIClient()
        outsider_client.force_login(outsider)
        self.assertEqual(outsider_client.get(f"/api/v1/items/{test['short_id']}/test/").status_code, 404)

    def test_legacy_data_migration_preserves_content_and_attempts(self):
        lesson = Lesson.objects.create(module=self.module, title='Old lecture', position=5, status='PUBLISHED')
        block = LessonBlock.objects.create(lesson=lesson, type='TEXT', position=0, content='Original')
        test = Test.objects.create(lesson=lesson, title='Old test', is_published=True)
        question = Question.objects.create(test=test, text='Question', position=0)
        option = Option.objects.create(question=question, text='Answer', is_correct=True, position=0)
        attempt = TestAttempt.objects.create(test=test, user=self.student, test_version=1,
            answers=[{'question': question.pk, 'option': option.pk}], snapshot=[{'correct': 'Answer'}],
            earned_points=1, total_points=1, percent=100, passed=True)
        practice = PracticeDefinition.objects.create(lesson=lesson, kind='Essay', config={'prompt': 'Write'})
        migration = importlib.import_module('apps.education.migrations.0009_migrate_learning_items')
        migration.migrate_items(apps, SimpleNamespace(connection=connection))
        items = list(LearningItem.objects.filter(module=self.module))
        self.assertEqual([item.type for item in items], ['LECTURE', 'TEST', 'PRACTICE'])
        self.assertEqual([item.position for item in items], [0, 1, 2])
        self.assertEqual(items[0].short_id, lesson.short_id)
        self.assertEqual(items[0].lesson_id, lesson.pk)
        self.assertEqual(items[1].test_id, test.pk)
        self.assertEqual(items[2].practice_id, practice.pk)
        self.assertEqual(LessonBlock.objects.get(pk=block.pk).content, 'Original')
        self.assertEqual(TestAttempt.objects.get(pk=attempt.pk).snapshot[0]['correct'], 'Answer')

    def create_item(self, kind, module=None):
        response = self.admin_client.post('/api/v1/items/', {
            'module': (module or self.module).pk, 'type': kind, 'title': kind,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_only_one_test_per_module_without_orphan_content(self):
        test = self.create_item('TEST')
        count = Test.objects.count()
        response = self.admin_client.post('/api/v1/items/', {
            'module': self.module.pk, 'type': 'TEST', 'title': 'Second test',
        }, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Test.objects.count(), count)
        self.assertEqual(LearningItem.objects.filter(module=self.module, type='TEST').count(), 1)
        other = Module.objects.create(course=self.course, title='Other module')
        self.create_item('TEST', module=other)
        self.assertEqual(self.admin_client.delete(f"/api/v1/items/{test['short_id']}/").status_code, 204)
        self.create_item('TEST')

    def test_new_content_is_inserted_before_test(self):
        test = self.create_item('TEST')
        lecture = self.create_item('LECTURE')
        practice = self.create_item('PRACTICE')
        self.assertEqual(lecture['position'], 0)
        self.assertEqual(practice['position'], 1)
        items = list(LearningItem.objects.filter(module=self.module))
        self.assertEqual([item.type for item in items], ['LECTURE', 'PRACTICE', 'TEST'])
        self.assertEqual([item.position for item in items], [0, 1, 2])
        self.assertEqual(items[-1].short_id, test['short_id'])
        self.assertEqual(Lesson.objects.get(pk=lecture['lesson']).position, 0)

    def test_reordering_cannot_move_test_or_content_past_test(self):
        lecture = self.create_item('LECTURE')
        practice = self.create_item('PRACTICE')
        test = self.create_item('TEST')
        for item, position in [(test, 0), (test, 1), (lecture, 2), (practice, 2)]:
            response = self.admin_client.patch(f"/api/v1/items/{item['short_id']}/", {'position': position}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(list(LearningItem.objects.filter(module=self.module).values_list('type', flat=True)),
                         ['LECTURE', 'PRACTICE', 'TEST'])
        moved = self.admin_client.patch(f"/api/v1/items/{practice['short_id']}/", {'position': 0}, format='json')
        self.assertEqual(moved.status_code, 200, moved.data)
        self.assertEqual(list(LearningItem.objects.filter(module=self.module).values_list('type', flat=True)),
                         ['PRACTICE', 'LECTURE', 'TEST'])
        self.assertEqual(Lesson.objects.get(pk=lecture['lesson']).position, 1)
        response = self.admin_client.patch(f"/api/v1/items/{test['short_id']}/", {'title': 'Renamed'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['position'], 2)
        self.assertEqual(self.admin_client.delete(f"/api/v1/items/{practice['short_id']}/").status_code, 204)
        self.assertEqual(list(LearningItem.objects.filter(module=self.module).values_list('type', 'position')),
                         [('LECTURE', 0), ('TEST', 1)])

    def test_reordering_without_test_still_works(self):
        lecture = self.create_item('LECTURE')
        self.create_item('PRACTICE')
        response = self.admin_client.patch(f"/api/v1/items/{lecture['short_id']}/", {'position': 1}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(list(LearningItem.objects.filter(module=self.module).values_list('type', flat=True)),
                         ['PRACTICE', 'LECTURE'])
