import atexit

from django.apps import AppConfig


class ChatConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'chat'

    def ready(self):
        from .model_selection import selected_model
        from .runtime import ChatRuntime
        from .signals import connect_search_index_signals

        self.runtime = ChatRuntime(selected_model)
        atexit.register(self.runtime.close)
        connect_search_index_signals()
