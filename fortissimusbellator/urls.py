"""
URL configuration for fortissimusbellator project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from .views import FileUploadView, EditorJsImageUploadByFileView, EditorJsImageUploadByUrlView

urlpatterns = [
    path('', include('frontoffice.urls')),
    path('', include('breeding.urls')),
    path('', include('accounts.urls')),
    path('blog/', include('blog.urls')),
    path('i18n/', include('django.conf.urls.i18n')),
    path('admin/', admin.site.urls),
    path('upload/', FileUploadView.as_view(), name='upload'),
    path(
        'editorjs/image/upload/file/',
        EditorJsImageUploadByFileView.as_view(),
        name='editorjs_image_upload_by_file'
    ),
    path(
        'editorjs/image/upload/url/',
        EditorJsImageUploadByUrlView.as_view(),
        name='editorjs_image_upload_by_url'
    ),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
