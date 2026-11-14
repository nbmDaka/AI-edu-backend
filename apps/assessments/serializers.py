from rest_framework import serializers
from .models import Test, Question, Option, TestAttempt


class AttemptInput(serializers.Serializer):
    answers = serializers.ListField(child=serializers.DictField())
    request_id = serializers.UUIDField(required=False)

class OptionAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = Option
        fields = ['id', 'text', 'is_correct', 'position']

class QuestionAdminSerializer(serializers.ModelSerializer):
    options = OptionAdminSerializer(many=True)
    class Meta:
        model = Question
        fields = ['id', 'text', 'position', 'points', 'competency', 'options']

class TestAdminSerializer(serializers.ModelSerializer):
    questions = QuestionAdminSerializer(many=True)
    class Meta:
        model = Test
        fields = ['id', 'lesson', 'title', 'description', 'passing_percent', 'max_attempts', 'is_published', 'version', 'questions']

class OptionStudentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Option
        fields = ['id', 'text', 'position']

class QuestionStudentSerializer(serializers.ModelSerializer):
    options = OptionStudentSerializer(many=True)
    class Meta:
        model = Question
        fields = ['id', 'text', 'position', 'points', 'options']

class TestStudentSerializer(serializers.ModelSerializer):
    questions = QuestionStudentSerializer(many=True)
    class Meta:
        model = Test
        fields = ['id', 'lesson', 'title', 'description', 'passing_percent', 'max_attempts', 'version', 'questions']

class AttemptSerializer(serializers.ModelSerializer):
    adaptive_result = serializers.SerializerMethodField()

    def get_adaptive_result(self, obj) -> dict | None:
        from apps.education.recommendations import resolve_recommendations
        result = obj.adaptive_result
        if not result or 'recommendations' not in result:
            return result
        request = self.context.get('request')
        return {**result, 'recommendations': resolve_recommendations(result['recommendations'], request.user if request else None)}

    test_title = serializers.CharField(source='test.title', read_only=True)
    course_title = serializers.SerializerMethodField()
    def get_course_title(self, obj) -> str | None:
        item = getattr(obj.test, 'learning_item', None)
        if item:
            return item.module.course.title
        return obj.test.lesson.module.course.title if obj.test.lesson_id else None
    class Meta:
        model = TestAttempt
        fields = ['id', 'test', 'test_title', 'course_title', 'test_version', 'answers', 'snapshot', 'earned_points', 'total_points', 'percent', 'passed', 'adaptive_result', 'completed_at']
