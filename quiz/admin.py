from django.contrib import admin

from modeltranslation.admin import (
    TranslationAdmin, TranslationStackedInline
)

from .models import Question, Answer, AnswerWeight


class AnswerStackedInline(TranslationStackedInline):
    model = Answer
    extra = 1


@admin.register(Question)
class QuestionAdmin(TranslationAdmin):
    model = Question
    list_display = ('text', 'order')
    list_editable = ('order',)
    search_fields = ('text',)
    ordering = ('order',)
    inlines = [AnswerStackedInline]


class AnswerWeightStackedInline(admin.StackedInline):
    model = AnswerWeight
    extra = 1


@admin.register(Answer)
class AnswerAdmin(TranslationAdmin):
    list_display = ('text', 'question', 'order',)
    list_editable = ('order',)
    search_fields = ('text',)
    ordering = ('question__order', 'order',)
    inlines = [AnswerWeightStackedInline]
