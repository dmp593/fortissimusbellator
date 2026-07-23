from django import forms
from django.utils.translation import gettext_lazy as _

from discounts.models import Promotion


class PromotionAdminForm(forms.ModelForm):
    SCOPE_RELATION_FIELDS = {
        Promotion.Scope.BREEDS: 'breeds',
        Promotion.Scope.SPECIFIC_DOGS: 'dogs',
        Promotion.Scope.SPECIFIC_LITTERS: 'litters',
    }

    class Meta:
        model = Promotion
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['scope'].help_text = _(
            'Global scopes need no selection. For a selected scope, use only '
            'the matching field shown below.'
        )
        relation_help_texts = {
            'breeds': _('Required only for the selected breeds scope.'),
            'dogs': _('Required only for the selected dogs scope.'),
            'litters': _('Required only for the selected litters scope.'),
        }
        for relation, help_text in relation_help_texts.items():
            field = self.fields[relation]
            field.help_text = help_text
            field.widget.attrs['data-promotion-scope-field'] = relation

    def clean(self):
        cleaned_data = super().clean()
        scope = cleaned_data.get('scope')
        required_relation = self.SCOPE_RELATION_FIELDS.get(scope)

        for relation in self.SCOPE_RELATION_FIELDS.values():
            selected = cleaned_data.get(relation)
            if relation == required_relation and selected is not None and not selected:
                self.add_error(
                    relation,
                    _('Select at least one item for this promotion scope.'),
                )
            elif relation != required_relation:
                # A scope change must not retain hidden, inapplicable targets.
                cleaned_data[relation] = self.fields[relation].queryset.none()
        return cleaned_data
