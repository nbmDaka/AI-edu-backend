import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.getenv('DEBUG', 'false').lower() == 'true'
SECRET_KEY = os.getenv('SECRET_KEY', 'local-development-only-change-me' if DEBUG else '')
if not SECRET_KEY or (not DEBUG and (SECRET_KEY.startswith('replace-with') or len(SECRET_KEY) < 32)):
    raise RuntimeError('A strong SECRET_KEY is required')
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,backend').split(',')
CSRF_TRUSTED_ORIGINS = [x for x in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',') if x]
CSRF_FAILURE_VIEW = 'common.exceptions.csrf_failure'
INSTALLED_APPS = [
    'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
    'rest_framework', 'drf_spectacular', 'storages',
    'apps.accounts', 'apps.education', 'apps.lessons', 'apps.assessments',
    'apps.mediafiles', 'apps.activities',
    'apps.tutor',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [],
              'APP_DIRS': True, 'OPTIONS': {'context_processors': [
                  'django.template.context_processors.debug', 'django.template.context_processors.request',
                  'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages']}}]
WSGI_APPLICATION = 'config.wsgi.application'
if os.getenv('DATABASE_HOST'):
    DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql', 'NAME': os.getenv('DATABASE_NAME', 'aiedu'),
        'USER': os.getenv('DATABASE_USER', 'aiedu'), 'PASSWORD': os.getenv('DATABASE_PASSWORD', ''),
        'HOST': os.getenv('DATABASE_HOST'), 'PORT': os.getenv('DATABASE_PORT', '5432')}}
else:
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3'}}
if os.getenv('REDIS_URL'):
    CACHES = {'default': {'BACKEND': 'django_redis.cache.RedisCache', 'LOCATION': os.getenv('REDIS_URL'),
                          'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient'}}}
else:
    CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
AUTH_USER_MODEL = 'accounts.User'
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework.authentication.SessionAuthentication'],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
    'DEFAULT_PAGINATION_CLASS': 'common.pagination.StandardPagination', 'PAGE_SIZE': 30,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'EXCEPTION_HANDLER': 'common.exceptions.handler',
}
SPECTACULAR_SETTINGS = {'TITLE': 'AI Edu API', 'VERSION': '1.0.0'}
LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = 'UTC'
USE_TZ = True
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_ROOT = BASE_DIR / 'media'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = False
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
DATA_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024
MAX_IMAGE_BYTES = int(os.getenv('MAX_IMAGE_BYTES', str(10 * 1024 * 1024)))
AI_SERVICE_URL = os.getenv('AI_SERVICE_URL', '').rstrip('/')
AI_SERVICE_API_KEY = os.getenv('AI_SERVICE_API_KEY', '')
AI_SERVICE_TIMEOUT = int(os.getenv('AI_SERVICE_TIMEOUT', '90'))
AI_TUTOR_RATE = os.getenv('AI_TUTOR_RATE', '20/min')
if os.getenv('S3_ENDPOINT_URL'):
    STORAGES = {'default': {'BACKEND': 'storages.backends.s3.S3Storage', 'OPTIONS': {
        'bucket_name': os.getenv('S3_BUCKET', 'aiedu'), 'endpoint_url': os.getenv('S3_ENDPOINT_URL'),
        'access_key': os.getenv('S3_ACCESS_KEY'), 'secret_key': os.getenv('S3_SECRET_KEY'),
        'default_acl': None, 'querystring_auth': True, 'file_overwrite': False}},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
