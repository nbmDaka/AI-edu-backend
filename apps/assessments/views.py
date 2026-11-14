from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from drf_spectacular.utils import extend_schema
from common.permissions import is_admin
from apps.education.access import can_access_test
from apps.education.models import Lesson, LearningItem
from .models import Test, TestAttempt
from .serializers import TestAdminSerializer, TestStudentSerializer, QuestionAdminSerializer, QuestionStudentSerializer, AttemptSerializer, AttemptInput
from .services import save_test, save_item_test, submit_attempt

class TestView(GenericAPIView):
    serializer_class = TestAdminSerializer
    def get(self, request, short_id):
        lesson = Lesson.objects.filter(short_id=short_id).first()
        if not lesson: raise NotFound()
        test = Test.objects.prefetch_related('questions__options').filter(lesson=lesson).first()
        if not test: raise NotFound()
        if is_admin(request.user):
            return Response(TestAdminSerializer(test).data)
        if not test.is_published or not can_access_test(request.user, test):
            raise PermissionDenied()
        return Response(TestStudentSerializer(test).data)

    def put(self, request, short_id):
        if not is_admin(request.user): raise PermissionDenied()
        lesson = Lesson.objects.filter(short_id=short_id).first()
        if not lesson: raise NotFound()
        test = save_test(lesson, request.data)
        return Response(TestAdminSerializer(Test.objects.prefetch_related('questions__options').get(pk=test.pk)).data)

class QuestionsView(GenericAPIView):
    serializer_class = QuestionAdminSerializer
    def get(self, request, pk):
        test = Test.objects.select_related('lesson__module__course__learning_track').prefetch_related('questions__options').filter(pk=pk).first()
        if not test: raise NotFound()
        if is_admin(request.user): return Response(QuestionAdminSerializer(test.questions.all(), many=True).data)
        if not test.is_published or not can_access_test(request.user, test):
            raise PermissionDenied()
        return Response(QuestionStudentSerializer(test.questions.all(), many=True).data)

    def put(self, request, pk):
        if not is_admin(request.user): raise PermissionDenied()
        test = Test.objects.filter(pk=pk).first()
        if not test: raise NotFound()
        save = (lambda data: save_item_test(test.learning_item, data)) if hasattr(test, 'learning_item') else (lambda data: save_test(test.lesson, data))
        updated = save({'title': test.title, 'description': test.description,
            'passing_percent': test.passing_percent, 'max_attempts': test.max_attempts,
            'is_published': test.is_published, 'questions': request.data})
        return Response(QuestionAdminSerializer(updated.questions.prefetch_related('options'), many=True).data)

class AttemptView(GenericAPIView):
    serializer_class = AttemptSerializer
    def get(self, request, short_id):
        test = Test.objects.select_related('lesson__module__course__learning_track').filter(lesson__short_id=short_id, lesson__deleted_at__isnull=True).first()
        if not test: raise NotFound()
        if not is_admin(request.user) and (not test.is_published or not can_access_test(request.user, test)):
            raise NotFound()
        q = TestAttempt.objects.select_related('test__learning_item__module__course', 'test__lesson__module__course').filter(test=test)
        if not is_admin(request.user): q = q.filter(user=request.user)
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(q, request)
        return paginator.get_paginated_response(AttemptSerializer(page, many=True, context={'request': request}).data)

    @extend_schema(request=AttemptInput)
    def post(self, request, short_id):
        test = Test.objects.filter(lesson__short_id=short_id, lesson__deleted_at__isnull=True).first()
        if not test: raise NotFound()
        if not isinstance(request.data, dict): raise ValidationError({'answers': 'Ожидается объект'})
        attempt = submit_attempt(test.id, request.user, request.data.get('answers'), request.data.get('request_id'))
        return Response(AttemptSerializer(attempt, context={'request': request}).data, status=201)

class AllAttemptsView(GenericAPIView):
    serializer_class = AttemptSerializer
    def get(self, request):
        q = TestAttempt.objects.select_related('test__learning_item__module__course', 'test__lesson__module__course')
        if not is_admin(request.user):
            q = q.filter(user=request.user)
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(q, request)
        return paginator.get_paginated_response(AttemptSerializer(page, many=True, context={'request': request}).data)


class ItemTestView(GenericAPIView):
    serializer_class = TestAdminSerializer

    def get_item(self, short_id):
        item = LearningItem.objects.select_related('test', 'module__course__learning_track').filter(short_id=short_id, type='TEST').first()
        if not item or not item.test_id:
            raise NotFound()
        return item

    def get(self, request, short_id):
        item = self.get_item(short_id)
        test = Test.objects.prefetch_related('questions__options').get(pk=item.test_id)
        if is_admin(request.user):
            return Response(TestAdminSerializer(test).data)
        if not test.is_published or not can_access_test(request.user, test):
            raise NotFound()
        return Response(TestStudentSerializer(test).data)

    def put(self, request, short_id):
        if not is_admin(request.user):
            raise PermissionDenied()
        item = self.get_item(short_id)
        test = save_item_test(item, request.data)
        return Response(TestAdminSerializer(Test.objects.prefetch_related('questions__options').get(pk=test.pk)).data)


class ItemAttemptView(GenericAPIView):
    serializer_class = AttemptSerializer

    def get_test(self, request, short_id):
        test = Test.objects.select_related('learning_item__module__course__learning_track').filter(
            learning_item__short_id=short_id, learning_item__type='TEST').first()
        if not test or (not is_admin(request.user) and (not test.is_published or not can_access_test(request.user, test))):
            raise NotFound()
        return test

    def get(self, request, short_id):
        test = self.get_test(request, short_id)
        q = TestAttempt.objects.select_related('test__learning_item__module__course', 'test__lesson__module__course').filter(test=test)
        if not is_admin(request.user):
            q = q.filter(user=request.user)
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(q, request)
        return paginator.get_paginated_response(AttemptSerializer(page, many=True, context={'request': request}).data)

    @extend_schema(request=AttemptInput)
    def post(self, request, short_id):
        test = self.get_test(request, short_id)
        if not isinstance(request.data, dict):
            raise ValidationError({'answers': 'Ожидается объект'})
        attempt = submit_attempt(test.id, request.user, request.data.get('answers'), request.data.get('request_id'))
        return Response(AttemptSerializer(attempt, context={'request': request}).data, status=201)
