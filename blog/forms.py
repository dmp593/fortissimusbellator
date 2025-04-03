from django import forms
from editorjs.widgets import EditorJsWidget

from .translation import models


class PostForm(forms.ModelForm):
    class Meta:
        model = models.Post
        fields = '__all__'
        widgets = {
            'content': EditorJsWidget(),
        }
