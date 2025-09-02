from django.contrib import admin

from modeltranslation.admin import TranslationAdmin
from .models import Question, Answer, AnswerWeight


@admin.register(Question)
class QuestionAdmin(TranslationAdmin):
    list_display = ('text', 'order')
    list_editable = ('order',)
    search_fields = ('text',)
    ordering = ('order',)


class AnswerWeightAdmin(admin.StackedInline):
    model = AnswerWeight
    extra = 1
    fields = ('breed', 'weight')


@admin.register(Answer)
class AnswerAdmin(TranslationAdmin):
    list_display = ('text', 'question', 'order',)
    list_editable = ('order',)
    search_fields = ('text',)
    ordering = ('question__order', 'order',)

    inlines = [AnswerWeightAdmin]
