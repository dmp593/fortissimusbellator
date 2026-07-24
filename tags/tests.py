from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from blog.models import Post

from .models import Tag


class GenericTagTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        author = get_user_model().objects.create_user(username='tag-author')
        cls.first_post = Post.posts.create(
            author=author,
            title='First',
            cover='posts/first.jpg',
            content={'blocks': []},
            published_at=timezone.now(),
            active=True,
        )
        cls.second_post = Post.posts.create(
            author=author,
            title='Second',
            cover='posts/second.jpg',
            content={'blocks': []},
            published_at=timezone.now(),
            active=True,
        )
        cls.content_type = ContentType.objects.get_for_model(Post)

    def create_tag(self, post):
        return Tag.objects.create(
            tag='care',
            content_type=self.content_type,
            object_id=post.pk,
        )

    def test_tag_is_unique_per_object(self):
        self.create_tag(self.first_post)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_tag(self.first_post)

    def test_same_tag_is_allowed_on_different_objects(self):
        first = self.create_tag(self.first_post)
        second = self.create_tag(self.second_post)

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(str(first), 'care')

    def test_generic_relation_deletes_tags_with_parent(self):
        tag = self.create_tag(self.first_post)

        self.first_post.delete()

        self.assertFalse(Tag.objects.filter(pk=tag.pk).exists())
