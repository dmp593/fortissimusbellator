"""Classify lightweight chat intents and assemble query analysis."""

from .domain import EntityKind, QueryAnalysis
from .matching import contains_phrase, normalize_text, same_word, words


AVAILABLE_DOGS = "available_dogs"
CURRENT_LITTERS = "current_litters"
PRICING = "pricing"
FAQS = "faqs"
CURRENT_PAGE = "current_page"
CONTACT = "contact"
LOCATION = "location"
VISIT = "visit"
GREETING = "greeting"
ENTITY_INFO = "entity_info"

QUICK_INTENTS = frozenset({AVAILABLE_DOGS, CURRENT_LITTERS, PRICING, FAQS})

INTENT_PHRASES = {
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

DOG_WORDS = (
    "dog", "dogs", "puppy", "puppies", "cao", "caes", "cachorro",
    "cachorros", "perro", "perros", "chien", "chiens", "hund",
    "hunde", "cane", "cani",
)
AVAILABLE_WORDS = (
    "available", "for sale", "disponivel", "disponiveis", "venda",
    "disponible", "disponibles", "verfugbar", "verkauf",
    "disponibile", "disponibili",
)
LITTER_WORDS = (
    "litter", "litters", "ninhada", "ninhadas", "camada", "camadas",
    "portee", "portees", "wurf", "wurfe", "cucciolata", "cucciolate",
)
CURRENT_WORDS = (
    "current", "upcoming", "available", "atual", "atuais", "proxima",
    "proximas", "actual", "actuales", "actuel", "prochain", "aktuell",
    "kommend", "attuale", "prossima",
)
REFERENCE_WORDS = (
    "he", "she", "it", "this dog", "that dog", "him", "her",
    "ele", "ela", "este cao", "esta cadela", "esse cao", "essa cadela",
    "este", "esta", "esse", "essa", "el", "ella", "il", "elle", "ce chien",
    "cette chienne", "er", "sie", "dieser hund", "lui", "lei",
)
GREETINGS = frozenset({
    "hi", "hello", "hey", "ola", "bom dia", "boa tarde", "boa noite",
    "hola", "bonjour", "salut", "hallo", "guten tag", "ciao",
    "buongiorno",
})


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
        has_dog_word = self._contains_word(query_words, DOG_WORDS)
        if has_dog_word and contains_phrase(message, AVAILABLE_WORDS):
            intents.add(AVAILABLE_DOGS)
        has_litter_word = self._contains_word(query_words, LITTER_WORDS)
        if has_litter_word and self._contains_word(query_words, CURRENT_WORDS):
            intents.add(CURRENT_LITTERS)
        return frozenset(intents)

    @staticmethod
    def has_reference(message):
        return contains_phrase(message, REFERENCE_WORDS, threshold=0.92)

    @staticmethod
    def has_generic_dog_word(message):
        return IntentDetector._contains_word(words(message), DOG_WORDS)

    @staticmethod
    def has_availability_word(message):
        return contains_phrase(message, AVAILABLE_WORDS)

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
            any(match.kind == EntityKind.DOG for match in entities.matches)
            and self.intent_detector.has_availability_word(request.message)
        ):
            intents = intents.union({AVAILABLE_DOGS})

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
        return bool(
            intents.intersection({PRICING, AVAILABLE_DOGS})
            and not self.intent_detector.has_generic_dog_word(message)
        )
