import shutil
import mimetypes

from pathlib import Path
from urllib.parse import unquote

from django import forms
from django.conf import settings
from django.core.files.storage import default_storage

from attachments import models
from attachments.widgets import FilePreviewWidget, FilePreviewInlineWidget


LEN_MEDIA_URL = len(settings.MEDIA_URL)


class AttachmentForm(forms.ModelForm):
    file = forms.FileField(widget=FilePreviewWidget, required=False)
    tmp_file = forms.CharField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = models.Attachment
        fields = ('file', 'thumbnail', 'description', 'order',)

    def save(self, commit=True):
        tmp_file = self.cleaned_data.get('tmp_file')

        if tmp_file:
            if self.instance.file and default_storage.exists(
                self.instance.file.name
            ):
                default_storage.delete(self.instance.file.name)

            tmp_file = Path(default_storage.location).joinpath(
                unquote(tmp_file)[LEN_MEDIA_URL:]
            )

            self.instance.filename = tmp_file.name

            mime_type = mimetypes.guess_type(self.instance.filename)[0]
            self.instance.mime_type = mime_type or "application/octet-stream"

            with open(tmp_file, 'rb') as f:
                self.instance.file.save(self.instance.filename, f)

            shutil.rmtree(tmp_file.parent)

        return super().save(commit)


class AttachmentInlineForm(AttachmentForm):
    file = forms.FileField(widget=FilePreviewInlineWidget, required=False)
