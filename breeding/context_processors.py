from breeding.models import Breed


def breeds(request):
    return {
        'breeds': Breed.objects_specific_featured.all()
    }
