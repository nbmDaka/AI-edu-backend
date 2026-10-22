from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from .models import MediaFile
from .views import AttachmentUploadView


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'}},
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class OfficeAttachmentTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin@attachment.test', 'Password123!', role='ADMIN')
        self.student = User.objects.create_user('student@attachment.test', 'Password123!')
        self.client = APIClient()
        self.client.force_login(self.admin)

    def test_every_supported_mime_type_fits_the_database_field(self):
        size = MediaFile._meta.get_field('content_type').max_length
        for content_type in AttachmentUploadView.allowed_types.values():
            with self.subTest(content_type=content_type):
                self.assertLessEqual(len(content_type), size)

    def test_office_mime_types_upload_and_download_without_truncation(self):
        for extension in ('.docx', '.pptx', '.xlsx'):
            with self.subTest(extension=extension):
                content_type = AttachmentUploadView.allowed_types[extension]
                upload = SimpleUploadedFile('original' + extension, b'attachment storage fixture', content_type=content_type)
                response = self.client.post('/api/v1/media/files/', {'file': upload}, format='multipart')
                self.assertEqual(response.status_code, 201, response.data)
                media = MediaFile.objects.get(pk=response.data['id'])
                self.assertEqual(media.content_type, content_type)
                download = self.client.get(response.data['url'])
                self.assertEqual(download.status_code, 200)
                self.assertEqual(download['Content-Type'], 'application/octet-stream')
                self.assertIn('attachment;', download['Content-Disposition'])
                self.assertEqual(b''.join(download.streaming_content), b'attachment storage fixture')

    def test_attachment_upload_remains_admin_only(self):
        self.client.force_login(self.student)
        upload = SimpleUploadedFile('work.docx', b'fixture')
        response = self.client.post('/api/v1/media/files/', {'file': upload}, format='multipart')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(MediaFile.objects.count(), 0)
