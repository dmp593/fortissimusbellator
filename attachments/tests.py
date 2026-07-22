import os
import socket
import time
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from fortissimusbellator.upload_security import cleanup_stale_chunks


@override_settings(STATIC_ROOT=None)
class StaffUploadTests(TestCase):
    def setUp(self):
        self.media_directory = TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.settings_override = override_settings(
            MEDIA_ROOT=self.media_directory.name,
            UPLOAD_MAX_FILE_BYTES=6,
            UPLOAD_MAX_CHUNK_BYTES=3,
            UPLOAD_CHUNK_MAX_AGE_SECONDS=10,
            EDITOR_IMAGE_MAX_BYTES=1024 * 1024,
            EDITOR_IMAGE_MAX_PIXELS=1_000_000,
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

        user_model = get_user_model()
        self.staff = user_model.objects.create_user(
            username="staff",
            password="password",
            is_staff=True,
        )
        self.regular_user = user_model.objects.create_user(
            username="customer",
            password="password",
        )

    def test_upload_endpoints_require_staff(self):
        urls = (
            reverse("upload"),
            reverse("editorjs_image_upload_by_file"),
            reverse("editorjs_image_upload_by_url"),
        )
        for url in urls:
            with self.subTest(url=url):
                self.client.force_login(self.regular_user)
                self.assertEqual(self.client.post(url).status_code, 302)

    def test_chunks_are_streamed_into_one_bounded_file(self):
        self.client.force_login(self.staff)
        upload_id = str(uuid4())
        payload = {
            "fileId": upload_id,
            "fileName": "document.pdf",
            "totalChunks": "2",
        }

        first = self.client.post(reverse("upload"), {
            **payload,
            "chunkIndex": "0",
            "file": SimpleUploadedFile("chunk", b"abc"),
        })
        second = self.client.post(reverse("upload"), {
            **payload,
            "chunkIndex": "1",
            "file": SimpleUploadedFile("chunk", b"def"),
        })

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        final_path = (
            Path(self.media_directory.name)
            / "uploads"
            / upload_id.replace("-", "")
            / "document.pdf"
        )
        self.assertEqual(final_path.read_bytes(), b"abcdef")
        self.assertIn("document.pdf", second.json()["url"])

    def test_chunk_count_cannot_exceed_file_limit(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("upload"), {
            "fileId": str(uuid4()),
            "fileName": "too-large.bin",
            "totalChunks": "3",
            "chunkIndex": "0",
            "file": SimpleUploadedFile("chunk", b"abc"),
        })
        self.assertEqual(response.status_code, 400)

    def test_editor_upload_accepts_a_real_image(self):
        image_data = BytesIO()
        Image.new("RGB", (10, 10), color="red").save(image_data, "PNG")
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("editorjs_image_upload_by_file"),
            {"image": SimpleUploadedFile(
                "image.png", image_data.getvalue(), content_type="image/png"
            )},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["success"], 1)
        self.assertTrue(response.json()["file"]["url"].endswith(".png"))

    def test_editor_upload_rejects_content_that_is_not_an_image(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("editorjs_image_upload_by_file"),
            {"image": SimpleUploadedFile("fake.png", b"not an image")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["success"], 0)

    @patch("fortissimusbellator.upload_security.requests.get")
    def test_remote_image_blocks_internal_addresses_before_request(self, get):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("editorjs_image_upload_by_url"),
            {"url": "http://127.0.0.1/private-image"},
        )
        self.assertEqual(response.status_code, 400)
        get.assert_not_called()

    @patch("fortissimusbellator.upload_security.socket.getaddrinfo")
    @patch("fortissimusbellator.upload_security.requests.get")
    def test_remote_image_revalidates_every_redirect(self, get, getaddrinfo):
        getaddrinfo.side_effect = [
            [(
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", 80),
            )],
            [(
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("127.0.0.1", 80),
            )],
        ]
        remote_response = get.return_value
        remote_response.status_code = 302
        remote_response.headers = {"Location": "http://internal.test/image"}
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("editorjs_image_upload_by_url"),
            {"url": "http://public.test/image"},
        )

        self.assertEqual(response.status_code, 400)
        get.assert_called_once()

    def test_abandoned_chunks_are_cleaned_opportunistically(self):
        stale_directory = (
            Path(self.media_directory.name) / "uploads" / ".chunks" / "stale"
        )
        stale_directory.mkdir(parents=True)
        (stale_directory / "00000.part").write_bytes(b"old")
        old_time = time.time() - 60
        os.utime(stale_directory, (old_time, old_time))

        cleanup_stale_chunks()

        self.assertFalse(stale_directory.exists())
