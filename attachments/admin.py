from django.contrib.contenttypes import admin as ct_admin

from . import models, forms


class AttachmentStackedInline(ct_admin.GenericStackedInline):
    """
    Inline for managing attachments (files) related to any model via GenericForeignKey.
    """
    model = models.Attachment
    form = forms.AttachmentInlineForm
    extra = 1  # Number of empty forms to display by default
    fields = ('file', 'thumbnail', 'description', 'order')
