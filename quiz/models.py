from django.db import models
from django.utils.translation import gettext_lazy as _


from breeding.models import Breed


class Question(models.Model):
    text = models.CharField(
        max_length=255,
        verbose_name=_('question')
    )

    order = models.IntegerField(
        default=999,
        verbose_name=_('order'),
    )

    class Meta:
        verbose_name = _('Question')
        verbose_name_plural = _('Questions')
        ordering = ["order"]

    def __str__(self):
        return self.text


class Answer(models.Model):
    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name="answers",
        related_query_name="answer",
        verbose_name=_('question')
    )

    text = models.CharField(
        max_length=255,
        verbose_name=_('answer')
    )

    # weight = relation to breed (could be a score or mapping table)
    breeds = models.ManyToManyField(
        Breed,
        through="AnswerWeight",
        verbose_name=_('breeds')
    )

    order = models.IntegerField(
        default=999,
        verbose_name=_('order'),
    )

    class Meta:
        verbose_name = _('Answer')
        verbose_name_plural = _('Answers')
        ordering = ["order"]

    def __str__(self):
        return self.text


class AnswerWeight(models.Model):
    answer = models.ForeignKey(
        Answer,
        on_delete=models.CASCADE,
        verbose_name=_('answer')
    )

    breed = models.ForeignKey(
        Breed,
        on_delete=models.CASCADE,
        verbose_name=_('breed')
    )

    weight = models.IntegerField(
        default=0,  # e.g., +3 if strong match, 0 if irrelevant
        verbose_name=_('weight')
    )

    class Meta:
        unique_together = ("answer", "breed")  # one weight per breed per answer
        verbose_name = _('Answer Weight')
        verbose_name_plural = _('Answer Weights')

    def __str__(self):
        return self.answer.text
