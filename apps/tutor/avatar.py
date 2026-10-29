import time
from urllib.parse import urlsplit

from django.conf import settings
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


def private_gateway_url():
    value = settings.AI_AVATAR_SERVICE_URL
    try:
        parsed = urlsplit(value)
        valid_port = parsed.port == 5185
    except ValueError:
        return None
    if (parsed.scheme != 'http' or parsed.hostname != 'aiedu-avatar-gateway' or not valid_port
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
        return None
    return value.rstrip('/')


def avatar_allowed(user):
    if not user.is_authenticated or not settings.AI_AVATAR_ENABLED or settings.AI_AVATAR_EXPIRES_AT <= time.time():
        return False
    allowed = settings.AI_AVATAR_ALLOWED_USERS
    return bool(settings.AI_SERVICE_API_KEY and private_gateway_url() and
                ('*' in allowed or str(user.pk) in allowed or getattr(user, 'email', '').lower() in allowed))


def model_configured():
    key = settings.AI_AVATAR_MODEL_KEY
    version = settings.AI_AVATAR_MODEL_VERSION
    return (key.startswith('platform-assets/avatar/') and '..' not in key and key.endswith('.glb')
            and len(version) == 64 and all(char in '0123456789abcdef' for char in version))


class EmptyInput(serializers.Serializer):
    pass


class TutorAvatarConfigView(GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = EmptyInput

    @extend_schema(responses={200: dict})
    def get(self, request):
        enabled = avatar_allowed(request.user) and model_configured()
        if enabled:
            try:
                enabled = default_storage.exists(settings.AI_AVATAR_MODEL_KEY)
            except OSError:
                enabled = False
        data = {'enabled': bool(enabled)}
        if enabled:
            data.update({'model_url': '/api/v1/tutor/avatar/model/?v=' + settings.AI_AVATAR_MODEL_VERSION,
                         'expires_at': settings.AI_AVATAR_EXPIRES_AT,
                         'engine': 'nvidia-audio2face-sdk-mark-2.3', 'voice': 'cedar'})
        response = Response(data)
        response['Cache-Control'] = 'private, no-store'
        return response


class TutorAvatarModelView(GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = EmptyInput

    @extend_schema(responses={(200, 'model/gltf-binary'): bytes})
    def get(self, request):
        if not avatar_allowed(request.user) or not model_configured() or request.query_params.get('v') != settings.AI_AVATAR_MODEL_VERSION:
            raise Http404
        key = settings.AI_AVATAR_MODEL_KEY
        try:
            size = default_storage.size(key)
            if not 100000 < size <= 15 * 1024 * 1024:
                raise Http404
            source = default_storage.open(key, 'rb')
        except (FileNotFoundError, OSError):
            raise Http404 from None
        response = FileResponse(source, content_type='model/gltf-binary', filename='teacher.glb')
        response['Content-Length'] = str(size)
        response['Cache-Control'] = 'private, max-age=3600, immutable'
        response['ETag'] = '"' + settings.AI_AVATAR_MODEL_VERSION + '"'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
