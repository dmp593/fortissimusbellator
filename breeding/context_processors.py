from breeding.models import Breed


def featured_breeds(request):
    return {
        'featured_breeds': Breed.objects_specific_featured.all()
    }
