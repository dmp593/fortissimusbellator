from django.contrib import admin

from modeltranslation.admin import TranslationAdmin

from . import forms
from .translation import models


@admin.register(models.Category)
class CategoryAdmin(TranslationAdmin):
    ...


@admin.register(models.Post)
class PostAdmin(TranslationAdmin):
    form = forms.PostForm

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields['author'].initial = request.user
        return form

    def save_model(self, request, obj, form, change):
        if not obj.author:
            obj.author = request.user
        super().save_model(request, obj, form, change)
