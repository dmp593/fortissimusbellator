import mimetypes
import pathlib

from uuid import uuid4
from django.db import models
from django.contrib.auth import get_user_model

from django.contrib.contenttypes.fields import (
    GenericRelation,
    GenericForeignKey
)

from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext_lazy as _

from tags.models import Tag
from .managers import Manager, PublishedPostsManager


User = get_user_model()


def post_cover_upload_to(instance: 'Post', filename: str) -> str:
    extension = mimetypes.guess_extension(filename)

    if not extension:
        extension = pathlib.Path(filename).suffix

    return f"posts/{uuid4().hex}{extension}"


class Like(models.Model):
    liked_by = models.ForeignKey(
        to=User,
        on_delete=models.CASCADE,
        verbose_name=_('liked by'),
    )

    liked_at = models.DateTimeField(
        null=False,
        blank=False,
        verbose_name=_('liked at'),
    )

    content_type = models.ForeignKey(
        ContentType,
        limit_choices_to=models.Q(app_label='blog'),
        on_delete=models.CASCADE
    )

    object_id = models.UUIDField()

    content_object = GenericForeignKey("content_type", "object_id")

    class Meta:
        unique_together = (
            ('liked_by', 'content_type', 'object_id'),
        )


class Category(models.Model):
    name = models.CharField(
        max_length=100,
        null=False,
        blank=False,
        verbose_name=_('name'),
    )

    parent = models.ForeignKey(
        'Category',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name=_('parent'),
    )

    active = models.BooleanField(
        default=True,
        verbose_name=_('active')
    )

    order = models.IntegerField(
        default=999,
        verbose_name=_('order')
    )

    objects = Manager()
    objects_active = Manager(active=True)

    class Meta:
        verbose_name = _('category')
        verbose_name_plural = _('categories')

        unique_together = (
            ('name', 'parent', ),
        )

        ordering = ('order', 'name', 'parent__name')

    def __str__(self) -> str:
        return f"{self.name}"


class Post(models.Model):
    author = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=False,
        verbose_name=_('author'),
    )

    title = models.CharField(
        max_length=255,
        null=False,
        blank=False,
        verbose_name=_('title')
    )

    cover = models.ImageField(
        upload_to=post_cover_upload_to,
        null=False,
        blank=False,
        verbose_name=_('cover'),
    )

    content = models.JSONField(
        null=False,
        blank=False,
        verbose_name=_('content')
    )

    categories = models.ManyToManyField(
        Category,
        related_name='posts',
        related_query_name='post',
        verbose_name=_('category')
    )

    tags = GenericRelation(
        Tag,
        verbose_name=_('tags'),
    )

    likes = GenericRelation(
        Like,
        verbose_name=_('likes')
    )

    published_at = models.DateTimeField(
        null=True,
        verbose_name=_('published at')
    )

    active = models.BooleanField(
        null=False,
        default=False,
        verbose_name=_('active')
    )

    order = models.IntegerField(
        default=999,
        verbose_name=_('order')
    )

    posts = Manager()
    posts_published = PublishedPostsManager(active=True)

    def __str__(self):
        return f"{self.title}"

    class Meta:
        verbose_name = _('post')
        verbose_name_plural = _('posts')
        ordering = ('order', '-published_at')


class Comment(models.Model):
    author = models.ForeignKey(
        to=User,
        on_delete=models.CASCADE,
        null=False,
        verbose_name=_('author')
    )

    post = models.ForeignKey(
        to=Post,
        on_delete=models.CASCADE,
        null=False,
        related_name='comments',
        related_query_name='comment',
        verbose_name=_('post')
    )

    parent = models.ForeignKey(
        to='Comment',
        on_delete=models.SET_NULL,
        related_name='replies',
        related_query_name='reply',
        null=True,
        verbose_name=_('parent')
    )

    comment = models.TextField(
        null=False,
        blank=False,
        verbose_name=_('comment')
    )

    commented_at = models.DateTimeField(
        null=False,
        blank=False,
        verbose_name=_('commented_at')
    )

    likes = GenericRelation(
        Like,
        verbose_name=_('likes')
    )

    class Meta:
        verbose_name = _('comment')
        verbose_name_plural = _('comments')
