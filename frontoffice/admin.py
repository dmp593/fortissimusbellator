from django.contrib import admin
from .translation import FrequentlyAskedQuestion

from chat.admin_aliases import ChatAliasSuggestionsAdminMixin
from fortissimusbellator.admin import FieldTranslatorAdmin


@admin.register(FrequentlyAskedQuestion)
class FrequentlyAskedQuestionAdmin(
    ChatAliasSuggestionsAdminMixin,
    FieldTranslatorAdmin,
):
    translation_fields = ['question', 'answer']
