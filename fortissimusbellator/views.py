"""Private upload endpoints used by Django admin widgets."""

from django.contrib.admin.views.decorators import staff_member_required
from django.core.files.storage import default_storage
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View

from .upload_security import (
    RemoteImageUnavailable,
    UploadRejected,
    assemble_chunks,
    clean_chunk_numbers,
    clean_filename,
    clean_upload_id,
    cleanup_stale_chunks,
    fetch_remote_image,
    save_uploaded_image,
    store_chunk,
    validate_chunk,
)


class StaffUploadView(View):
    """Require an authenticated staff account for every upload action."""

    @method_decorator(staff_member_required)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    @staticmethod
    def rejected(error, status=400):
        return JsonResponse({"error": str(error)}, status=status)


class FileUploadView(StaffUploadView):
    """Receive bounded chunks and publish one temporary assembled file."""

    def post(self, request, *args, **kwargs):
        try:
            cleanup_stale_chunks()
            upload_id = clean_upload_id(request.POST.get("fileId"))
            filename = clean_filename(
                request.POST.get("fileName", request.POST.get("filename"))
            )
            chunk_index, total_chunks = clean_chunk_numbers(
                request.POST.get("chunkIndex"),
                request.POST.get("totalChunks"),
            )
            file_chunk = request.FILES.get("file")
            validate_chunk(file_chunk)
            store_chunk(upload_id, chunk_index, file_chunk)

            if chunk_index != total_chunks - 1:
                return JsonResponse({"status": "ok"})

            path = assemble_chunks(upload_id, filename, total_chunks)
            return JsonResponse({
                "status": "ok",
                "url": default_storage.url(path),
            })
        except UploadRejected as exc:
            return self.rejected(exc)


class EditorJsImageUploadByFileView(StaffUploadView):
    def post(self, request, *args, **kwargs):
        try:
            path = save_uploaded_image(request.FILES.get("image"))
        except UploadRejected as exc:
            return JsonResponse({"success": 0, "error": str(exc)}, status=400)
        return _editor_success(path)


class EditorJsImageUploadByUrlView(StaffUploadView):
    def post(self, request, *args, **kwargs):
        try:
            path = fetch_remote_image(request.POST.get("url"))
        except UploadRejected as exc:
            return JsonResponse({"success": 0, "error": str(exc)}, status=400)
        except RemoteImageUnavailable as exc:
            return JsonResponse({"success": 0, "error": str(exc)}, status=502)
        return _editor_success(path)


def _editor_success(path):
    return JsonResponse({
        "success": 1,
        "file": {"url": default_storage.url(path)},
    })
