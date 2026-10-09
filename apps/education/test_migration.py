from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class LearningItemMigrationTests(TransactionTestCase):
    def test_upgrade_keeps_legacy_rows_media_and_results(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        before = [('education', '0007_lesson_draft_data') if app == 'education' else
                  ('assessments', '0001_initial') if app == 'assessments' else
                  ('activities', '0001_initial') if app == 'activities' else (app, migration)
                  for app, migration in latest]
        try:
            executor.migrate(before)
            old = executor.loader.project_state(before).apps
            User = old.get_model('accounts', 'User')
            Track = old.get_model('education', 'LearningTrack')
            Course = old.get_model('education', 'Course')
            Module = old.get_model('education', 'Module')
            Lesson = old.get_model('education', 'Lesson')
            Block = old.get_model('lessons', 'LessonBlock')
            Media = old.get_model('mediafiles', 'MediaFile')
            Test = old.get_model('assessments', 'Test')
            Attempt = old.get_model('assessments', 'TestAttempt')
            Practice = old.get_model('activities', 'PracticeDefinition')
            user = User.objects.create(email='migration@example.test', password='hashed', role='STUDENT')
            track = Track.objects.create(title='Legacy', is_published=True)
            course = Course.objects.create(learning_track=track, title='Legacy course', slug='legacy-course', is_published=True)
            module = Module.objects.create(course=course, title='Legacy module', is_published=True)
            first = Lesson.objects.create(module=module, title='First', position=1, status='PUBLISHED')
            second = Lesson.objects.create(module=module, title='Second', position=4, status='PUBLISHED')
            media = Media.objects.create(file='lessons/existing.png', original_name='existing.png',
                content_type='image/png', size=100, uploaded_by=user)
            block = Block.objects.create(lesson=first, type='IMAGE', position=0, content='Caption', media=media)
            test = Test.objects.create(lesson=first, title='Legacy test', is_published=True)
            attempt = Attempt.objects.create(test=test, user=user, test_version=1, answers=[], snapshot=[{'correct': 'A'}],
                earned_points=1, total_points=1, percent=100, passed=True)
            practice = Practice.objects.create(lesson=first, kind='Essay', config={'prompt': 'Write'})
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            from apps.education.models import LearningItem
            from apps.lessons.models import LessonBlock
            from apps.assessments.models import TestAttempt
            from apps.activities.models import PracticeDefinition
            items = list(LearningItem.objects.filter(module_id=module.pk))
            self.assertEqual([item.type for item in items], ['LECTURE', 'PRACTICE', 'LECTURE', 'TEST'])
            self.assertEqual([item.position for item in items], [0, 1, 2, 3])
            self.assertEqual(items[0].short_id, first.short_id)
            self.assertEqual(items[2].short_id, second.short_id)
            self.assertEqual(items[3].test_id, test.pk)
            self.assertEqual(items[1].practice_id, practice.pk)
            self.assertEqual(LessonBlock.objects.get(pk=block.pk).media_id, media.pk)
            self.assertEqual(TestAttempt.objects.get(pk=attempt.pk).snapshot[0]['correct'], 'A')
            self.assertEqual(PracticeDefinition.objects.get(pk=practice.pk).config['prompt'], 'Write')
        finally:
            MigrationExecutor(connection).migrate(latest)
