"""Small Django admin widgets used by the chat module."""

from django.contrib.admin.widgets import AdminTextareaWidget


class ChatAliasTextareaWidget(AdminTextareaWidget):
    """Textarea with an optional, admin-only AI suggestion action."""

    template_name = "admin/widgets/chat_alias_textarea.html"

    def __init__(self, attrs=None, suggestion_url=""):
        super().__init__(attrs)
        self.suggestion_url = suggestion_url

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["suggestion_url"] = self.suggestion_url
        return context

    class Media:
        css = {"all": ("css/admin_chat_aliases.css",)}
        js = ("js/admin/chat_aliases.js",)
