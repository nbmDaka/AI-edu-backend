from django.contrib import admin
from django.urls import include, path
from apps.education.analytics_views import AdminLearningAnalyticsView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from apps.accounts import views as accounts
from apps.accounts import admin_views as admin_accounts
from apps.education import views as education
from apps.education import certificates
from apps.lessons import views as lessons
from apps.assessments import views as assessments
from apps.mediafiles import views as mediafiles
from apps.tutor.views import TutorChatView, TutorSpeechView, TutorTranscribeView
from apps.activities import views as activities
from apps.activities.workspace import views as workspace
from apps.lessons.importing import views as lecture_imports

urlpatterns = [
    path('django-admin/', admin.site.urls),
    path('api/v1/csrf/', accounts.CsrfView.as_view()),
    path('api/v1/auth/register/', accounts.RegisterView.as_view()),
    path('api/v1/auth/verify-email/', accounts.EmailVerificationView.as_view()),
    path('api/v1/auth/verify-email/resend/', accounts.EmailVerificationResendView.as_view()),
    path('api/v1/auth/login/', accounts.LoginView.as_view()),
    path('api/v1/auth/logout/', accounts.LogoutView.as_view()),
    path('api/v1/auth/me/', accounts.ProfileView.as_view()),
    path('api/v1/auth/password/', accounts.PasswordView.as_view()),
    path('api/v1/auth/password-reset/', accounts.PasswordResetRequestView.as_view()),
    path('api/v1/auth/password-reset/confirm/', accounts.PasswordResetConfirmView.as_view()),
    path('api/v1/tutor/chat/', TutorChatView.as_view()),
    path('api/v1/tutor/speech/', TutorSpeechView.as_view()),
    path('api/v1/tutor/transcribe/', TutorTranscribeView.as_view()),
    path('api/v1/me/learning/', education.LearningDashboardView.as_view()),
    path('api/v1/admin/analytics/', AdminLearningAnalyticsView.as_view()),
    path('api/v1/admin/users/', admin_accounts.AdminUserList.as_view()),
    path('api/v1/admin/users/<int:pk>/', admin_accounts.AdminUserDetail.as_view()),
    path('api/v1/admin/users/<int:pk>/learning-progress/', admin_accounts.AdminUserLearningProgress.as_view()),
    path('api/v1/tracks/', education.TrackList.as_view()),
    path('api/v1/tracks/<str:short_id>/', education.TrackDetail.as_view()),
    path('api/v1/courses/', education.CourseList.as_view()),
    path('api/v1/courses/<str:slug>/', education.CourseDetail.as_view()),
    path('api/v1/courses/<str:slug>/progress/', education.CourseProgressView.as_view()),
    path('api/v1/courses/<str:slug>/journey/', certificates.JourneyView.as_view()),
    path('api/v1/courses/<str:slug>/certificate/', certificates.IssueCertificateView.as_view()),
    path('api/v1/certificates/<uuid:token>/', certificates.CertificateView.as_view()),
    path('api/v1/certificates/<uuid:token>/download/', certificates.CertificateDownloadView.as_view()),
    path('api/v1/certificates/<uuid:token>/qr/', certificates.CertificateQRView.as_view()),
    path('api/v1/modules/', education.ModuleList.as_view()),
    path('api/v1/modules/<str:short_id>/', education.ModuleDetail.as_view()),
    path('api/v1/items/', education.LearningItemList.as_view()),
    path('api/v1/items/<str:short_id>/', education.LearningItemDetail.as_view()),
    path('api/v1/items/<str:short_id>/progress/', education.ItemProgressView.as_view()),
    path('api/v1/items/<str:short_id>/practice/', activities.ItemPracticeView.as_view()),
    path('api/v1/practice-types/', activities.PracticeCatalogView.as_view()),
    path('api/v1/items/<str:short_id>/practice/draft/', activities.PracticeDraftView.as_view()),
    path('api/v1/items/<str:short_id>/practice/start/', activities.PracticeStartView.as_view()),
    path('api/v1/items/<str:short_id>/practice/history/', activities.PracticeHistoryView.as_view()),
    path('api/v1/items/<str:short_id>/practice/workspace/', workspace.WorkspaceCreateView.as_view()),
    path('api/v1/workspace/documents/<int:pk>/', workspace.WorkspaceDocumentView.as_view()),
    path('api/v1/workspace/source-lectures/', workspace.SourceLecturesView.as_view()),
    path('api/v1/workspace/documents/<int:pk>/messages/', workspace.WorkspaceMessagesView.as_view()),
    path('api/v1/workspace/documents/<int:pk>/operations/', workspace.WorkspaceOperationsView.as_view()),
    path('api/v1/workspace/operations/<int:pk>/', workspace.WorkspaceOperationView.as_view()),
    path('api/v1/workspace/artifacts/<int:pk>/download/', workspace.WorkspaceArtifactView.as_view()),
    path('api/v1/lessons/', education.LessonList.as_view()),
    path('api/v1/lessons/<str:short_id>/', education.LessonDetail.as_view()),
    path('api/v1/lessons/<str:short_id>/blocks/', lessons.BlocksView.as_view()),
    path('api/v1/lessons/<str:short_id>/attachments/', lessons.AttachmentsView.as_view()),
    path('api/v1/lessons/<str:short_id>/draft/', lessons.LessonDraftView.as_view()),
    path('api/v1/lessons/<str:short_id>/imports/', lecture_imports.ImportListView.as_view()),
    path('api/v1/items/<str:short_id>/practice/imports/', lecture_imports.PracticeImportListView.as_view()),
    path('api/v1/items/<str:short_id>/practice/imports/<uuid:job_id>/', lecture_imports.PracticeImportDetailView.as_view()),
    path('api/v1/items/<str:short_id>/practice/imports/<uuid:job_id>/source/', lecture_imports.PracticeImportSourceView.as_view()),
    path('api/v1/lessons/<str:short_id>/imports/<uuid:job_id>/', lecture_imports.ImportDetailView.as_view()),
    path('api/v1/lessons/<str:short_id>/imports/<uuid:job_id>/source/', lecture_imports.ImportSourceView.as_view()),
    path('api/v1/lessons/<str:short_id>/test/', assessments.TestView.as_view()),
    path('api/v1/items/<str:short_id>/test/', assessments.ItemTestView.as_view()),
    path('api/v1/items/<str:short_id>/attempts/', assessments.ItemAttemptView.as_view()),
    path('api/v1/tests/<int:pk>/questions/', assessments.QuestionsView.as_view()),
    path('api/v1/lessons/<str:short_id>/attempts/', assessments.AttemptView.as_view()),
    path('api/v1/attempts/', assessments.AllAttemptsView.as_view()),
    path('api/v1/media/', mediafiles.MediaUploadView.as_view()),
    path('api/v1/media/files/', mediafiles.AttachmentUploadView.as_view()),
    path('api/v1/media/<int:pk>/', mediafiles.MediaView.as_view()),
    path('api/v1/media/<int:pk>/download/', mediafiles.MediaDownloadView.as_view()),
    path('api/v1/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/v1/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
]
