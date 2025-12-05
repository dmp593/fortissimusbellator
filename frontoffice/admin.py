from django.contrib import admin
from .translation import FrequentlyAskedQuestion

from fortissimusbellator.admin import FieldTranslatorAdmin


@admin.register(FrequentlyAskedQuestion)
class FrequentlyAskedQuestionAdmin(FieldTranslatorAdmin):
    translation_fields = ['question', 'answer']
