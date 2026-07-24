"""Specialized answer strategies used by the deterministic router."""

import logging
from dataclasses import dataclass

from django.conf import settings
from django.db.models import Q
from django.template.defaultfilters import striptags
from django.utils import formats, timezone
from django.utils.translation import gettext as _

from reservations.availability import (
    dog_unavailability_reason,
)

from .business import ADDRESS, CONTACT_EMAIL, CONTACT_PHONES
from .catalog import (
    available_animals,
    current_litters,
    public_certifications,
    public_faqs,
    published_posts,
)
from .domain import ChatReply, ChatRequest, EntityKind, QueryAnalysis
from .intents import (
    AVAILABILITY,
    AVAILABLE_ANIMALS,
    AVAILABLE_LITTERS,
    BLOG,
    CERTIFICATIONS,
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
from .knowledge import (
    build_knowledge_snapshot,
    matching_faq,
    relevant_faqs,
)
from .response_policy import has_unsupported_urls


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


class BlogExpert:
    """List only real, currently published blog titles."""

    def answer(self, context):
        if BLOG not in context.analysis.intents:
            return None

        posts = list(published_posts()[:settings.CHAT_MAX_BLOG_POSTS])
        if not posts:
            return ChatReply(
                text=_("No blog posts are currently published."),
                state=context.request.state,
            )

        lines = [_("Published blog posts:")]
        lines.extend(f"- {post.title}" for post in posts)
        return ChatReply(
            text="\n".join(lines),
            state=context.request.state,
        )


class EntityExpert:
    def answer(self, context):
        resolution = context.analysis.entities
        if not resolution.matches:
            return None

        if resolution.ambiguous:
            choices = ", ".join(
                f"{match.name} ({self._entity_label(match)})"
                for match in resolution.matches
            )
            return ChatReply(
                text=_("Did you mean: %(choices)s?") % {"choices": choices},
                state=context.request.state,
            )

        answers = []
        for match in resolution.matches:
            answer = self._describe(match, context.analysis.intents)
            if answer:
                answers.append(answer)
        if not answers:
            return None
        state = (
            resolution.matches[0].as_state()
            if len(resolution.matches) == 1
            else context.request.state
        )
        return ChatReply(text="\n\n".join(answers), state=state)

    def _describe(self, match, intents):
        if match.kind == EntityKind.ANIMAL and ENTITY_INFO not in intents:
            if intents.intersection({AVAILABILITY, AVAILABLE_ANIMALS}):
                return self._animal_availability(match.instance)
            if PRICING in intents:
                return self._animal_price(match.instance)
        if match.kind == EntityKind.ANIMAL_KIND:
            return None
        if match.kind in {EntityKind.CERTIFICATION, EntityKind.FAQ}:
            return None
        if (
            match.kind == EntityKind.LITTER
            and ENTITY_INFO not in intents
            and intents.intersection({AVAILABILITY, AVAILABLE_LITTERS})
        ):
            return self._litter_availability(match.instance)

        handlers = {
            EntityKind.ANIMAL: self._animal,
            EntityKind.LITTER: self._litter,
            EntityKind.BREED: self._breed,
        }
        return handlers[match.kind](match.instance)

    @staticmethod
    def _animal_availability(animal):
        reason = dog_unavailability_reason(animal)
        if reason is None:
            return _("%(name)s is available for %(price)s.") % {
                "name": animal.name,
                "price": _price(animal.current_price_in_euros),
            }
        return _("%(name)s is not currently available. %(reason)s") % {
            "name": animal.name,
            "reason": reason,
        }

    @staticmethod
    def _animal_price(animal):
        if animal.for_sale and not animal.is_sold:
            answer = _("%(name)s is listed for %(price)s.") % {
                "name": animal.name,
                "price": _price(animal.current_price_in_euros),
            }
            reason = dog_unavailability_reason(animal)
            if reason:
                answer += " " + _(
                    "It cannot currently be pre-reserved. %(reason)s"
                ) % {"reason": reason}
            return answer
        return _("%(name)s has no current sale price.") % {
            "name": animal.name,
        }

    @staticmethod
    def _animal(animal):
        lines = [_("About %(name)s:") % {"name": animal.name}]
        lines.append(_("- Animal kind: %(value)s") % {
            "value": animal.breed.kind,
        })
        lines.append(_("- Breed: %(value)s") % {"value": animal.breed})
        lines.append(_("- Gender: %(value)s") % {
            "value": animal.get_gender_display()
        })
        lines.append(_("- Born: %(value)s") % {
            "value": _date(animal.birth_date),
        })

        if animal.description:
            lines.append(_("- Description: %(value)s") % {
                "value": _one_line(striptags(animal.description), 500)
            })
        if animal.has_training:
            lines.append(_("- Training: listed"))

        certification_codes = _animal_certification_codes(animal)
        if certification_codes:
            lines.append(_("- Certifications: %(value)s") % {
                "value": ", ".join(certification_codes),
            })

        unavailability_reason = dog_unavailability_reason(animal)
        if unavailability_reason is None:
            lines.append(_("- Availability: available for %(price)s") % {
                "price": _price(animal.current_price_in_euros)
            })
        else:
            lines.append(_("- Availability: %(reason)s") % {
                "reason": unavailability_reason,
            })

        from breeding.models import Litter

        litters = list(
            Litter.litters_for_sale
            .exclude(status=Litter.LitterStatus.COMPLETED)
            .filter(Q(father=animal) | Q(mother=animal))
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
        lines.append(_("- Birth updates: %(value)s") % {
            "value": _litter_availability_detail(litter),
        })
        return "\n".join(lines)

    @staticmethod
    def _litter_availability(litter):
        return _(
            "%(name)s cannot be pre-reserved. Subscribe to its birth alert; "
            "individual dogs can be pre-reserved after publication."
        ) % {"name": litter.name}

    @staticmethod
    def _breed(breed):
        lines = [_("About the %(name)s breed:") % {"name": breed}]
        if breed.description:
            lines.append(_one_line(striptags(breed.description), 700))
        else:
            lines.append(_("No additional description is currently published."))
        return "\n".join(lines)

    @staticmethod
    def _entity_label(match):
        if match.kind == EntityKind.ANIMAL:
            return str(match.instance.breed.kind)
        return {
            EntityKind.ANIMAL_KIND: _("animal kind"),
            EntityKind.LITTER: _("litter"),
            EntityKind.BREED: _("breed"),
            EntityKind.CERTIFICATION: _("certification"),
            EntityKind.FAQ: _("frequently asked question"),
        }[match.kind]


class CertificationExpert:
    """Render certification facts directly from the public database."""

    def answer(self, context):
        resolution = context.analysis.entities
        if resolution.ambiguous:
            return None

        matched = [
            match.instance
            for match in resolution.matches
            if match.kind == EntityKind.CERTIFICATION
        ]
        if matched:
            return ChatReply(
                text="\n\n".join(
                    self._certification_detail(certification)
                    for certification in matched
                ),
                state=resolution.matches[0].as_state(),
            )

        if CERTIFICATIONS not in context.analysis.intents:
            return None

        certifications = list(
            public_certifications()[:settings.CHAT_MAX_CERTIFICATIONS]
        )
        if not certifications:
            return ChatReply(
                text=_("No certifications are currently published."),
                state=context.request.state,
            )

        lines = [_("Published certifications:")]
        lines.extend(
            _("- %(code)s — %(name)s.") % {
                "code": certification.code,
                "name": certification.name,
            }
            for certification in certifications
        )
        lines.extend(("", _(
            "Ask about a certification code to see its details."
        )))
        return ChatReply(
            text="\n".join(lines),
            state=context.request.state,
        )

    @staticmethod
    def _certification_detail(certification):
        lines = [
            _("About certification %(code)s:") % {
                "code": certification.code,
            },
            _("- Name: %(value)s") % {"value": certification.name},
        ]
        if certification.parent:
            lines.append(_("- Parent certification: %(value)s") % {
                "value": (
                    f"{certification.parent.code} — "
                    f"{certification.parent.name}"
                ),
            })
        if certification.description:
            lines.append(_("- Description: %(value)s") % {
                "value": _one_line(
                    striptags(certification.description),
                    900,
                ),
            })
        return "\n".join(lines)


class InventoryExpert:
    def answer(self, context):
        intents = set(context.analysis.intents)
        entity_kinds = {match.kind for match in context.analysis.entities.matches}
        if EntityKind.ANIMAL in entity_kinds:
            intents.difference_update({
                AVAILABILITY,
                AVAILABLE_ANIMALS,
                PRICING,
            })
        if EntityKind.LITTER in entity_kinds:
            intents.difference_update({
                AVAILABILITY,
                AVAILABLE_LITTERS,
                CURRENT_LITTERS,
            })

        replies = []
        specific_availability = intents.intersection({
            AVAILABLE_ANIMALS,
            AVAILABLE_LITTERS,
        })
        if AVAILABILITY in intents and not specific_availability:
            replies.append(self._available_animals(context))
            replies.append(self._available_litters(context))
            intents.discard(PRICING)
        elif AVAILABLE_ANIMALS in intents:
            replies.append(self._available_animals(context))
            intents.discard(PRICING)  # Availability already includes prices.
        elif PRICING in intents:
            replies.append(self._prices(context))
        if AVAILABLE_LITTERS in intents:
            replies.append(self._available_litters(context))
        elif CURRENT_LITTERS in intents:
            replies.append(self._litters(context))
        return self._compose(replies, context.request.state)

    @staticmethod
    def _available_animals(context):
        animal_kind_id = _resolved_animal_kind_id(context.analysis)
        animals = list(
            available_animals(animal_kind_id=animal_kind_id)[
                :settings.CHAT_MAX_KENNEL_ITEMS
            ]
        )
        if not animals:
            return ChatReply(text=_(
                "No animals are currently listed for sale. "
                "Contact us for the latest availability."
            ))

        lines = [_('Animals currently available:')]
        for animal in animals:
            lines.append(_(
                "- %(name)s — %(kind)s; %(breed)s, %(gender)s, %(price)s."
            ) % {
                "name": animal.name,
                "kind": animal.breed.kind,
                "breed": animal.breed,
                "gender": animal.get_gender_display(),
                "price": _price(animal.current_price_in_euros),
            })
            certification_codes = _animal_certification_codes(animal)
            if certification_codes:
                lines.append(_("- Certifications: %(value)s") % {
                    "value": ", ".join(certification_codes),
                })
        lines.extend(("", _(
            "Contact us for more information or to arrange a visit."
        )))
        return ChatReply(
            text="\n".join(lines),
            state=_animal_state(animals, context.request.state),
        )

    @staticmethod
    def _prices(context):
        animal_kind_id = _resolved_animal_kind_id(context.analysis)
        animals = list(
            available_animals(animal_kind_id=animal_kind_id)[
                :settings.CHAT_MAX_KENNEL_ITEMS
            ]
        )
        if not animals:
            return ChatReply(text=_(
                "No prices are currently listed. "
                "Contact us for the latest information."
            ))

        lines = [_('Current prices:')]
        for animal in animals:
            lines.append(_("- %(name)s — %(price)s.") % {
                "name": animal.name,
                "price": _price(animal.current_price_in_euros),
            })
        lines.extend(("", _(
            "These are the prices currently shown on the website. "
            "Contact us for details."
        )))
        return ChatReply(
            text="\n".join(lines),
            state=_animal_state(animals, context.request.state),
        )

    @staticmethod
    def _litters(context):
        animal_kind_id = _resolved_animal_kind_id(context.analysis)
        litters = list(
            current_litters(animal_kind_id=animal_kind_id)[
                :settings.CHAT_MAX_KENNEL_ITEMS
            ]
        )
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
            details.append(_("birth updates: %(availability)s") % {
                "availability": _litter_availability_detail(litter),
            })
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
    def _available_litters(context):
        return ChatReply(
            text=_(
                'Litters cannot be pre-reserved. You can subscribe to a '
                'litter birth alert, then pre-reserve an individual dog '
                'after it is published.'
            ),
            state=context.request.state,
        )

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
        resolution = context.analysis.entities
        if resolution.ambiguous:
            return None

        indexed_faqs = [
            match.instance
            for match in resolution.matches
            if match.kind == EntityKind.FAQ
        ]
        allow_related = not (
            context.analysis.intents - {FAQS}
        )
        faq = (
            indexed_faqs[0]
            if len(indexed_faqs) == 1
            else matching_faq(
                context.request.message,
                allow_related=allow_related,
            )
        )
        if faq is not None:
            return ChatReply(text=faq.answer.strip(), state=context.request.state)

        if allow_related:
            related_faqs = relevant_faqs(context.request.message)
            if related_faqs:
                return ChatReply(
                    text="\n\n".join(
                        f"{related.question}\n{related.answer.strip()}"
                        for related in related_faqs
                    ),
                    state=context.request.state,
                )

        if FAQS not in context.analysis.intents:
            return None
        faqs = list(public_faqs()[:settings.CHAT_MAX_FAQS])
        if not faqs:
            return ChatReply(text=_(
                "No frequently asked questions are currently listed. "
                "Please contact us at %(phones)s."
            ) % {"phones": CONTACT_PHONES})
        lines = [_('Frequently asked questions:')]
        lines.extend(f"- {faq.question}" for faq in faqs)
        lines.extend(("", _("Ask me one of these questions to see the answer.")))
        return ChatReply(text="\n".join(lines), state=context.request.state)


class KnowledgeBoundaryExpert:
    """Answer safely when no published fact can ground a model response."""

    def answer(self, context):
        return ChatReply(
            text=_(
                "I can only answer using information published by "
                "Fortissimus Bellator. I do not have information about that. "
                "You can ask about animals, litters, prices, blog posts, visits, "
                "certifications, or contact details."
            ),
            state=context.request.state,
        )


class LocalModelExpert:
    """The only expert that invokes the local language model."""

    def __init__(self, assistant):
        self.assistant = assistant

    def answer(self, context):
        knowledge = build_knowledge_snapshot(
            context.request.message,
            context.request.page_context,
        )
        if not knowledge.has_query_facts:
            return None
        focus = (
            "animal certification explanations"
            if (
                CERTIFICATIONS in context.analysis.intents
                or any(
                    match.kind == EntityKind.CERTIFICATION
                    for match in context.analysis.entities.matches
                )
            )
            else "animal buying and breed guidance"
            if context.analysis.intents.intersection(
                {
                    AVAILABILITY,
                    AVAILABLE_ANIMALS,
                    AVAILABLE_LITTERS,
                    CURRENT_LITTERS,
                    PRICING,
                }
            )
            else "general animal and breeding questions"
        )
        text = self.assistant.reply(
            context.request.history,
            context.request.message,
            context.request.language,
            knowledge.text,
            focus,
        )
        if has_unsupported_urls(text, knowledge.text):
            logging.getLogger(__name__).warning(
                "chat_model_response_rejected reason=unsupported_url"
            )
            return None
        return ChatReply(text=text, state=context.request.state)


def _animal_state(animals, fallback):
    if len(animals) != 1:
        return fallback
    from .domain import ConversationState

    return ConversationState(
        entity_kind=EntityKind.ANIMAL,
        entity_id=animals[0].pk,
        entity_name=animals[0].name,
    )


def _animal_certification_codes(animal):
    return [
        item.certification.code
        for item in animal.animal_certifications.all()
    ]


def _resolved_animal_kind_id(analysis):
    matches = [
        match
        for match in analysis.entities.matches
        if match.kind == EntityKind.ANIMAL_KIND
    ]
    return matches[0].instance.pk if len(matches) == 1 else None


def _price(value):
    if value is None:
        return _("price on request")
    amount = formats.number_format(
        value, decimal_pos=2, use_l10n=True, force_grouping=True
    )
    return _("€%(amount)s") % {"amount": amount}


def _date(value):
    return formats.date_format(value, "DATE_FORMAT")


def _litter_availability_detail(litter):
    return _(
        'subscribe to receive an email when the litter is born; individual '
        'dogs can be pre-reserved after publication'
    )


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
