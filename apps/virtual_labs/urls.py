"""Маршруты модуля. Подключаются одной строкой в config/urls.py основной платформы:

    path('api/v1/virtual-labs/', include('apps.virtual_labs.urls')),
"""
from django.urls import path

from .views import admin, learner, lti, services, tutor

app_name = 'virtual_labs'

urlpatterns = [
    # слушатель
    path('config/', learner.ConfigView.as_view()),
    path('state/', learner.StateView.as_view()),
    path('profile/', learner.ProfileView.as_view()),
    path('settings/', learner.SettingsView.as_view()),
    path('sessions/<str:code>/', learner.SessionView.as_view()),
    path('sessions/<str:code>/recommendations/', learner.SessionRecommendationsView.as_view()),
    path('portfolio/', learner.PortfolioView.as_view()),
    path('portfolio/<str:client_id>/', learner.PortfolioItemView.as_view()),
    path('events/', learner.EventsView.as_view()),
    path('evaluate/', learner.EvaluateView.as_view()),
    path('prompt/analyze/', learner.PromptAnalyzeView.as_view()),
    path('analytics/', learner.AnalyticsView.as_view()),
    path('next/', learner.NextWorkView.as_view()),
    path('groups/', learner.MyGroupsView.as_view()),
    path('groups/join/', learner.JoinGroupView.as_view()),
    path('assignments/', learner.MyAssignmentsView.as_view()),
    path('reviews/', learner.MyReviewsView.as_view()),
    # сервисы
    path('ai/generate/', services.GenerateView.as_view()),
    path('ai/tts/', services.TTSView.as_view()),
    path('literature/', services.LiteratureView.as_view()),
    path('files/', services.FilesView.as_view()),
    path('files/<int:pk>/', services.FileDetailView.as_view()),
    path('files/<int:pk>/download/', services.FileDownloadView.as_view()),
    path('export/', services.ExportView.as_view()),
    # тьютор
    path('tutor/groups/', tutor.GroupsView.as_view()),
    path('tutor/groups/<int:pk>/', tutor.GroupDetailView.as_view()),
    path('tutor/groups/<int:pk>/members/', tutor.GroupMembersView.as_view()),
    path('tutor/groups/<int:pk>/members/<int:user_id>/', tutor.GroupMemberDetailView.as_view()),
    path('tutor/assignments/', tutor.AssignmentsView.as_view()),
    path('tutor/assignments/<int:pk>/', tutor.AssignmentDetailView.as_view()),
    path('tutor/overview/', tutor.OverviewView.as_view()),
    path('tutor/sessions/', tutor.SessionsView.as_view()),
    path('tutor/sessions/<int:pk>/', tutor.SessionDetailView.as_view()),
    path('tutor/sessions/<int:pk>/reviews/', tutor.ReviewsView.as_view()),
    path('tutor/users/', tutor.UserSearchView.as_view()),
    # администратор
    path('admin/users/', admin.UsersView.as_view()),
    path('admin/users/<int:pk>/', admin.UserDetailView.as_view()),
    path('admin/courses/', admin.CoursesView.as_view()),
    path('admin/courses/<int:pk>/', admin.CourseDetailView.as_view()),
    path('admin/works/', admin.WorksView.as_view()),
    path('admin/works/<str:code>/', admin.WorkDetailView.as_view()),
    path('admin/criteria/', admin.CriteriaView.as_view()),
    path('admin/criteria/<str:key>/', admin.CriterionDetailView.as_view()),
    path('admin/prompt-templates/', admin.PromptTemplatesView.as_view()),
    path('admin/prompt-templates/<str:code>/', admin.PromptTemplateDetailView.as_view()),
    path('admin/ai-providers/', admin.ProvidersView.as_view()),
    path('admin/ai-providers/<int:pk>/', admin.ProviderDetailView.as_view()),
    path('admin/ai-providers/<int:pk>/test/', admin.ProviderTestView.as_view()),
    path('admin/ai-log/', admin.AILogView.as_view()),
    path('admin/backups/', admin.BackupsView.as_view()),
    path('admin/lti/platforms/', admin.LtiPlatformsView.as_view()),
    path('admin/lti/platforms/<int:pk>/', admin.LtiPlatformDetailView.as_view()),
    # LTI 1.3
    path('lti/login/', lti.lti_login, name='lti-login'),
    path('lti/launch/', lti.lti_launch, name='lti-launch'),
    path('lti/jwks/', lti.lti_jwks, name='lti-jwks'),
]
