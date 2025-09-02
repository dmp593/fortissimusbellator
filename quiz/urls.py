from django.urls import path

from .views import pet_finder


app_name = "quiz"


urlpatterns = [
    path("", pet_finder, name="start_quiz"),
    path("result/", pet_finder, name="quiz_result"),
]
