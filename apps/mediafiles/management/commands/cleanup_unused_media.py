from django.core.management.base import BaseCommand
from apps.mediafiles.models import MediaFile
from apps.mediafiles.usage import draft_media_ids, practice_source_media_ids

class Command(BaseCommand):
    help = 'Show or soft-delete unused media, preserving files and archived content references'
    def add_arguments(self, parser):
        parser.add_argument('--delete', action='store_true')
    def handle(self, *args, **options):
        unused = MediaFile.objects.filter(lessonblock__isnull=True, lessonattachment__isnull=True,
                                          learningtrack__isnull=True, course__isnull=True,
                                          lecture_import_assets__isnull=True).exclude(pk__in=draft_media_ids() | practice_source_media_ids()).distinct()
        count = unused.count()
        if options['delete']:
            for media in unused.iterator():
                media.delete()
        self.stdout.write(f'{count} unused files' + (' deleted' if options['delete'] else ' found; use --delete to remove'))
