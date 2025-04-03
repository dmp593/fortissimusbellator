from django.urls import path
from . import views


app_name = 'breeding'


urlpatterns = [
    path('our-dogs/', views.our_dogs, name='our_dogs'),
    path('our-dogs/<int:breed_id>/', views.our_dogs, name='our_dogs'),
    path('buy-a-dog/', views.buy_a_dog, name='buy_a_dog'),
    path('buy-a-dog/<int:dog_id>/', views.dog_detail, name='dog_detail'),
    path('upcoming-litters/', views.upcoming_litters, name='upcoming_litters'),
    path('upcoming-litters/<int:litter_id>/', views.litter_detail, name='litter_detail'),
]
