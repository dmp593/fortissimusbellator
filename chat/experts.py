"""Specialized answer strategies used by the deterministic router."""

from dataclasses import dataclass

from django.conf import settings
from django.db.models import Q
from django.template.defaultfilters import striptags
from django.utils import formats, timezone
from django.utils.translation import gettext as _

from frontoffice.models import FrequentlyAskedQuestion

from .business import ADDRESS, CONTACT_EMAIL, CONTACT_PHONES
from .catalog import available_dogs, current_litters
from .domain import ChatReply, ChatRequest, EntityKind, QueryAnalysis
from .intents import (
    AVAILABLE_DOGS,
    CONTACT,
    CURRENT_LITTERS,
    CURRENT_PAGE,
    ENTITY_INFO,
    FAQS,
    GREETING,
    LOCATION,
    PRICING,
    VISIT,
)
from .knowledge import build_knowledge, matching_faq


@dataclass(frozen=True)
class ExpertContext:
    request: ChatRequest
    analysis: QueryAnalysis


class ResponseComposer:
    """Combine independent deterministic answers without repeating text."""

    @staticmethod
    def compose(replies, fallback_state):
        texts = list(dict.fromkeys(reply.text for reply in replies if reply.text))
        state = next(
            (reply.state for reply in replies if reply.state.has_entity),
            fallback_state,
        )
        return ChatReply(text="\n\n".join(texts), state=state)


class GreetingExpert:
    def answer(self, context):
        if context.analysis.intents == {GREETING}:
            return ChatReply(
                text=_("Hello! How can I help you today?"),
                state=context.request.state,
            )
        return None


class PageExpert:
    def answer(self, context):
        if CURRENT_PAGE not in context.analysis.intents:
            return None

        title = context.request.page_context.get("page_title", "")
        title = self._clean_title(title)
        if not title:
            return ChatReply(
                text=_("I cannot identify the current page."),
                state=context.request.state,
            )
        return ChatReply(
            text=_("You are on the “%(page)s” page.") % {"page": title},
            state=context.request.state,
        )

    @staticmethod
    def _clean_title(value):
        parts = [part.strip() for part in value.split("|") if part.strip()]
        if len(parts) > 1:
            parts = [
                part for part in parts
                if part.casefold() != "fortissimus bellator"
            ]
        return " | ".join(parts)[:150]


class EntityExpert:
    def answer(self, context):
        resolution = context.analysis.entities
        if not resolution.matches:
            return None

        if resolution.ambiguous:
            choices = ", ".join(
                f"{match.name} ({self._kind_label(match.kind)})"
                for match in resolution.matches
            )
            return ChatReply(
                text=_("Did you mean: %(choices)s?") % {"choices": choices},
                state=context.request.state,
            )

        answers = [
            self._describe(match, context.analysis.intents)
            for match in resolution.matches
        ]
        state = (
            resolution.matches[0].as_state()
            if len(resolution.matches) == 1
            else context.request.state
        )
        return ChatReply(text="\n\n".join(answers), state=state)

    def _describe(self, match, intents):
        if match.kind == EntityKind.DOG and ENTITY_INFO not in intents:
            if AVAILABLE_DOGS in intents:
                return self._dog_availability(match.instance)
            if PRICING in intents:
                return self._dog_price(match.instance)

        handlers = {
            EntityKind.DOG: self._dog,
            EntityKind.LITTER: self._litter,
            EntityKind.BREED: self._breed,
        }
        return handlers[match.kind](match.instance)

    @staticmethod
    def _dog_availability(dog):
        if dog.for_sale and dog.sold_at is None:
            return _("%(name)s is available for %(price)s.") % {
                "name": dog.name,
                "price": _price(dog.current_price_in_euros),
            }
        return _("%(name)s is not currently available for sale.") % {
            "name": dog.name,
        }

    @staticmethod
    def _dog_price(dog):
        if dog.for_sale and dog.sold_at is None:
            return _("%(name)s is listed for %(price)s.") % {
                "name": dog.name,
                "price": _price(dog.current_price_in_euros),
            }
        return _("%(name)s has no current sale price.") % {"name": dog.name}

    @staticmethod
    def _dog(dog):
        lines = [_("About %(name)s:") % {"name": dog.name}]
        lines.append(_("- Breed: %(value)s") % {"value": dog.breed})
        lines.append(_("- Gender: %(value)s") % {
            "value": dog.get_gender_display()
        })
        lines.append(_("- Born: %(value)s") % {"value": _date(dog.birth_date)})

        if dog.description:
            lines.append(_("- Description: %(value)s") % {
                "value": _one_line(striptags(dog.description), 500)
            })
        if dog.has_training:
            lines.append(_("- Training: listed"))

        certifications = list(dog.certifications.all())
        if certifications:
            lines.append(_("- Certifications: %(value)s") % {
                "value": ", ".join(str(item) for item in certifications)
            })

        if dog.for_sale and dog.sold_at is None:
            lines.append(_("- Availability: available for %(price)s") % {
                "price": _price(dog.current_price_in_euros)
            })
        elif dog.for_sale:
            lines.append(_("- Availability: no longer available"))

        from breeding.models import Litter

        litters = list(
            Litter.litters_for_sale
            .exclude(status=Litter.LitterStatus.COMPLETED)
            .filter(Q(father=dog) | Q(mother=dog))
        )
        if litters:
            lines.append(_("- Current litters: %(value)s") % {
                "value": ", ".join(litter.name for litter in litters)
            })
        return "\n".join(lines)

    @staticmethod
    def _litter(litter):
        lines = [_("About the %(name)s litter:") % {"name": litter.name}]
        lines.append(_("- Breed: %(value)s") % {"value": litter.breed})
        lines.append(_("- Status: %(value)s") % {
            "value": litter.get_status_display()
        })
        date_detail = _litter_date(litter)
        if date_detail:
            lines.append(f"- {date_detail}")
        if litter.description:
            lines.append(_("- Description: %(value)s") % {
                "value": _one_line(striptags(litter.description), 500)
            })
        if litter.father:
            lines.append(_("- Father: %(value)s") % {"value": litter.father.name})
        if litter.mother:
            lines.append(_("- Mother: %(value)s") % {"value": litter.mother.name})
        babies = litter.babies or litter.expected_babies
        if babies:
            lines.append(_("- Babies: %(value)s") % {"value": babies})
        return "\n".join(lines)

    @staticmethod
    def _breed(breed):
        lines = [_("About the %(name)s breed:") % {"name": breed}]
        if breed.description:
            lines.append(_one_line(striptags(breed.description), 700))
        else:
            lines.append(_("No additional description is currently published."))
        return "\n".join(lines)

    @staticmethod
    def _kind_label(kind):
        return {
            EntityKind.DOG: _("dog"),
            EntityKind.LITTER: _("litter"),
            EntityKind.BREED: _("breed"),
        }[kind]


class InventoryExpert:
    def answer(self, context):
        intents = set(context.analysis.intents)
        entity_kinds = {match.kind for match in context.analysis.entities.matches}
        if EntityKind.DOG in entity_kinds:
            intents.difference_update({AVAILABLE_DOGS, PRICING})
        if EntityKind.LITTER in entity_kinds:
            intents.discard(CURRENT_LITTERS)

        replies = []
        if AVAILABLE_DOGS in intents:
            replies.append(self._available_dogs(context))
            intents.discard(PRICING)  # Availability already includes prices.
        elif PRICING in intents:
            replies.append(self._prices(context))
        if CURRENT_LITTERS in intents:
            replies.append(self._litters(context))
        return self._compose(replies, context.request.state)

    @staticmethod
    def _available_dogs(context):
        dogs = list(available_dogs()[:settings.CHAT_MAX_KENNEL_ITEMS])
        if not dogs:
            return ChatReply(text=_(
                "No dogs are currently listed for sale. "
                "Contact us for the latest availability."
            ))

        lines = [_('Dogs currently available:')]
        for dog in dogs:
            lines.append(_("- %(name)s — %(breed)s, %(gender)s, %(price)s.") % {
                "name": dog.name,
                "breed": dog.breed,
                "gender": dog.get_gender_display(),
                "price": _price(dog.current_price_in_euros),
            })
        lines.extend(("", _(
            "Contact us for more information or to arrange a visit."
        )))
        return ChatReply(
            text="\n".join(lines),
            state=_dog_state(dogs, context.request.state),
        )

    @staticmethod
    def _prices(context):
        dogs = list(available_dogs()[:settings.CHAT_MAX_KENNEL_ITEMS])
        if not dogs:
            return ChatReply(text=_(
                "No prices are currently listed. "
                "Contact us for the latest information."
            ))

        lines = [_('Current prices:')]
        for dog in dogs:
            lines.append(_("- %(name)s — %(price)s.") % {
                "name": dog.name,
                "price": _price(dog.current_price_in_euros),
            })
        lines.extend(("", _(
            "These are the prices currently shown on the website. "
            "Contact us for details."
        )))
        return ChatReply(
            text="\n".join(lines),
            state=_dog_state(dogs, context.request.state),
        )

    @staticmethod
    def _litters(context):
        litters = list(current_litters()[:settings.CHAT_MAX_KENNEL_ITEMS])
        if not litters:
            return ChatReply(text=_(
                "No current or upcoming litters are listed. "
                "Contact us for the latest information."
            ))

        lines = [_('Current or upcoming litters:')]
        for litter in litters:
            details = [_('%(name)s — %(breed)s; status: %(status)s') % {
                "name": litter.name,
                "breed": litter.breed,
                "status": litter.get_status_display(),
            }]
            date_detail = _litter_date(litter)
            if date_detail:
                details.append(date_detail)
            lines.append("- " + "; ".join(details) + ".")
        lines.extend(("", _("Contact us for more information about a litter.")))

        state = context.request.state
        if len(litters) == 1:
            from .domain import ConversationState

            state = ConversationState(
                entity_kind=EntityKind.LITTER,
                entity_id=litters[0].pk,
                entity_name=litters[0].name,
            )
        return ChatReply(text="\n".join(lines), state=state)

    @staticmethod
    def _compose(replies, fallback_state):
        return (
            ResponseComposer.compose(replies, fallback_state)
            if replies else None
        )


class ContactExpert:
    def answer(self, context):
        intents = context.analysis.intents
        if not intents.intersection({CONTACT, LOCATION, VISIT}):
            return None

        lines = []
        if LOCATION in intents:
            lines.append(_("We are at %(address)s.") % {"address": ADDRESS})
        if CONTACT in intents:
            lines.append(_(
                "You can contact us on %(phones)s or at %(email)s."
            ) % {"phones": CONTACT_PHONES, "email": CONTACT_EMAIL})
        if VISIT in intents:
            lines.append(_(
                "Visits are arranged in advance. Contact us on "
                "%(phones)s to schedule one."
            ) % {"phones": CONTACT_PHONES})
        return ChatReply(text="\n".join(lines), state=context.request.state)


class FaqExpert:
    def answer(self, context):
        faq = matching_faq(context.request.message)
        if faq is not None:
            return ChatReply(text=faq.answer.strip(), state=context.request.state)

        if FAQS not in context.analysis.intents:
            return None
        faqs = FrequentlyAskedQuestion.objects.filter(active=True).order_by("order")[
            :settings.CHAT_MAX_FAQS
        ]
        if not faqs:
            return ChatReply(text=_(
                "No frequently asked questions are currently listed. "
                "Please contact us at %(phones)s."
            ) % {"phones": CONTACT_PHONES})
        lines = [_('Frequently asked questions:')]
        lines.extend(f"- {faq.question}" for faq in faqs)
        lines.extend(("", _("Ask me one of these questions to see the answer.")))
        return ChatReply(text="\n".join(lines), state=context.request.state)


class LocalModelExpert:
    """The only expert that invokes the local language model."""

    def __init__(self, assistant):
        self.assistant = assistant

    def answer(self, context):
        knowledge = build_knowledge(
            context.request.message,
            context.request.page_context,
        )
        focus = (
            "dog buying and breed guidance"
            if context.analysis.intents.intersection(
                {AVAILABLE_DOGS, CURRENT_LITTERS, PRICING}
            )
            else "general kennel and dog buying questions"
        )
        text = self.assistant.reply(
            context.request.history,
            context.request.message,
            context.request.language,
            knowledge,
            focus,
        )
        return ChatReply(text=text, state=context.request.state)


def _dog_state(dogs, fallback):
    if len(dogs) != 1:
        return fallback
    from .domain import ConversationState

    return ConversationState(
        entity_kind=EntityKind.DOG,
        entity_id=dogs[0].pk,
        entity_name=dogs[0].name,
    )


def _price(value):
    if value is None:
        return _("price on request")
    amount = formats.number_format(
        value, decimal_pos=2, use_l10n=True, force_grouping=True
    )
    return _("€%(amount)s") % {"amount": amount}


def _date(value):
    return formats.date_format(value, "DATE_FORMAT")


def _litter_date(litter):
    today = timezone.localdate()
    if litter.ready_date:
        return _("ready since: %(date)s") % {"date": _date(litter.ready_date)}
    if litter.birth_date:
        return _("born: %(date)s") % {"date": _date(litter.birth_date)}
    if litter.expected_birth_date and litter.expected_birth_date >= today:
        return _("expected birth: %(date)s") % {
            "date": _date(litter.expected_birth_date)
        }
    if litter.expected_ready_date and litter.expected_ready_date >= today:
        return _("expected ready: %(date)s") % {
            "date": _date(litter.expected_ready_date)
        }
    return ""


def _one_line(value, limit):
    return " ".join(str(value).split())[:limit]
