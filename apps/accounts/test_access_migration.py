from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class CourseAccessMigrationTests(TransactionTestCase):
    def test_existing_student_keeps_track_courses_and_history(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        before = [('accounts', '0003_user_learning_track') if app == 'accounts' else (app, migration)
                  for app, migration in latest]
        try:
            executor.migrate(before)
            old = executor.loader.project_state(before).apps
            Track, Course = old.get_model('education', 'LearningTrack'), old.get_model('education', 'Course')
            User = old.get_model('accounts', 'User')
            track = Track.objects.create(title='Existing track', is_published=True)
            course = Course.objects.create(title='Existing course', slug='existing', learning_track=track, is_published=True)
            user = User.objects.create(email='legacy@example.test', password='existing-hash', role='STUDENT', learning_track=track)
            module = old.get_model('education', 'Module').objects.create(course=course, title='Existing module', is_published=True)
            item = old.get_model('education', 'LearningItem').objects.create(module=module, type='PRACTICE',
                title='Existing practice', status='PUBLISHED', position=0)
            progress = old.get_model('education', 'UserLearningItemProgress').objects.create(user=user,
                learning_item=item, progress_percent=80)
            MigrationExecutor(connection).migrate(latest)
            from .models import User as CurrentUser, UserCourseAccess
            from apps.education.access import accessible_courses
            from apps.education.models import UserLearningItemProgress
            current = CurrentUser.objects.get(pk=user.pk)
            self.assertEqual(current.course_access_mode, 'TRACK_DEFAULT')
            self.assertEqual(current.password, 'existing-hash')
            self.assertEqual(list(accessible_courses(current).values_list('pk', flat=True)), [course.pk])
            self.assertFalse(UserCourseAccess.objects.filter(user=current).exists())
            self.assertEqual(UserLearningItemProgress.objects.get(pk=progress.pk).progress_percent, 80)
        finally:
            MigrationExecutor(connection).migrate(latest)
