"""Safety rules that run BEFORE the AI model, in plain code.

The model is good but not perfect. The rules that must never fail (emergencies, a request for a person,
'are you a robot', attempts to break the rules) are therefore decided here, by fixed code and fixed words.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Business

I = re.IGNORECASE


@dataclass
class RuleHit:
    kind: str  # emergency | urgent | human | robot | injection | angry | spanish
    reply: str
    handoff: bool = False
    urgent: bool = False
    reason: str = ""


# ---- emergencies (life safety) ----
GAS = re.compile(
    r"(smell(s|ing|ed)?\s+(of\s+|like\s+)?(natural\s+)?gas|gas\s+(leak|smell|odou?r)|leaking\s+gas|gas\s+is\s+leaking|"
    r"smell\s+(a\s+)?gas|rotten\s+egg)", I)
FIRE = re.compile(
    r"(on\s+fire|caught\s+fire|house\s+fire|fire\s+in\s+(the|my)|there'?s\s+a\s+fire|there\s+is\s+a\s+fire|"
    r"smell(s|ing)?\s+(of\s+)?smoke|smoke\s+(is\s+)?(coming|pouring)|sparks?\s+(flying|coming)|"
    r"(electrical|wiring)\s+fire|explosion|exploded)", I)
INJURY = re.compile(
    r"(injur(ed|y)|bleeding|unconscious|can'?t\s+breathe|cannot\s+breathe|heart\s+attack|electrocuted|"
    r"someone\s+(is\s+)?hurt|passed\s+out|carbon\s+monoxide|co\s+(alarm|detector)|being\s+shocked|got\s+shocked)", I)

# ---- other rules ----
HUMAN = re.compile(
    r"(\b(human|real\s+person|actual\s+person|live\s+person|representative|operator)\b.*\b(please|want|need|speak|talk|get|let)\b|"
    r"\b(speak|talk)\s+(to|with)\s+(a\s+|an\s+|the\s+|your\s+)?(person|someone|somebody|human|manager|owner|representative|"
    r"real\s+person|live\s+person)\b|\b(get|put)\s+me\s+(a|to|through\s+to)\s+(a\s+)?(human|person|someone|manager|owner)\b|"
    r"\b(call\s+me\s+back)\b|\b(real|live|human)\s+agent\b)", I)
ROBOT = re.compile(
    r"(\b(are|r)\s+(you|u)\s+(a\s+|an\s+)?(robot|bot|ai|a\.i\.|artificial|machine|computer|chatbot|real\s+(person|human)|human|"
    r"actual\s+person|recording)\b|\bis\s+this\s+(a\s+)?(robot|bot|ai|recording|real\s+person|human|machine)\b|"
    r"\b(am\s+i|i\s+am)\s+(talking|speaking|chatting)\s+(to|with)\s+(a\s+)?(robot|bot|ai|machine|computer|human|real\s+person)\b|"
    r"\bwho\s+am\s+i\s+(talking|speaking)\s+(to|with)\b)", I)
INJECTION = re.compile(
    r"((ignore|disregard|forget|override|bypass)\b.{0,40}\b(rules|instructions|prompt|guidelines|directions|restrictions|polic)\w*|"
    r"(show|reveal|print|repeat|tell\s+me|what('?s|\s+is|\s+are)|give\s+me|share|display|output|leak|recite)\b.{0,50}"
    r"\b(system\s+prompt|your\s+(instructions|prompt|rules|guidelines|programming|configuration)|initial\s+prompt|hidden\s+(prompt|instructions))|"
    r"\b(other|another|different|all)\s+(customers?|clients?|people'?s?|users?)('s)?\b.{0,40}\b(appointments?|data|info\w*|numbers?|names?|bookings?|records?|addresses)|"
    r"\b(list|show|give)\b.{0,20}\b(all|every)\s+(the\s+)?(customers?|clients?|appointments?|bookings?|users?)\b|"
    r"\byou\s+are\s+now\b|\bpretend\s+(to\s+be|you\s+are)\b|\bdeveloper\s+mode\b|\bjailbreak\b|\bact\s+as\s+(if\s+you\s+(are|were)|a|an)\b|"
    r"\bdan\s+mode\b)", I)
ANGRY = re.compile(
    r"\b(ridiculous|unacceptable|furious|outraged|terrible\s+service|worst|scam|rip\s?off|ripped\s+off|\bsue\b|lawyer|attorney|"
    r"fed\s+up|sick\s+of|useless|incompetent|idiots?|stupid|wtf|fuck\w*|shit\w*|pissed|disgusting|horrible|never\s+again)\b", I)

SPANISH_MARKERS = {
    "hola", "necesito", "quiero", "gracias", "buenos", "buenas", "cita", "ayuda", "tengo", "puedo", "casa", "agua",
    "hablo", "español", "espanol", "cuando", "cuándo", "cuanto", "cuánto", "estoy", "puede", "pueden", "favor",
    "servicio", "precio", "horario", "días", "dias", "mañana", "manana", "tarde", "noche", "reparar", "problema",
    "porque", "porqué", "pero", "también", "tambien", "donde", "dónde", "quisiera", "disculpe", "señor", "señora",
    "fuga", "plomero", "ustedes", "nosotros", "llamar", "llamen", "numero", "número", "nombre", "buen", "usted",
}
ENGLISH_MARKERS = {
    "the", "and", "is", "are", "you", "your", "can", "need", "want", "please", "hello", "hi", "thanks", "thank",
    "what", "when", "how", "much", "price", "appointment", "book", "have", "my", "i", "it", "this", "that", "do",
    "does", "with", "for", "of", "to", "in", "on", "at", "a", "an", "be", "not", "no", "yes", "tuesday", "monday",
    "wednesday", "thursday", "friday", "saturday", "sunday", "tomorrow", "today", "we", "me",
}


def looks_spanish(text: str) -> bool:
    words = re.findall(r"[a-záéíóúñü¿¡]+", text.lower())
    if not words:
        return False
    es = sum(1 for w in words if w in SPANISH_MARKERS)
    en = sum(1 for w in words if w in ENGLISH_MARKERS)
    accent = bool(re.search(r"[ñáéíóú¿¡]", text.lower()))
    return (es >= 2 and es > en) or (es >= 1 and accent and en == 0 and len(words) <= 12)


def _angry(text: str) -> bool:
    if ANGRY.search(text):
        return True
    letters = [c for c in text if c.isalpha()]
    if text.count("!") >= 3:
        return True
    return len(letters) >= 12 and sum(c.isupper() for c in letters) / len(letters) > 0.75


def check_rules(text: str, b: Business) -> RuleHit | None:
    """Return the first rule that fires, or None to let the AI model answer."""
    name = b.name
    t = text.strip()

    if GAS.search(t):
        return RuleHit(
            "emergency",
            f"Please leave the building right now and call 911 or your gas company's emergency line from outside. "
            f"Do not turn anything on or off. I have alerted {name} immediately and a person will contact you.",
            handoff=True, urgent=True, reason="emergency: gas smell",
        )
    if FIRE.search(t):
        return RuleHit(
            "emergency",
            f"Please get everyone out and call 911 now. I have alerted {name} immediately.",
            handoff=True, urgent=True, reason="emergency: fire or smoke",
        )
    if INJURY.search(t):
        return RuleHit(
            "emergency",
            f"Please call 911 now. I have alerted {name} immediately.",
            handoff=True, urgent=True, reason="emergency: injury or carbon monoxide",
        )
    for kw in (b.facts or {}).get("urgent_keywords", []) or []:
        if kw and kw.lower() in t.lower():
            return RuleHit(
                "urgent",
                f"Thank you. I am marking this as urgent and alerting {name} right now. A person will contact you as "
                f"soon as possible. If anyone is in danger, call 911.",
                handoff=True, urgent=True, reason=f"urgent keyword: {kw}",
            )
    if INJECTION.search(t):
        return RuleHit(
            "injection",
            f"I'm sorry, I can't help with that. I can answer questions about {name} and help you book, move or "
            f"cancel your own appointment. What can I do for you?",
            reason="injection attempt",
        )
    if ROBOT.search(t):
        return RuleHit(
            "robot",
            f"Yes, I'm an AI assistant for {name}. I can answer questions and book appointments, or I can get a "
            f"person for you. What would you like?",
            reason="asked if AI",
        )
    if HUMAN.search(t):
        return RuleHit(
            "human",
            f"Of course. I am passing this to {name} now, with a short summary, and a person will get back to you "
            f"as soon as possible.",
            handoff=True, reason="customer asked for a person",
        )
    if _angry(t):
        return RuleHit(
            "angry",
            f"I'm sorry for the trouble, and I want to get this right. I am passing this to {name} now with the "
            f"details so a person can help you personally. They will contact you as soon as possible.",
            handoff=True, reason="angry customer",
        )
    if looks_spanish(t):
        return RuleHit(
            "spanish",
            f"Gracias por escribir a {name}. Por ahora atendemos en inglés, pero una persona le devolverá la "
            f"llamada. Por favor envíe su nombre y su mejor número de teléfono. / Thanks for contacting {name}. "
            f"A person will call you back. Please send your name and best phone number.",
            handoff=True, reason="spanish",
        )
    return None


POST_HANDOFF_REPLY = (
    "Thank you. {name} has your message and a person will follow up as soon as possible. "
    "If this is an emergency, please call 911."
)
KILL_SWITCH_REPLY = (
    "Thanks for contacting {name}. Our automatic assistant is not available right now. "
    "I have let the team know and a person will get back to you as soon as possible."
)
CAP_REPLY = (
    "I want to make sure you get the right help, so I am passing this conversation to {name}. "
    "A person will follow up with you as soon as possible."
)
LLM_DOWN_REPLY = (
    "Sorry, I'm having a technical problem right now. I have let {name} know and a person will get back to "
    "you as soon as possible."
)
LLM_RETRY_REPLY = "Sorry, I did not get that. Please send your message again in a moment."
INVALID_PHONE_REPLY = (
    "That does not look like a valid US phone number. Please type the full 10-digit number with the area code."
)
PHONE_GUARD_REPLY = "Sorry, I do not have your phone number yet. What is the best number to reach you?"
PRICE_GUARD_REPLY = (
    "I don't have a confirmed price for that, so I won't guess. I'll ask {name} to follow up with you "
    "with an exact answer."
)
CONFIRM_GUARD_REPLY = (
    "I could not confirm that booking yet. I have passed your details to {name} and a person will confirm "
    "with you shortly."
)
LEAK_GUARD_REPLY = (
    "I'm sorry, I can't help with that. I can answer questions about {name} and help with your appointment."
)
