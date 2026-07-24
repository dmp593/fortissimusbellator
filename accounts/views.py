import logging

from django.conf import settings
from django.http import HttpResponseRedirect
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, get_user_model
from django.contrib.auth import views as auth_views
from django.contrib.auth import forms as auth_forms
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.password_validation import password_validators_help_texts
from django.db import transaction
from django.utils.http import (
    url_has_allowed_host_and_scheme,
    urlsafe_base64_decode,
)
from django.utils.encoding import force_str
from django.utils.translation import gettext_lazy as _
from django.contrib import messages
from django.views.generic import UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.decorators.http import require_http_methods


from django.urls import reverse_lazy


from .emails import send_activation_email
from .models import Profile
from .forms import (
    LitterAlertPreferenceForm,
    UserCreationForm,
    UserProfileForm,
)
from breeding.services.litter_alerts import get_or_create_alert_preference


User = get_user_model()
logger = logging.getLogger(__name__)


class LoginView(auth_views.LoginView):
    template_name = 'login.html'


class LogoutView(auth_views.LogoutView):
    template_name = 'logout.html'


class PasswordResetView(auth_views.PasswordResetView):
    template_name = 'password/password_reset_form.html'
    email_template_name = 'password/password_reset_email.txt'
    html_email_template_name = 'password/password_reset_email.html'
    subject_template_name = 'password/password_reset_subject.txt'
    from_email = settings.DEFAULT_FROM_EMAIL
    success_url = reverse_lazy('password_reset_done')

    def form_valid(self, form):
        form.save(
            domain_override=self.request.get_host(),
            use_https=self.request.is_secure(),
            token_generator=self.token_generator,
            from_email=self.from_email,
            email_template_name=self.email_template_name,
            subject_template_name=self.subject_template_name,
            request=self.request,
            html_email_template_name=self.html_email_template_name,
            extra_email_context={
                'language_code': self.request.LANGUAGE_CODE,
            },
        )
        return HttpResponseRedirect(self.get_success_url())


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = 'password/password_reset_done.html'


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = 'password/password_reset_confirm.html'
    success_url = reverse_lazy('password_reset_complete')


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = 'password/password_reset_complete.html'


@require_http_methods(['GET', 'POST'])
def register(request):
    next_url = _safe_next_url(request)
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = form.save(commit=False)
                user.is_active = False
                user.save()
                Profile.objects.create(
                    user=user,
                    phone=form.cleaned_data['phone'],
                )
            try:
                send_activation_email(
                    request=request,
                    user=user,
                    next_url=next_url,
                )
            except Exception:
                logger.exception(
                    'Unable to send account activation email',
                    extra={'user_id': user.pk},
                )
                messages.error(
                    request,
                    _(
                        'Your account was created, but the activation email '
                        'could not be sent. Please request it again.'
                    ),
                )
                return redirect('resend_activation_email')
            return redirect('email_confirmation_sent')
    else:
        form = UserCreationForm()
    return render(
        request,
        'register.html',
        {'form': form, 'next': next_url},
    )


@require_http_methods(['GET', 'POST'])
def resend_activation_email(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        user = (
            User.objects.filter(email__iexact=email, is_active=False)
            .order_by('pk')
            .first()
        )
        if user is not None:
            try:
                send_activation_email(request=request, user=user)
            except Exception:
                logger.exception(
                    'Unable to resend account activation email',
                    extra={'user_id': user.pk},
                )
        messages.success(
            request,
            _(
                'If an inactive account exists for this address, a new '
                'activation email has been sent.'
            ),
        )
        return redirect('email_confirmation_sent')
    return render(request, 'resend_activation_email.html')


def activate(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is not None and default_token_generator.check_token(user, token):
        user.is_active = True
        user.save(update_fields=['is_active'])
        login(request, user)
        return redirect(_safe_next_url(request) or 'welcome')
    return render(request, 'activation_invalid.html', status=400)


def email_confirmation_sent(request):
    return render(request, 'email_confirmation_sent.html')


@login_required
def welcome(request):
    return render(request, 'welcome.html')


class UserProfileView(LoginRequiredMixin, UpdateView):
    model = Profile
    form_class = UserProfileForm
    success_url = reverse_lazy('profile')

    def get_object(self):
        try:
            return self.request.user.profile
        except Profile.DoesNotExist:
            return Profile.objects.create(user=self.request.user)

    def form_valid(self, form):
        messages.success(self.request, _('Profile saved.'))
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, _('Error saving profile.'))
        return super().form_invalid(form)


class ChangePasswordView(auth_views.PasswordChangeView):
    model = User
    template_name = 'accounts/password_change_form.html'
    success_url = reverse_lazy('change_password')
    form_class = auth_forms.PasswordChangeForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['password_validators_help_texts'] = password_validators_help_texts()
        return context

    def get_object(self):
        return self.request.user

    def form_valid(self, form):
        messages.success(self.request, _('Password changed successfully.'))
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, _('Error changing password.'))
        return super().form_invalid(form)


@login_required
def litter_alert_settings(request):
    preference = get_or_create_alert_preference(
        request.user,
        language_code=request.LANGUAGE_CODE,
    )
    form = LitterAlertPreferenceForm(
        request.POST or None,
        instance=preference,
    )
    if request.method == 'POST' and form.is_valid():
        preference = form.save(commit=False)
        preference.language_code = request.LANGUAGE_CODE
        preference.save()
        form.save_m2m()
        messages.success(request, _('Litter alert settings saved.'))
        return redirect('litter_alert_settings')

    overrides = (
        request.user.litter_alert_overrides.select_related(
            'litter',
            'litter__breed',
        )
        .filter(litter__active=True)
        .order_by('litter__name')
    )
    return render(
        request,
        'accounts/litter_alert_settings.html',
        {
            'form': form,
            'alert_overrides': overrides,
        },
    )


def _safe_next_url(request):
    next_url = request.POST.get('next') or request.GET.get('next') or ''
    if not next_url:
        return ''
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return ''
    return next_url
