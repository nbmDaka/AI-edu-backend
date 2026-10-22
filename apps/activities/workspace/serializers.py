from rest_framework import serializers
from apps.activities.models import WorkspaceDocument, WorkspaceMessage, WorkspaceOperation
from apps.activities.serializers import StrictSerializer


class DocumentCreate(StrictSerializer):
    parameters = serializers.JSONField(required=False)
    language = serializers.ChoiceField(choices=['ru', 'kk', 'en'], default='ru')


class DocumentUpdate(StrictSerializer):
    revision = serializers.IntegerField(min_value=1)
    parameters = serializers.JSONField(required=False)
    content = serializers.JSONField(required=False)

    def validate(self, attrs):
        if not {'parameters', 'content'} & set(attrs):
            raise serializers.ValidationError('Укажите parameters или content')
        return attrs


class OperationCreate(StrictSerializer):
    request_id = serializers.UUIDField()
    revision = serializers.IntegerField(min_value=1)
    kind = serializers.ChoiceField(choices=WorkspaceOperation.Kind.choices)
    prompt = serializers.CharField(max_length=8000)
    source_lecture_id = serializers.IntegerField(min_value=1, required=False)


class SourceLectureOutput(serializers.ModelSerializer):
    title = serializers.CharField(read_only=True)

    class Meta:
        model = WorkspaceDocument
        fields = ['id', 'title', 'updated_at']


class DocumentOutput(serializers.ModelSerializer):
    class Meta:
        model = WorkspaceDocument
        fields = ['id', 'submission', 'document_type', 'schema_version', 'parameters', 'content', 'conversation_state',
                  'revision', 'created_at', 'updated_at']
        read_only_fields = fields


class MessageOutput(serializers.ModelSerializer):
    content = serializers.SerializerMethodField()
    choices = serializers.SerializerMethodField()
    artifact = serializers.SerializerMethodField()
    offer_source_lecture = serializers.SerializerMethodField()

    def get_offer_source_lecture(self, message) -> bool:
        return message.role == 'assistant' and message.operation.result.get('offer_source_lecture', False)

    def get_choices(self, message) -> list[str]:
        return message.operation.result.get('choices', []) if message.role == 'assistant' else []

    def get_artifact(self, message) -> dict | None:
        return message.operation.result.get('artifact') if message.role == 'assistant' else None

    def get_content(self, message) -> dict | None:
        if message.role == 'assistant' and message.operation.kind in ('generate', 'chat'):
            return message.operation.result.get('content')
        return None

    class Meta:
        model = WorkspaceMessage
        fields = ['id', 'operation', 'role', 'text', 'content', 'choices', 'artifact', 'offer_source_lecture', 'created_at']
        read_only_fields = fields


class OperationOutput(serializers.ModelSerializer):
    class Meta:
        model = WorkspaceOperation
        fields = ['id', 'document', 'request_id', 'kind', 'status', 'base_revision', 'result', 'error_code',
                  'model', 'duration_ms', 'input_tokens', 'output_tokens', 'total_tokens', 'cost', 'cost_currency',
                  'attempts', 'created_at', 'started_at', 'finished_at']
        read_only_fields = fields
