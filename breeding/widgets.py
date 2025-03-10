from django.forms import ClearableFileInput
from django.urls import reverse


class FilePreviewWidget(ClearableFileInput):
    template_name = 'widgets/file_preview.html'

    chunked_upload_url = "upload"
    chunked_upload_method = "post"

    class Media:
        css = {
            'all': [
                'widgets/css/file_preview.css'
            ],
        }
        js = [
            'widgets/js/file_preview.js'
        ]

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)

        context['widget']['attrs']['data-chunked-upload-url'] = reverse(self.chunked_upload_url)
        context['widget']['attrs']['data-chunked-upload-method'] = self.chunked_upload_method

        return context


class FilePreviewInlineWidget(FilePreviewWidget):
    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context['widget']['attrs']['multiple'] = True
        return context
