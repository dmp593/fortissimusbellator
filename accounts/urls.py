from django.urls import path
from . import views


urlpatterns = [
    path(
        'welcome/',
        views.welcome,
        name='welcome'
    ),

    path(
        'register/',
        views.register,
        name='register'
    ),

    path(
        'activate/<uidb64>/<token>/',
        views.activate,
        name='activate'
    ),

    path(
        'email-confirmation-sent/',
        views.email_confirmation_sent,
        name='email_confirmation_sent'
    ),

    path(
        'resend-activation-email/',
        views.resend_activation_email,
        name='resend_activation_email'
    ),

    path(
        'login/',
        views.LoginView.as_view(),
        name='login'
    ),

    path(
        'logout/',
        views.LogoutView.as_view(),
        name='logout'
    ),


    # Password reset URLs
    path(
        'password-reset/',
        views.PasswordResetView.as_view(),
        name='password_reset'
    ),

    path(
        'password-reset/done/',
        views.PasswordResetDoneView.as_view(),
        name='password_reset_done'
    ),

    path(
        'password-reset-confirm/<uidb64>/<token>/',
        views.PasswordResetConfirmView.as_view(),
        name='password_reset_confirm'
    ),

    path(
        'password-reset-complete/',
        views.PasswordResetCompleteView.as_view(),
        name='password_reset_complete'
    ),
]
