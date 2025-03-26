from django.contrib import admin
from modeltranslation.admin import TranslationAdmin
from .translation import FrequentlyAskedQuestion


@admin.register(FrequentlyAskedQuestion)
class FrequentlyAskedQuestionAdmin(TranslationAdmin):
    ...
