from django.core.mail import send_mail
from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, get_user_model
from django.contrib.auth import views as auth_views
from django.contrib.auth.tokens import default_token_generator
from django.contrib.sites.shortcuts import get_current_site
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.utils.translation import gettext_lazy as _
from django.template.loader import render_to_string


from django.urls import reverse_lazy


from .models import Profile
from .forms import UserCreationForm


User = get_user_model()


class LoginView(auth_views.LoginView):
    template_name = 'login.html'


class LogoutView(auth_views.LogoutView):
    template_name = 'logout.html'


class PasswordResetView(auth_views.PasswordResetView):
    template_name = 'password/password_reset_form.html'
    html_email_template_name = 'password/password_reset_email.html'
    success_url = reverse_lazy('password_reset_done')


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = 'password/password_reset_done.html'


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = 'password/password_reset_confirm.html'
    success_url = reverse_lazy('password_reset_complete')


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = 'password/password_reset_complete.html'


def register(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = False  # User is inactive until email confirmation
            user.save()

            # Create a Profile instance for the user
            phone = request.POST.get('phone')
            Profile.objects.create(user=user, phone=phone)

            # Send activation email
            current_site = get_current_site(request)

            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)

            message = render_to_string('email_activate_account.html', {
                'user': user,
                'domain': current_site.domain,
                'uid': uid,
                'token': token,
            })

            send_mail(
                subject=_('Activate your account'),
                message=request.get_host() + reverse(
                    "activate",
                    kwargs={
                        'uidb64': uid, 'token': token
                    }
                ),
                html_message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email]
            )
            
            return redirect('email_confirmation_sent')
    else:
        form = UserCreationForm()
    return render(request, 'register.html', {'form': form})


def resend_activation_email(request):
    if request.method == 'POST':
        email = request.POST.get('email')
        try:
            user = User.objects.get(email=email, is_active=False)
            # Resend the activation email
            current_site = get_current_site(request)

            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)

            message = render_to_string('email_activate_account.html', {
                'user': user,
                'domain': current_site.domain,
                'uid': uid,
                'token': token,
            })

            send_mail(
                subject=_('Activate your account'),
                message=request.get_host() + reverse(
                    "activate",
                    kwargs={
                        'uidb64': uid, 'token': token
                    }
                ),
                html_message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email]
            )
            return redirect('email_confirmation_sent')
        except User.DoesNotExist:
            return render(request, 'resend_activation_email.html', {'error': 'No inactive user found with this email.'})
    return render(request, 'resend_activation_email.html')


def activate(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is not None and default_token_generator.check_token(user, token):
        user.is_active = True
        user.save()
        login(request, user)
        return redirect('welcome')
    else:
        return HttpResponse('Activation link is invalid!')


def email_confirmation_sent(request):
    return render(request, 'email_confirmation_sent.html')


@login_required
def welcome(request):
    return render(request, 'welcome.html')

