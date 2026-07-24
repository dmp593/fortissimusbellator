from django.urls import path

from . import views


app_name = 'reservations'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('payment/success/', views.payment_success, name='payment_success'),
    path(
        'pre-reservations/<uuid:public_id>/confirmed/',
        views.pre_reservation_confirmation,
        name='pre_reservation_confirmation',
    ),
    path(
        'pre-reservations/<uuid:public_id>/payment-cancelled/',
        views.payment_cancelled,
        name='payment_cancelled',
    ),
    path(
        'pre-reservations/<uuid:public_id>/retry-payment/',
        views.retry_pre_reservation_payment,
        name='retry_pre_reservation_payment',
    ),
    path(
        'pre-reservations/<uuid:public_id>/cancel/',
        views.cancel_pre_reservation,
        name='cancel_pre_reservation',
    ),
    path(
        'reservations/<uuid:public_id>/checkout/',
        views.reservation_deposit_checkout,
        name='reservation_checkout',
    ),
    path(
        'reservations/<uuid:public_id>/confirmed/',
        views.reservation_confirmation,
        name='reservation_confirmation',
    ),
    path(
        'reservations/<uuid:public_id>/payment-cancelled/',
        views.reservation_payment_cancelled,
        name='reservation_payment_cancelled',
    ),
    path(
        'reservations/<uuid:public_id>/retry-payment/',
        views.retry_reservation_payment,
        name='retry_reservation_payment',
    ),
    path(
        'documents/<int:document_id>/download/',
        views.download_document,
        name='download_document',
    ),
    path(
        'documents/<int:document_id>/retry-pdf/',
        views.retry_pdf,
        name='retry_pdf',
    ),
]
