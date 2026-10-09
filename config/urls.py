from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from apps.accounts import views as accounts
from apps.education import views as education
from apps.lessons import views as lessons
from apps.assessments import views as assessments
from apps.mediafiles import views as mediafiles

urlpatterns = [
    path('django-admin/', admin.site.urls),
    path('api/v1/csrf/', accounts.CsrfView.as_view()),
    path('api/v1/auth/register/', accounts.RegisterView.as_view()),
    path('api/v1/auth/login/', accounts.LoginView.as_view()),
    path('api/v1/auth/logout/', accounts.LogoutView.as_view()),
    path('api/v1/auth/me/', accounts.ProfileView.as_view()),
    path('api/v1/auth/password/', accounts.PasswordView.as_view()),
    path('api/v1/tracks/', education.TrackList.as_view()),
    path('api/v1/tracks/<str:short_id>/', education.TrackDetail.as_view()),
    path('api/v1/courses/', education.CourseList.as_view()),
    path('api/v1/courses/<str:slug>/', education.CourseDetail.as_view()),
    path('api/v1/courses/<str:slug>/progress/', education.CourseProgressView.as_view()),
    path('api/v1/modules/', education.ModuleList.as_view()),
    path('api/v1/modules/<str:short_id>/', education.ModuleDetail.as_view()),
    path('api/v1/items/', education.LearningItemList.as_view()),
    path('api/v1/items/<str:short_id>/', education.LearningItemDetail.as_view()),
    path('api/v1/items/<str:short_id>/progress/', education.ItemProgressView.as_view()),
    path('api/v1/items/<str:short_id>/practice/', education.ItemPracticeView.as_view()),
    path('api/v1/lessons/', education.LessonList.as_view()),
    path('api/v1/lessons/<str:short_id>/', education.LessonDetail.as_view()),
    path('api/v1/lessons/<str:short_id>/blocks/', lessons.BlocksView.as_view()),
    path('api/v1/lessons/<str:short_id>/draft/', lessons.LessonDraftView.as_view()),
    path('api/v1/lessons/<str:short_id>/test/', assessments.TestView.as_view()),
    path('api/v1/items/<str:short_id>/test/', assessments.ItemTestView.as_view()),
    path('api/v1/items/<str:short_id>/attempts/', assessments.ItemAttemptView.as_view()),
    path('api/v1/tests/<int:pk>/questions/', assessments.QuestionsView.as_view()),
    path('api/v1/lessons/<str:short_id>/attempts/', assessments.AttemptView.as_view()),
    path('api/v1/attempts/', assessments.AllAttemptsView.as_view()),
    path('api/v1/media/', mediafiles.MediaUploadView.as_view()),
    path('api/v1/media/<int:pk>/', mediafiles.MediaView.as_view()),
    path('api/v1/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/v1/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
]
