from django import forms
from django_recaptcha.fields import ReCaptchaField


class ContactForm(forms.Form):
    name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Your Name'}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={'class': 'form-input', 'placeholder': 'Your Email'}))
    message = forms.CharField(widget=forms.Textarea(attrs={'class': 'form-textarea', 'placeholder': 'Your Message', 'rows': 5}))
    captcha = ReCaptchaField()
