from django import forms
from .models import Question


class QuizForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for q in Question.objects.all():
            question_id = f"question_{q.pk}"

            question_field = forms.ModelChoiceField(
                queryset=q.answers.all(),
                widget=forms.RadioSelect,
                label=q.text,
                required=True
            )

            self.fields[question_id] = question_field
