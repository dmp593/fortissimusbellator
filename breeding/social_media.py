import logging
import time
import requests

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.contrib.sites.models import Site
from django.utils.text import slugify
from attachments.models import Attachment


logger = logging.getLogger(__name__)


def get_animal_image_url(animal):
    """
    Retrieves the absolute URL of the first image attachment for the animal.
    It *must* be publicly accessible by the Meta servers.
    """

    content_type = ContentType.objects.get_for_model(animal)

    attachment = Attachment.objects.filter(
        content_type=content_type,
        object_id=animal.id,
        mime_type__startswith="image"
    ).first()

    if not attachment or not attachment.file:
        return None

    site = Site.objects.get_current()
    site_url = f"https://{site.domain}/"

    return f"{site_url}{attachment.file.url}"


# ---------------------------------------------------
# FACEBOOK PAGE PUBLISH
# ---------------------------------------------------

def publish_to_facebook(message, image_url):
    """
    Publish a photo post to the Facebook Page. Fallback to feed post if no image.

    Requires:
        pages_manage_posts
        pages_read_engagement
        pages_show_list
    """

    page_id = getattr(settings, "FACEBOOK_PAGE_ID", None)
    access_token = getattr(settings, "FACEBOOK_ACCESS_TOKEN", None)

    if not page_id or not access_token:
        logger.warning("Facebook credentials missing")
        return False, "Facebook credentials not configured"

    url = f"https://graph.facebook.com/{settings.FACEBOOK_GRAPH_VERSION}/{page_id}"

    if image_url:
        url = f"{url}/photos"
        payload = {
            "message": message,
            "url": image_url,
            "access_token": access_token,
        }
    else:
        url = f"{url}/feed"
        payload = {
            "message": message,
            "access_token": access_token,
        }

    try:
        res = requests.post(url, data=payload, timeout=30)
        data = res.json()

        if not res.ok:
            logger.error("Facebook publish error: %s", data)
            return False, data.get("error", {}).get("message")

        return True, data.get("id")

    except Exception as e:
        logger.exception("Error posting to Facebook")
        return False, str(e)


# ---------------------------------------------------
# INSTAGRAM PUBLISH
# ---------------------------------------------------

def create_instagram_media(ig_user_id, access_token, image_url, caption):
    """
    Step 1: Create IG media container (image post). Supports alt_text optional.
    """
    url = f"https://graph.facebook.com/{settings.FACEBOOK_GRAPH_VERSION}/{ig_user_id}/media"

    payload = {
        "image_url": image_url,
        "caption": caption,
        "access_token": access_token,
        # Optional: "alt_text": "Description for accessibility"
    }

    res = requests.post(url, data=payload, timeout=30)
    data = res.json()

    if not res.ok:
        logger.error("Instagram create media error: %s", data)
        return False, data.get("error", {}).get("message")

    creation_id = data.get("id")
    return True, creation_id


def publish_instagram_media(ig_user_id, access_token, creation_id):
    """
    Step 2: Publish existing IG media container (photo) to feed.

    Requires:
        instagram_basic
        instagram_content_publish
        pages_show_list
    """
    url = f"https://graph.facebook.com/{settings.FACEBOOK_GRAPH_VERSION}/{ig_user_id}/media_publish"

    payload = {
        "creation_id": creation_id,
        "access_token": access_token,
    }

    res = requests.post(url, data=payload, timeout=30)
    data = res.json()

    if not res.ok:
        logger.error("Instagram publish error: %s", data)
        return False, data.get("error", {}).get("message")

    return True, data.get("id")


def wait_for_media_ready(creation_id, access_token, timeout=25):
    """
    Optional: Poll the container until ready (recommended for reliability).
    """
    status_url = f"https://graph.facebook.com/{GRAPH_VERSION}/{creation_id}"
    start = time.time()

    while (time.time() - start) < timeout:
        res = requests.get(
            status_url,
            params={"fields": "status_code", "access_token": access_token},
            timeout=15,
        )
        info = res.json()

        if not res.ok:
            logger.error("IG status check error: %s", info)
            return False

        status = info.get("status_code")
        if status == "FINISHED":
            return True
        if status == "ERROR":
            logger.error("IG media processing error")
            return False

        time.sleep(2)

    logger.error("IG media processing timed out")
    return False


def publish_to_instagram(message, image_url):
    """
    Publish a photo post to an Instagram Business account.
    """

    ig_user_id = getattr(settings, "INSTAGRAM_ACCOUNT_ID", None)
    access_token = getattr(settings, "FACEBOOK_ACCESS_TOKEN", None)

    if not ig_user_id or not access_token:
        logger.warning("Instagram credentials missing")
        return False, "Instagram credentials not configured"

    if not image_url:
        return False, "Image URL required for Instagram"

    ok, creation_id = create_instagram_media(
        ig_user_id, access_token, image_url, message
    )
    if not ok:
        return False, creation_id

    if not wait_for_media_ready(creation_id, access_token):
        return False, "Media not ready"

    return publish_instagram_media(ig_user_id, access_token, creation_id)


# ---------------------------------------------------
# ENTRY FUNCTION
# ---------------------------------------------------

def publish_animal(animal):
    """
    Build a PT message and publish to FB Page and IG Business account.
    """
    site_domain = Site.objects.get_current().domain
    site_url = f"https://{site_domain}/"

    animal_breed_name = animal.breed.name_pt or animal.breed.name_en or animal.breed.name
    animal_description = animal.description_pt or animal.description_en or animal.description

    message = f"🐕 {animal.name} de Fortissimus Bellator 🐕\n"
    if animal.father and animal.mother:
        message += f"({animal.father.name} x {animal.mother.name})\n"
    message += f"Raça: {animal_breed_name}\n\n"

    message += f"{animal_description}\n\n"

    message += "Entre em contacto para mais informações!\n"
    message += f"🌐 Website: {site_url}\n"
    message += "📞 WhatsApp: +351 924 454 382\n\n"

    message += "⚔️FORTISSIMUS BELLATOR⚔️\n•▪️Breeding Legendaries▪️•\n\n"

    message += """#fortissimusbellator #breedinglegendaries
#caes #dogs #puppies #pets #doglovers
#instadog #dogsofinstagram #puppiesofinstagram
#workingdogs #dogtraining #dogbreeding #dogbreeder
#dogsofportugal #caesdeportugal"""

    if animal.breed.name_pt:
        message += f"\n#{slugify(animal.breed.name_pt).replace('-', '')}"

    if animal.breed.name_en:
        message += f" #{slugify(animal.breed.name_en).replace('-', '')}"

    image_url = get_animal_image_url(animal)

    results = {}

    fb_ok, fb_res = publish_to_facebook(message, image_url)
    results["facebook"] = "Success" if fb_ok else f"Error: {fb_res}"

    if image_url:
        ig_ok, ig_res = publish_to_instagram(message, image_url)
        results["instagram"] = "Success" if ig_ok else f"Error: {ig_res}"
    else:
        results["instagram"] = "Skipped (no image)"

    return results
