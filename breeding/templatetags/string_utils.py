from django import template


register = template.Library()


@register.filter
def startswith(value, prefix):
    return value.lower().startswith(prefix)


@register.filter
def endswith(value, suffix):
    return value.lower().endswith(suffix)
