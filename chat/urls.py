from django.urls import path

from .views import message, model_status


app_name = "chat"

urlpatterns = [
    path("message/", message, name="message"),
    path("model-status/", model_status, name="model_status"),
]
