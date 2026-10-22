from drf_spectacular.utils import extend_schema
from drf_spectacular.types import OpenApiTypes
from rest_framework import generics
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from django.http import HttpResponse
from django.utils.http import content_disposition_header
from apps.activities.models import WorkspaceDocument, WorkspaceOperation, WorkspaceArtifact
from apps.activities.views import practice_item
from .serializers import (DocumentCreate, DocumentUpdate, DocumentOutput, MessageOutput,
                          OperationCreate, OperationOutput)
from .services import create_document, update_document, create_operation


def owned_document(request, pk):
    document = WorkspaceDocument.objects.select_related('submission__item').filter(pk=pk, owner=request.user).first()
    if not document:
        raise NotFound()
    practice_item(request, document.submission.item.short_id)
    return document


class WorkspaceCreateView(generics.GenericAPIView):
    serializer_class = DocumentCreate

    @extend_schema(responses={200: DocumentOutput, 201: DocumentOutput})
    def post(self, request, short_id):
        item = practice_item(request, short_id)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document, created = create_document(request.user, item, serializer.validated_data.get('parameters'),
                                            serializer.validated_data['language'])
        return Response(DocumentOutput(document).data, status=201 if created else 200)


class WorkspaceDocumentView(generics.GenericAPIView):
    serializer_class = DocumentUpdate

    @extend_schema(responses=DocumentOutput)
    def get(self, request, pk):
        return Response(DocumentOutput(owned_document(request, pk)).data)

    @extend_schema(responses=DocumentOutput)
    def patch(self, request, pk):
        document = owned_document(request, pk)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(DocumentOutput(update_document(request.user, document, serializer.validated_data)).data)


class WorkspaceMessagesView(generics.GenericAPIView):
    serializer_class = MessageOutput

    @extend_schema(responses=MessageOutput(many=True))
    def get(self, request, pk):
        document = owned_document(request, pk)
        page = self.paginate_queryset(document.messages.select_related('operation').all())
        return self.get_paginated_response(MessageOutput(page, many=True).data)


class WorkspaceOperationsView(generics.GenericAPIView):
    serializer_class = OperationCreate

    @extend_schema(responses={202: OperationOutput})
    def post(self, request, pk):
        document = owned_document(request, pk)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(OperationOutput(create_operation(request.user, document, serializer.validated_data)).data, status=202)


class WorkspaceOperationView(generics.GenericAPIView):
    serializer_class = OperationOutput

    def get(self, request, pk):
        operation = WorkspaceOperation.objects.filter(pk=pk, document__owner=request.user).first()
        if not operation:
            raise NotFound()
        owned_document(request, operation.document_id)
        return Response(self.get_serializer(operation).data)


class WorkspaceArtifactView(generics.GenericAPIView):
    serializer_class = DocumentOutput

    @extend_schema(responses={(200, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'): OpenApiTypes.BINARY})
    def get(self, request, pk):
        artifact = WorkspaceArtifact.objects.filter(pk=pk, document__owner=request.user).first()
        if artifact is None:
            raise NotFound()
        owned_document(request, artifact.document_id)
        from .docx_export import DOCX_MIME
        response = HttpResponse(bytes(artifact.data), content_type=DOCX_MIME)
        response['Content-Disposition'] = content_disposition_header(True, artifact.name)
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
