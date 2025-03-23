from breeding.models import Breed


def breeds(request):
    return {
        'breeds': Breed.specific.all()
    }
