from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PracticeUpgradeTests(TransactionTestCase):
    def test_upgrade_preserves_criteria_scores_progress_and_legacy_config(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        # Keep lessons before instruction imports, which require education 0019.
        # All historical targets must be compatible with the practice rollback.
        before = [('education', '0013_adaptive_learning') if app == 'education' else
                  ('activities', '0002_alter_practicedefinition_lesson') if app == 'activities' else
                  ('lessons', '0003_lectureimportjob_lectureimportasset_and_more') if app == 'lessons' else (app, migration)
                  for app, migration in latest]
        try:
            executor.migrate(before)
            old = executor.loader.project_state(before).apps
            user = old.get_model('accounts', 'User').objects.create(email='upgrade@practice.test', password='hashed')
            track = old.get_model('education', 'LearningTrack').objects.create(title='Track')
            course = old.get_model('education', 'Course').objects.create(title='Course', learning_track=track)
            module = old.get_model('education', 'Module').objects.create(title='Module', course=course)
            definition = old.get_model('activities', 'PracticeDefinition').objects.create(kind='Legacy', config={'prompt': 'keep'})
            item = old.get_model('education', 'LearningItem').objects.create(title='Practice', module=module, type='PRACTICE',
                status='PUBLISHED', practice=definition, practice_criteria=['A', 'B', 'C', 'D'])
            submission = old.get_model('education', 'PracticeSubmission').objects.create(user=user, item=item,
                criteria=['A', 'B', 'C', 'D'], checks=[True, True, True, False])
            progress = old.get_model('education', 'UserLearningItemProgress').objects.create(user=user, learning_item=item,
                progress_percent=75, is_completed=False)
            stale_item = old.get_model('education', 'LearningItem').objects.create(title='Changed practice', module=module,
                type='PRACTICE', position=1, status='PUBLISHED', practice_criteria=['New'])
            stale = old.get_model('education', 'PracticeSubmission').objects.create(user=user, item=stale_item,
                criteria=['Old'], checks=[True])
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            from apps.education.models import LearningItem, PracticeSubmission, UserLearningItemProgress
            from .models import PracticeDefinition
            migrated = LearningItem.objects.get(pk=item.pk)
            saved = PracticeSubmission.objects.get(pk=submission.pk)
            self.assertEqual(migrated.practice_id, definition.pk)
            self.assertEqual(saved.practice_version_id, migrated.practice_version_id)
            self.assertEqual(saved.result.psi_value, .75)
            self.assertEqual(saved.result.psi_source, 'SELF_ASSESSMENT')
            self.assertEqual(saved.status, 'SUBMITTED')
            self.assertEqual(saved.updated_at, submission.updated_at)
            self.assertEqual([row['title'] for row in saved.criteria_snapshot], ['A', 'B', 'C', 'D'])
            self.assertEqual(UserLearningItemProgress.objects.get(pk=progress.pk).progress_percent, 75)
            self.assertEqual(PracticeDefinition.objects.get(pk=definition.pk).config, {'prompt': 'keep'})
            historical = PracticeSubmission.objects.get(pk=stale.pk)
            self.assertFalse(historical.is_current)
            self.assertEqual(historical.criteria_snapshot[0]['title'], 'Old')
            self.assertEqual(historical.result.psi_value, 1)
            self.assertNotEqual(historical.practice_version_id, LearningItem.objects.get(pk=stale_item.pk).practice_version_id)

            # Exercise reverse data updates followed by schema changes on a
            # populated database, including PostgreSQL's deferred FK triggers.
            executor = MigrationExecutor(connection)
            executor.migrate(before)
            restored = executor.loader.project_state(before).apps
            restored_item = restored.get_model('education', 'LearningItem').objects.get(pk=item.pk)
            self.assertEqual(restored_item.practice_criteria, ['A', 'B', 'C', 'D'])
            restored_progress = restored.get_model('education', 'UserLearningItemProgress').objects.get(pk=progress.pk)
            self.assertEqual(restored_progress.progress_percent, 75)
        finally:
            MigrationExecutor(connection).migrate(latest)
