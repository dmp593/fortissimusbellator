"""Validated runtime description of a downloadable GGUF model."""

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


MODEL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
REPOSITORY_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$"
)
REVISION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ChatModelSpec:
    """Immutable model data consumed by the download and inference adapter."""

    model_id: str
    name: str
    repository: str
    filename: str
    revision: str = "main"
    sha256: str = ""
    download_size: int = 0
    summary: str = ""
    recommended: bool = False

    @property
    def path(self):
        return Path(settings.CHAT_MODEL_DIR).expanduser() / self.filename

    @property
    def download_url(self):
        repository = quote(self.repository, safe="/")
        revision = quote(self.revision, safe="")
        filename = quote(self.filename, safe="")
        return (
            f"https://huggingface.co/{repository}/resolve/"
            f"{revision}/{filename}"
        )


def validate_model_spec(model):
    """Validate values that can make the web process access disk or network."""
    errors = {}
    if not MODEL_ID_PATTERN.fullmatch(model.model_id or ""):
        errors["model_id"] = _(
            "Use lowercase letters, numbers, dots, dashes, or underscores."
        )
    if not (model.name or "").strip():
        errors["name"] = _("Enter a display name.")
    if not REPOSITORY_PATTERN.fullmatch(model.repository or ""):
        errors["repository"] = _(
            "Use a Hugging Face repository in owner/name format."
        )
    if not REVISION_PATTERN.fullmatch(model.revision or ""):
        errors["revision"] = _("Enter a valid branch, tag, or commit.")
    if (
        not model.filename
        or Path(model.filename).name != model.filename
        or Path(model.filename).suffix.lower() != ".gguf"
    ):
        errors["filename"] = _("Enter a local filename ending in .gguf.")

    checksum = (model.sha256 or "").lower()
    if checksum and not SHA256_PATTERN.fullmatch(checksum):
        errors["sha256"] = _(
            "Enter a 64-character SHA-256 value or leave it blank."
        )
    if (
        isinstance(model.download_size, bool)
        or not isinstance(model.download_size, int)
        or model.download_size < 0
        or model.download_size > settings.CHAT_MODEL_MAX_DOWNLOAD_BYTES
    ):
        errors["download_size"] = _(
            "The estimated size exceeds the server download limit."
        )

    if errors:
        raise ValidationError(errors)
