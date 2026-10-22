import json
from io import StringIO
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.lessons.importing.validation import validate_upload
from .services import save_draft, publish_draft
from .test_workspace import WorkspaceFixture


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class PracticeSourceFileTests(WorkspaceFixture, TestCase):
    def setUp(self):
        super().setUp()
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        settings = override_settings(MEDIA_ROOT=folder.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.admin = APIClient()
        self.admin.force_login(User.objects.create_user('source-admin@test.test', 'Password123!', role='ADMIN'))
        self.config = {**self.config, 'instructions': [json.dumps({'type': 'doc', 'content': [
            {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Use the source file'}]}]})]}

    def upload(self, size=16):
        return self.admin.post('/api/v1/media/files/', {'file': SimpleUploadedFile(
            'source.txt', b'x' * size, content_type='text/plain')}, format='multipart')

    def test_upload_boundary_and_import_limit(self):
        self.assertEqual(self.upload(10 * 1024 * 1024).status_code, 201)
        self.assertEqual(self.upload(10 * 1024 * 1024 + 1).status_code, 400)
        with self.assertRaises(ValidationError):
            validate_upload(SimpleUploadedFile('source.pdf', b'x' * (10 * 1024 * 1024 + 1)))
        self.assertEqual(self.admin.post('/api/v1/media/', {'file': SimpleUploadedFile(
            'source.png', b'x' * (10 * 1024 * 1024 + 1), content_type='image/png')}, format='multipart').status_code, 400)

    def test_source_is_protected_in_draft_and_downloadable_after_publication(self):
        uploaded = self.upload().data
        source = {key: uploaded[key] for key in ('media', 'name', 'size', 'url')}
        draft = {**self.draft, 'config': {**self.config, 'source_files': [source]}}
        save_draft(self.item, self.user, draft)
        call_command('cleanup_unused_media', delete=True, stdout=StringIO())
        self.assertEqual(self.client.get(source['url']).status_code, 403)
        self.assertEqual(self.admin.delete(f'/api/v1/media/{source["media"]}/').status_code, 400)
        publish_draft(self.item)
        response = self.client.get(source['url'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'x' * 16)
        response.close()
        self.other.learning_track = None
        self.other.save()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(source['url']).status_code, 403)

    def test_invalid_source_metadata_and_unknown_media_are_rejected(self):
        uploaded = self.upload().data
        for source in ({**uploaded, 'media': 999999}, {**uploaded, 'url': 'https://example.com'},
                       {**uploaded, 'name': 'forged.txt'}, {**uploaded, 'size': 1}):
            with self.subTest(source=source), self.assertRaises(ValidationError):
                save_draft(self.item, self.user, {**self.draft, 'config': {**self.config, 'source_files': [source]}})
