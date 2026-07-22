from django.urls import path

from .views import message


app_name = "chat"

urlpatterns = [
    path("message/", message, name="message"),
]
