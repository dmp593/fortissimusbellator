from breeding.models import Breed


def breeds(request):
    return {'breeds': Breed.specific.all()}


def theme_context(request):
    # Get the theme from the cookie, defaulting to 'light' if not set
    theme = request.COOKIES.get('theme', 'light')

    return {'theme': theme}
