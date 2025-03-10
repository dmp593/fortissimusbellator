from django.urls import path
from . import views


urlpatterns = [
    path('', views.home, name='home'),
    path('about-us/', views.about_us, name='about_us'),
    path('contact-us/', views.contact_us, name='contact_us'),
    path('our-dogs/', views.our_dogs, name='our_dogs'),
    path('our-dogs/<int:dog_id>/', views.dog_detail, name='dog_detail'),
    path('faqs/', views.faqs, name='faqs'),
]
