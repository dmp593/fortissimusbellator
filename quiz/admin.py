from django.contrib import admin

from modeltranslation.admin import TranslationStackedInline

from fortissimusbellator.admin import FieldTranslatorAdmin


from .models import Question, Answer, AnswerWeight


class AnswerStackedInline(TranslationStackedInline):
    model = Answer
    extra = 1


@admin.register(Question)
class QuestionAdmin(FieldTranslatorAdmin):
    model = Question
    list_display = ('text', 'order')
    list_editable = ('order',)
    search_fields = ('text',)
    ordering = ('order',)
    inlines = [AnswerStackedInline]
    translation_fields = ['text']


class AnswerWeightStackedInline(admin.StackedInline):
    model = AnswerWeight
    extra = 1


@admin.register(Answer)
class AnswerAdmin(FieldTranslatorAdmin):
    list_display = ('text', 'question', 'order',)
    list_editable = ('order',)
    search_fields = ('text',)
    ordering = ('question__order', 'order',)
    inlines = [AnswerWeightStackedInline]
    translation_fields = ['text']
