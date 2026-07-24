"""Classify lightweight chat intents and assemble query analysis."""

from .domain import EntityKind, QueryAnalysis
from .matching import contains_phrase, normalize_text, same_word, words


AVAILABLE_ANIMALS = "available_animals"
AVAILABLE_LITTERS = "available_litters"
AVAILABILITY = "availability"
CURRENT_LITTERS = "current_litters"
PRICING = "pricing"
FAQS = "faqs"
BLOG = "blog"
CURRENT_PAGE = "current_page"
CONTACT = "contact"
LOCATION = "location"
VISIT = "visit"
GREETING = "greeting"
ENTITY_INFO = "entity_info"
CERTIFICATIONS = "certifications"

QUICK_INTENTS = frozenset({
    AVAILABLE_ANIMALS,
    AVAILABLE_LITTERS,
    CERTIFICATIONS,
    CURRENT_LITTERS,
    PRICING,
    FAQS,
})

INTENT_PHRASES = {
    CERTIFICATIONS: (
        "certification", "certifications", "certificate", "certificates",
        "certificacao", "certificacoes", "certificado", "certificados",
        "certificacion", "certificaciones", "certificat", "certificats",
        "zertifizierung", "zertifizierungen", "zertifikat", "zertifikate",
        "certificazione", "certificazioni",
    ),
    BLOG: (
        "blog", "post", "posts", "article", "articles", "artigo",
        "artigos", "publicacao", "publicacoes", "publicacion",
        "publicaciones", "article de blog", "articles de blog",
        "beitrag", "beitrage", "articolo", "articoli",
    ),
    FAQS: (
        "faq", "frequently asked", "perguntas frequentes",
        "preguntas frecuentes", "questions frequentes",
        "haufig gestellte", "domande frequenti",
    ),
    PRICING: (
        "price", "pricing", "cost", "preco", "quanto custa",
        "quanto custam", "precio", "cuanto cuesta", "prix", "cout",
        "combien coute", "preis", "kosten", "wie viel kostet", "prezzo",
        "quanto costa",
    ),
    CURRENT_LITTERS: (
        "current litter", "upcoming litter", "ninhada", "ninhadas",
        "camada", "camadas", "portee", "portees", "wurf", "wurfe",
        "cucciolata", "cucciolate",
    ),
    CURRENT_PAGE: (
        "what page am i on", "which page am i on", "current page",
        "que pagina estou", "em que pagina estou", "onde estou no site",
        "en que pagina estoy", "quelle page", "auf welcher seite",
        "in quale pagina",
    ),
    CONTACT: (
        "contact", "phone", "telephone", "email", "contacto",
        "telefone", "telemovel", "correo", "telefono", "contacter",
        "kontakt", "contatto",
    ),
    LOCATION: (
        "where are you located", "your address", "location", "morada",
        "onde ficam", "onde estao", "localizacao", "direccion",
        "adresse", "standort", "indirizzo",
    ),
    VISIT: (
        "arrange a visit", "book a visit", "visit you", "marcar visita",
        "agendar visita", "visitar", "concertar una visita", "visite",
        "besuch", "visita",
    ),
    ENTITY_INFO: (
        "what do you know about", "tell me about", "who is",
        "o que sabes sobre", "fala me de", "informacoes sobre",
        "que sabes de", "parle moi de", "erzahl mir", "parlami di",
    ),
}

ANIMAL_WORDS = (
    "animal", "animals", "pet", "pets", "animais", "animal de estimacao",
    "mascota", "mascotas", "animaux", "haustier", "haustiere", "animali",
)
AVAILABLE_WORDS = (
    "available", "for sale", "disponivel", "disponiveis", "venda",
    "disponible", "disponibles", "verfugbar", "verkauf",
    "disponibile", "disponibili",
)
PURCHASE_WORDS = (
    "buy", "purchase", "acquire", "reserve",
    "comprar", "adquirir", "reservar",
    "acheter", "acquerir", "reserver",
    "kaufen", "reservieren",
    "comprare", "acquistare", "prenotare",
)
LITTER_WORDS = (
    "litter", "litters", "ninhada", "ninhadas", "camada", "camadas",
    "portee", "portees", "wurf", "wurfe", "cucciolata", "cucciolate",
)
EXPLICIT_CURRENT_WORDS = (
    "current", "upcoming", "atual", "atuais", "proxima", "proximas",
    "actual", "actuales", "actuel", "prochain", "aktuell", "kommend",
    "attuale", "prossima",
)
REFERENCE_WORDS = (
    "he", "she", "it", "this animal", "that animal", "this pet", "him", "her",
    "this dog", "that dog",
    "ele", "ela", "este animal", "esse animal", "este cao", "esta cadela",
    "esse cao", "essa cadela",
    "este", "esta", "esse", "essa", "el", "ella", "il", "elle", "ce chien",
    "cette chienne", "er", "sie", "dieser hund", "lui", "lei",
)
GREETINGS = frozenset({
    "hi", "hello", "hey", "ola", "bom dia", "boa tarde", "boa noite",
    "hola", "bonjour", "salut", "hallo", "guten tag", "ciao",
    "buongiorno",
})


def is_blog_query(message):
    """Return whether a message explicitly asks about published articles."""
    return contains_phrase(message, INTENT_PHRASES[BLOG])


def is_certification_query(message):
    """Return whether a message explicitly asks about certifications."""
    return contains_phrase(message, INTENT_PHRASES[CERTIFICATIONS])


class IntentDetector:
    def detect(self, message, requested_intent=None):
        intents = set()
        if requested_intent in QUICK_INTENTS:
            intents.add(requested_intent)

        normalized = normalize_text(message)
        if normalized in GREETINGS:
            intents.add(GREETING)

        for intent, phrases in INTENT_PHRASES.items():
            if contains_phrase(message, phrases):
                intents.add(intent)

        query_words = words(message)
        has_availability_word = contains_phrase(message, AVAILABLE_WORDS)
        has_purchase_word = self._contains_exact_word(
            query_words,
            PURCHASE_WORDS,
        )
        asks_availability = has_availability_word or has_purchase_word
        if asks_availability:
            intents.add(AVAILABILITY)

        has_animal_word = self._contains_word(query_words, ANIMAL_WORDS)
        if has_animal_word and asks_availability:
            intents.add(AVAILABLE_ANIMALS)
        has_litter_word = self._contains_word(query_words, LITTER_WORDS)
        asks_current_litters = has_litter_word and self._contains_word(
            query_words,
            EXPLICIT_CURRENT_WORDS,
        )
        if has_litter_word:
            if asks_availability and not (
                has_animal_word and asks_current_litters
            ):
                intents.add(AVAILABLE_LITTERS)
                intents.discard(CURRENT_LITTERS)
            else:
                intents.add(CURRENT_LITTERS)
        return frozenset(intents)

    @staticmethod
    def has_reference(message):
        return contains_phrase(message, REFERENCE_WORDS, threshold=0.92)

    @staticmethod
    def has_generic_animal_word(message):
        return IntentDetector._contains_word(words(message), ANIMAL_WORDS)

    @staticmethod
    def has_generic_litter_word(message):
        return IntentDetector._contains_word(words(message), LITTER_WORDS)

    @staticmethod
    def asks_current_litters(message):
        query_words = words(message)
        return (
            IntentDetector._contains_word(query_words, LITTER_WORDS)
            and IntentDetector._contains_word(
                query_words,
                EXPLICIT_CURRENT_WORDS,
            )
        )

    @staticmethod
    def has_availability_word(message):
        query_words = words(message)
        return (
            contains_phrase(message, AVAILABLE_WORDS)
            or IntentDetector._contains_exact_word(
                query_words,
                PURCHASE_WORDS,
            )
        )

    @staticmethod
    def _contains_exact_word(query_words, candidates):
        candidate_words = {
            word
            for candidate in candidates
            for word in words(candidate)
        }
        return any(query_word in candidate_words for query_word in query_words)

    @staticmethod
    def _contains_word(query_words, candidates):
        candidate_words = [word for candidate in candidates for word in words(candidate)]
        return any(
            same_word(query_word, candidate)
            for query_word in query_words
            for candidate in candidate_words
        )


class QueryAnalyzer:
    def __init__(self, entity_resolver, intent_detector=None):
        self.entity_resolver = entity_resolver
        self.intent_detector = intent_detector or IntentDetector()

    def analyze(self, request):
        intents = self.intent_detector.detect(
            request.message, request.requested_intent
        )
        entities = self.entity_resolver.resolve_explicit(request.message)
        used_state = False

        if (
            any(
                match.kind in {EntityKind.ANIMAL, EntityKind.ANIMAL_KIND}
                for match in entities.matches
            )
            and self.intent_detector.has_availability_word(request.message)
        ):
            intents = intents.union({AVAILABILITY, AVAILABLE_ANIMALS})
        if (
            any(
                match.kind == EntityKind.ANIMAL_KIND
                for match in entities.matches
            )
            and self.intent_detector.asks_current_litters(request.message)
        ):
            intents = intents.difference({AVAILABLE_LITTERS}).union({
                CURRENT_LITTERS,
            })
        if (
            any(match.kind == EntityKind.LITTER for match in entities.matches)
            and self.intent_detector.has_availability_word(request.message)
        ):
            intents = intents.union({AVAILABILITY, AVAILABLE_LITTERS})

        if not entities.matches and self._needs_context(request.message, intents):
            entities = self.entity_resolver.resolve_page(request.page_context)
            if not entities.matches:
                entities = self.entity_resolver.resolve_state(request.state)
                used_state = bool(entities.matches)

        return QueryAnalysis(
            intents=intents,
            entities=entities,
            used_conversation_state=used_state,
        )

    def _needs_context(self, message, intents):
        if self.intent_detector.has_reference(message):
            return True
        if ENTITY_INFO in intents:
            return True
        has_animal_word = self.intent_detector.has_generic_animal_word(message)
        has_litter_word = self.intent_detector.has_generic_litter_word(message)
        if PRICING in intents and not has_animal_word:
            return True
        return bool(
            intents.intersection({AVAILABILITY, AVAILABLE_ANIMALS})
            and not has_animal_word
            and not has_litter_word
        )
