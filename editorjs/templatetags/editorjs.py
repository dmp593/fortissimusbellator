from django import template
from django.template.loader import get_template
from django.utils.safestring import mark_safe


register = template.Library()


@register.filter(name='editorjs_render')
def editorjs_render(content):
    if not content or 'blocks' not in content:
        return ''
    
    output = []
    for block in content['blocks']:
        template_name = f"editorjs/{block['type'].lower()}.html"
        context = {'data': block['data'], 'block_type': block['type']}
        
        try:
            tpl = get_template(template_name)
            output.append(tpl.render(context))
        except template.TemplateDoesNotExist:
            output.append(f"<!-- Unsupported block: {block['type']} -->")
    
    return mark_safe(''.join(output))
