import json

from django import forms
from django.utils.safestring import mark_safe
from django.template.loader import render_to_string
from django.conf import settings

from .conf import CDN_JSDELIVR


class EditorJsWidget(forms.Widget):
    template_name = 'forms/widgets/editorjs.html'

    def __init__(
        self,
        *args,
        plugins=settings.EDITORJS_DEFAULT_PLUGINS,
        config=settings.EDITORJS_DEFAULT_CONFIG,
        **kwargs
    ):
        self.plugins = plugins
        self.config = config

        # Fix "__init__() got an unexpected keyword argument 'widget'"
        widget = kwargs.pop('widget', None)

        if widget:
            self.plugins = getattr(widget, 'plugins', self.plugins)
            self.config = getattr(widget, 'config', self.config)

        super().__init__(*args, **kwargs)

    @property
    def media(self):
        return forms.Media(
            css={
                "all": [
                    'forms/widgets/css/editorjs.css'
                ]
            },
            js=[
                f"{CDN_JSDELIVR}npm/@editorjs/editorjs",
                *[f"{CDN_JSDELIVR}npm/{plugin}" for plugin in self.plugins],
                'forms/widgets/js/editorjs.js',
            ]
        )

    def render(self, name, value, attrs=None, renderer=None):
        context = {
            'name': name,
            'value': value or '',
            'config': json.dumps(self.config),
            'widget': self,
        }

        try:
            render_to_string(self.template_name, context)
        except Exception as e:
            print("error: " + str(e))

        return mark_safe(
            render_to_string(self.template_name, context)
        )
