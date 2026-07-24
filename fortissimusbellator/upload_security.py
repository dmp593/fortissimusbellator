"""Validation and bounded storage helpers for staff upload endpoints."""

import ipaddress
import logging
import shutil
import socket
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import UUID, uuid4

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image, UnidentifiedImageError


logger = logging.getLogger(__name__)

ALLOWED_IMAGE_FORMATS = {
    "GIF": ".gif",
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
}
CHUNK_ROOT = "uploads/.chunks"


class UploadRejected(ValueError):
    """An upload failed a user-correctable validation rule."""


class RemoteImageUnavailable(RuntimeError):
    """A validated remote image could not be retrieved safely."""


def clean_filename(value):
    """Return a storage-safe basename while preserving its useful extension."""
    if not isinstance(value, str):
        raise UploadRejected("A filename is required.")
    filename = Path(value.strip()).name
    if filename in {"", ".", ".."} or any(ord(char) < 32 for char in filename):
        raise UploadRejected("The filename is invalid.")
    return filename[:180]


def clean_upload_id(value):
    try:
        return UUID(str(value)).hex
    except (TypeError, ValueError, AttributeError) as exc:
        raise UploadRejected("The upload identifier is invalid.") from exc


def clean_chunk_numbers(chunk_index, total_chunks):
    try:
        index = int(chunk_index)
        total = int(total_chunks)
    except (TypeError, ValueError) as exc:
        raise UploadRejected("The chunk information is invalid.") from exc

    max_chunks = (
        settings.UPLOAD_MAX_FILE_BYTES + settings.UPLOAD_MAX_CHUNK_BYTES - 1
    ) // settings.UPLOAD_MAX_CHUNK_BYTES
    if total < 1 or total > max_chunks or index < 0 or index >= total:
        raise UploadRejected("The chunk information is outside the allowed range.")
    return index, total


def validate_chunk(file_chunk):
    if file_chunk is None:
        raise UploadRejected("A file chunk is required.")
    if file_chunk.size < 1:
        raise UploadRejected("The file chunk is empty.")
    if file_chunk.size > settings.UPLOAD_MAX_CHUNK_BYTES:
        raise UploadRejected("The file chunk is too large.")


def store_chunk(upload_id, chunk_index, file_chunk):
    path = _chunk_path(upload_id, chunk_index)
    if default_storage.exists(path):
        default_storage.delete(path)
    default_storage.save(path, file_chunk)


def assemble_chunks(upload_id, filename, total_chunks):
    """Stream stored chunks into their final file without buffering it in RAM."""
    final_path = f"uploads/{upload_id}/{filename}"
    if default_storage.exists(final_path):
        default_storage.delete(final_path)

    size = 0
    try:
        created_path = default_storage.save(final_path, ContentFile(b""))
        if created_path != final_path:
            default_storage.delete(created_path)
            raise UploadRejected("The destination file already exists.")
        with default_storage.open(final_path, "wb") as destination:
            for index in range(total_chunks):
                chunk_path = _chunk_path(upload_id, index)
                if not default_storage.exists(chunk_path):
                    raise UploadRejected("One or more file chunks are missing.")
                with default_storage.open(chunk_path, "rb") as source:
                    while chunk := source.read(1024 * 1024):
                        size += len(chunk)
                        if size > settings.UPLOAD_MAX_FILE_BYTES:
                            raise UploadRejected("The complete file is too large.")
                        destination.write(chunk)
    except Exception:
        if default_storage.exists(final_path):
            default_storage.delete(final_path)
        raise

    for index in range(total_chunks):
        default_storage.delete(_chunk_path(upload_id, index))
    _remove_empty_chunk_directory(upload_id)
    return final_path


def cleanup_stale_chunks():
    """Opportunistically remove abandoned local chunk directories."""
    try:
        root = Path(default_storage.path(CHUNK_ROOT))
    except (AttributeError, NotImplementedError):
        return
    if not root.is_dir():
        return

    cutoff = time.time() - settings.UPLOAD_CHUNK_MAX_AGE_SECONDS
    for directory in root.iterdir():
        try:
            if directory.is_dir() and directory.stat().st_mtime < cutoff:
                shutil.rmtree(directory)
        except OSError:
            logger.warning("Could not clean stale upload chunks at %s", directory)


def save_uploaded_image(uploaded_file):
    if uploaded_file is None:
        raise UploadRejected("An image is required.")
    if uploaded_file.size > settings.EDITOR_IMAGE_MAX_BYTES:
        raise UploadRejected("The image is too large.")

    content = uploaded_file.read(settings.EDITOR_IMAGE_MAX_BYTES + 1)
    extension = validate_image(content)
    return _save_editor_image(content, extension)


def fetch_remote_image(url):
    """Download one public image with redirect, memory, and SSRF limits."""
    current_url = url
    for _redirect in range(settings.EDITOR_REMOTE_MAX_REDIRECTS + 1):
        validate_public_url(current_url)
        response = _request_remote_image(current_url)
        with response:
            redirect_url = _remote_redirect_url(response, current_url)
            if redirect_url:
                current_url = redirect_url
                continue
            _validate_remote_response(response)
            content = _read_remote_content(response)

        extension = validate_image(content)
        return _save_editor_image(content, extension)

    raise RemoteImageUnavailable("The remote image redirected too many times.")


def _request_remote_image(url):
    try:
        return requests.get(
            url,
            stream=True,
            allow_redirects=False,
            timeout=(5, settings.EDITOR_REMOTE_READ_TIMEOUT),
            headers={"User-Agent": "FortissimusBellator/1.0"},
        )
    except requests.RequestException as exc:
        raise RemoteImageUnavailable(
            "The remote image could not be reached."
        ) from exc


def _remote_redirect_url(response, current_url):
    if not 300 <= response.status_code < 400:
        return None
    location = response.headers.get("Location")
    if not location:
        raise RemoteImageUnavailable("The remote redirect is invalid.")
    return urljoin(current_url, location)


def _validate_remote_response(response):
    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RemoteImageUnavailable(
            "The remote server rejected the image request."
        ) from exc

    content_length = response.headers.get("Content-Length")
    try:
        announced_size = int(content_length) if content_length else 0
    except ValueError:
        announced_size = 0
    if announced_size > settings.EDITOR_IMAGE_MAX_BYTES:
        raise UploadRejected("The remote image is too large.")


def _read_remote_content(response):
    content = bytearray()
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        content.extend(chunk)
        if len(content) > settings.EDITOR_IMAGE_MAX_BYTES:
            raise UploadRejected("The remote image is too large.")
    return bytes(content)


def validate_public_url(url):
    if not isinstance(url, str):
        raise UploadRejected("A valid image URL is required.")
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UploadRejected("Only public HTTP and HTTPS URLs are allowed.")
    if parsed.username or parsed.password:
        raise UploadRejected("Credentials are not allowed in image URLs.")

    try:
        addresses = socket.getaddrinfo(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise RemoteImageUnavailable("The image hostname could not be resolved.") from exc

    if not addresses:
        raise RemoteImageUnavailable("The image hostname has no address.")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise UploadRejected("Private and internal image addresses are blocked.")


def validate_image(content):
    if not content:
        raise UploadRejected("The image is empty.")
    try:
        with Image.open(BytesIO(content)) as image:
            if image.width * image.height > settings.EDITOR_IMAGE_MAX_PIXELS:
                raise UploadRejected("The image dimensions are too large.")
            image.verify()
            image_format = image.format
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise UploadRejected("The uploaded file is not a supported image.") from exc
    if image_format not in ALLOWED_IMAGE_FORMATS:
        raise UploadRejected("The image format is not supported.")
    return ALLOWED_IMAGE_FORMATS[image_format]


def _save_editor_image(content, extension):
    path = f"uploads/blog/{uuid4().hex}{extension}"
    return default_storage.save(path, ContentFile(content))


def _chunk_path(upload_id, index):
    return f"{CHUNK_ROOT}/{upload_id}/{index:05d}.part"


def _remove_empty_chunk_directory(upload_id):
    try:
        directory = Path(default_storage.path(f"{CHUNK_ROOT}/{upload_id}"))
        directory.rmdir()
    except (AttributeError, NotImplementedError, OSError):
        pass
