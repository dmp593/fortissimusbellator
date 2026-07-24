from django.urls import path
from . import views


app_name = 'breeding'


urlpatterns = [
    path('our-dogs/', views.our_dogs, name='our_dogs'),
    path('our-dogs/<int:breed_id>/', views.our_dogs, name='our_dogs'),
    
    path('buy-a-dog/', views.buy_a_dog, name='buy_a_dog'),
    path('buy-a-dog/<int:dog_id>/', views.dog_detail, name='dog_detail'),
    path('buy-a-dog/<int:dog_id>/pre_reserve', views.pre_reserve_dog, name='pre_reserve_dog'),
    
    path('upcoming-litters/', views.upcoming_litters, name='upcoming_litters'),
    path('upcoming-litters/<int:litter_id>/', views.litter_detail, name='litter_detail'),
    path(
        'upcoming-litters/<int:litter_id>/alerts/subscribe/',
        views.subscribe_litter_alert,
        name='subscribe_litter_alert',
    ),
    path(
        'upcoming-litters/<int:litter_id>/alerts/unsubscribe/',
        views.unsubscribe_litter_alert,
        name='unsubscribe_litter_alert',
    ),
]
