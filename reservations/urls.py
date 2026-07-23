from django.urls import path

from . import views


app_name = 'reservations'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('payment/success/', views.payment_success, name='payment_success'),
    path(
        '<uuid:public_id>/confirmed/',
        views.reservation_confirmation,
        name='reservation_confirmation',
    ),
    path(
        '<uuid:public_id>/payment-cancelled/',
        views.payment_cancelled,
        name='payment_cancelled',
    ),
    path(
        '<uuid:public_id>/retry-payment/',
        views.retry_payment,
        name='retry_payment',
    ),
    path(
        '<uuid:public_id>/cancel/',
        views.cancel_reservation,
        name='cancel',
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
