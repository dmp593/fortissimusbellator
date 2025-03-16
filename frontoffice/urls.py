from django.urls import path
from . import views


urlpatterns = [
    path('', views.home, name='home'),
    path('about-us/', views.about_us, name='about_us'),
    path('contact-us/', views.contact_us, name='contact_us'),
    path('our-dogs/', views.our_dogs, name='our_dogs'),
    path('our-dogs/<int:breed_id>/', views.our_dogs, name='our_dogs'),
    path('buy-a-dog/', views.buy_a_dog, name='buy_a_dog'),
    path('buy-a-dog/<int:dog_id>/', views.dog_detail, name='dog_detail'),
    path('upcoming-litters/', views.upcoming_litters, name='upcoming_litters'),
    path('upcoming-litters/<int:litter_id>/', views.litter_detail, name='litter_detail'),
    path('faqs/', views.faqs, name='faqs'),
]
