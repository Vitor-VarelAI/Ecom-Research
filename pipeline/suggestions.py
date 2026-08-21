"""Deterministic, advisory keyword suggestions from Google Trends related queries."""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import urlparse


_INTERROGATIVES = {
    "como",
    "qual",
    "quais",
    "quando",
    "onde",
    "porque",
    "porquê",
    "por que",
    "qué",
    "que",
    "quien",
    "quién",
    "donde",
    "dónde",
    "cuando",
    "cómo",
    "why",
    "what",
    "which",
    "when",
    "where",
    "who",
    "how",
    "pourquoi",
    "comment",
    "quand",
}


def _fold(text: Any) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.findall(r"[\w]+", value.casefold(), flags=re.UNICODE))


def _numeric_score(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if "," in text and "." not in text and re.search(r",\d{3}(?:\D|$)", text):
        text = text.replace(",", "")
    elif "." in text and "," not in text and re.search(r"\.\d{3}(?:\D|$)", text):
        text = text.replace(".", "")
    else:
        text = text.replace(",", ".")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return 0.0
    try:
        return float(match.group(0))
    except ValueError:
        return 0.0


def _is_breakout(value: Any, item: dict[str, Any]) -> bool:
    candidates = [value, item.get("type"), item.get("growth"), item.get("trend")]
    return any(_fold(candidate) == "breakout" for candidate in candidates)


def _is_url(value: str) -> bool:
    if value.startswith(("http://", "https://", "www.")):
        return True
    if re.match(r"^[\w.-]+\.[A-Za-z]{2,}(?:/|$)", value):
        return True
    try:
        return bool(urlparse(value).scheme and urlparse(value).netloc)
    except ValueError:
        return False


def _is_interrogative(value: str) -> bool:
    if "?" in value:
        return True
    first_word = _fold(value).split(maxsplit=1)[0] if value.strip() else ""
    return first_word in {_fold(word) for word in _INTERROGATIVES}


# Tokens excluded when choosing the product-head token: Portuguese/English
# stopwords, prepositions and pronouns plus geographic qualifiers (countries,
# demonyms, continents and major cities), accent/case folded to match
# ``_fold`` output.  Everything is a deliberate, explicit heuristic: the head
# token anchors product relevance, so function words and place names must
# never win the selection (``camisolas portugal`` and ``portugal camisolas``
# both choose ``camisolas``).
_PRODUCT_STOPWORDS = frozenset({
    # Portuguese function words (folded): articles, prepositions, conjunctions, pronouns
    "a", "o", "as", "os", "um", "uma", "uns", "umas",
    "de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas",
    "por", "para", "pra", "pro", "com", "sem", "sob", "sobre", "entre",
    "ate", "apos", "desde", "contra", "mediante", "conforme", "segundo",
    "durante", "perante", "ante", "ao", "aos",
    "e", "ou", "mas", "que", "porque", "pois", "se", "como", "quando",
    "enquanto", "nem", "senao", "todavia", "contudo", "porem", "portanto",
    "logo", "assim", "embora",
    "eu", "tu", "ele", "ela", "nos", "vos", "eles", "elas", "voce",
    "voces", "me", "mim", "comigo", "te", "ti", "contigo", "si",
    "consigo", "lhe", "lhes", "este", "esta", "estes", "estas", "isto",
    "esse", "essa", "esses", "essas", "isso", "aquele", "aquela",
    "aqueles", "aquelas", "aquilo", "meu", "minha", "meus", "minhas",
    "teu", "tua", "teus", "tuas", "seu", "sua", "seus", "suas",
    "nosso", "nossa", "nossos", "nossas", "vosso", "vossa", "mesmo",
    "mesma", "proprio", "propria", "outro", "outra", "outros", "outras",
    "nada", "ninguem", "alguem", "tudo", "todos", "todas", "algo",
    "quem", "qual", "quais", "cujo", "cuja", "cujos", "cujas",
    "quanto", "quanta", "quantos", "quantas",
    "ser", "estar", "ter", "haver", "fazer", "dizer", "ir", "ver",
    "poder", "dever", "querer", "saber", "parecer", "ficar", "dar",
    "nao", "sim", "ja", "ainda", "agora", "sempre", "nunca", "tambem",
    "muito", "muita", "muitos", "muitas", "pouco", "pouca", "poucos",
    "poucas", "mais", "menos", "tao", "tanto", "tanta", "tantos",
    "tantas", "bem", "mal", "bom", "boa", "bons", "boas", "foi",
    "era", "sao", "e", "esta", "estao", "tem", "tendo", "tinha",
    # English function words (folded)
    "a", "an", "the", "and", "or", "but", "if", "of", "to", "in",
    "on", "at", "by", "for", "with", "without", "from", "up", "down",
    "about", "into", "onto", "over", "under", "between", "among",
    "through", "during", "before", "after", "above", "below", "out",
    "off", "per", "via", "is", "are", "was", "were", "be", "been",
    "being", "am", "do", "does", "did", "has", "have", "had", "i",
    "you", "he", "she", "it", "we", "they", "them", "his", "her",
    "its", "our", "their", "my", "your", "me", "him", "us", "not",
    "so", "than", "that", "this", "these", "those", "then", "there",
    "here", "when", "where", "why", "how", "what", "which", "who",
    "whom", "will", "would", "can", "could", "should", "may", "might",
    "must", "shall", "s", "t", "ll", "re", "ve", "d", "m", "y",
    "as", "also", "just", "only", "more", "most", "some", "any", "no",
    "yes",
})

# Geographic country/market/region qualifiers (accent/case folded).  These are
# never the product head: a related query sharing only the geographic token
# (``volta a portugal 2026`` for ``camisolas portugal``) must not count as
# product evidence.
_GEO_QUALIFIERS = frozenset({
    # Core PT/ES/FR/BR markets + common international names (PT + EN)
    "portugal", "portuguese", "espanha", "spain", "franca", "france",
    "brasil", "brazil", "italia", "italy", "alemanha", "germany",
    "inglaterra", "england", "escocia", "irlanda", "ireland", "holanda",
    "belgica", "luxemburgo", "suica", "austria", "suecia", "noruega",
    "dinamarca", "finlandia", "polonia", "grecia", "turquia", "russia",
    "ucrania", "china", "japao", "japan", "coreia", "india", "canada",
    "mexico", "argentina", "chile", "colombia", "peru", "venezuela",
    "uruguai", "paraguai", "bolivia", "equador", "cuba", "australia",
    "angola", "mocambique", "mozambique", "guine", "timor", "macau",
    "marrocos", "tunisia", "egito", "argelia", "nigeria", "quenia",
    "africa", "asia", "europa", "europe", "america", "oceania", "mundo",
    "global", "internacional", "nacional", "estados", "unidos", "eua",
    "usa", "uk", "reino",
    # Demonyms (PT + EN)
    "portugues", "portuguesa", "portugueses", "portuguesas", "espanhol",
    "espanhola", "espanhois", "espanholas", "frances", "francesa",
    "franceses", "francesas", "italiano", "italiana", "italianos",
    "italianas", "alemao", "alema", "alemaes", "alemas", "ingles",
    "inglesa", "ingleses", "inglesas", "brasileiro", "brasileira",
    "brasileiros", "brasileiras", "americano", "americana", "americanos",
    "americanas", "europeu", "europeia", "europeus", "europeias",
    "angolano", "angolana", "angolanos", "angolanas", "mocambicano",
    "mocambicana", "spanish", "french", "italian", "german", "english",
    "british", "brazilian", "angolan", "mozambican", "american",
    "european",
    # Major cities and regions
    "lisboa", "porto", "coimbra", "braga", "faro", "evora", "aveiro",
    "viseu", "guimaraes", "sintra", "cascais", "algarve", "alentejo",
    "minho", "madeira", "acores", "madrid", "barcelona", "valencia",
    "sevilha", "granada", "bilbau", "paris", "londres", "london",
    "berlim", "berlin", "munique", "hamburgo", "roma", "milao",
    "veneza", "florenca", "napoles", "turim", "genova", "amsterdao",
    "amsterdam", "roterdao", "haia", "bruxelas", "genebra", "zurique",
    "viena", "dublin", "oslo", "estocolmo", "copenhaga", "helsinquia",
    "atenas", "varsovia", "praga", "budapeste", "istambul", "ancara",
    "moscovo", "moscou", "moscow", "sao", "paulo", "rio", "janeiro",
    "brasilia", "salvador", "recife", "fortaleza", "curitiba", "belo",
    "horizonte", "santiago", "buenos", "aires", "lima", "bogota",
    "caracas", "montevideu", "assuncao", "quito", "havana", "toronto",
    "montreal", "vancouver", "quebec", "nova", "york", "los", "angeles",
    "chicago", "miami", "seattle", "boston", "san", "francisco",
    "sydney", "melbourne", "toquio", "osaka", "xangai", "pequim",
    "hong", "kong", "singapura", "luanda", "maputo",
    "norte", "sul", "leste", "oeste", "central", "centro", "capital",
})

# Concise, bounded description of the relevance policy persisted with the
# filter audit (never stores excluded query text).
_RELEVANCE_POLICY = (
    "product-head token containment: first source-keyword token that is "
    "neither a PT/EN stopword/preposition nor a geographic qualifier "
    "(fallback: first token); accent/case-folded exact token equality or a "
    "single trailing 's' plural variant only (vela~velas); arbitrary shared "
    "prefixes never count (pasta~pastelaria, arte~artesa, sol~sola rejected)"
)


def is_stopword(token: str) -> bool:
    """True when a folded token is a PT/EN function word (never meaningful
    product evidence).  The head-token selection and the matcher's
    meaningful-token contract share this single rule."""
    return token in _PRODUCT_STOPWORDS


def is_geographic_qualifier(token: str) -> bool:
    """True when a folded token is a geographic qualifier (country,
    demonym, region or major city).  It can never be the product head and
    never counts as meaningful product evidence outside the strict core
    markets — the matcher requires geo proof only for the four core markets
    (PT/ES/FR/BR)."""
    return token in _GEO_QUALIFIERS


def product_head_token(source_keyword: str) -> str | None:
    """Deterministic product-head token for a source keyword.

    The first folded source token that is neither a Portuguese/English
    stopword/preposition nor a geographic qualifier; the first token when
    every token qualifies out; ``None`` when the keyword has no tokens at
    all (empty or punctuation-only).
    """
    tokens = _fold(source_keyword).split()
    if not tokens:
        return None
    for token in tokens:
        if token not in _PRODUCT_STOPWORDS and token not in _GEO_QUALIFIERS:
            return token
    return tokens[0]


def tokens_equivalent(head: str, token: str) -> bool:
    """Exact-or-single-trailing-``s`` equivalence between two folded tokens.

    Exact equality always matches; the only plural variant allowed is one
    trailing ``s`` appended to (or removed from) the other token, so
    ``vela``~``velas`` and ``camisola``~``camisolas`` equate while shared
    prefixes of any other length never do: ``pasta``~``pastelaria``,
    ``arte``~``artesa`` and ``sol``~``sola`` stay unrelated.  This is the
    single public rule shared by the Trends related filter, the Keyword
    Planner CSV selection and the Apify ads high-intent gate.
    """
    if token == head:
        return True
    if head and token == head + "s":
        return True
    if token and head == token + "s":
        return True
    return False


def is_product_related(source_keyword: str, candidate_keyword: str) -> bool:
    """True when a candidate keyword contains the source's product head.

    The candidate is product-related when at least one of its
    accent/case-folded tokens matches the source product head token
    (``product_head_token``) under exact-or-single-trailing-``s``
    equivalence (``tokens_equivalent``): ``camisola de portugal`` relates
    to ``camisola`` and ``camisolas`` but never to ``caneca de natal``,
    ``volta a portugal 2026``, ``pastelaria`` (for ``pasta``), ``artesa``
    (for ``arte``), ``sola`` (for ``sol``) or any row whose only overlap is
    a stopword like ``de``.  A source keyword without any token is related
    to nothing.
    """
    head = product_head_token(source_keyword)
    if head is None:
        return False
    tokens = _fold(candidate_keyword).split()
    return any(tokens_equivalent(head, token) for token in tokens)


def filter_relevant_related_queries(
    source_keyword: str,
    trends_data: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return a *copy* of ``trends_data`` keeping only product-relevant
    related queries, plus bounded audit metadata.

    A candidate query is product-relevant only when it contains the product
    head token (``product_head_token``) under exact-or-single-trailing-``s``
    equivalence (``tokens_equivalent``) — the same deterministic policy
    exposed as ``is_product_related`` and reused by the Keyword Planner CSV
    related selection.  The input dict and list are never mutated; the
    relative order of retained candidates is preserved.  Error payloads,
    non-list data and keywords without any token are returned as an
    unchanged copy (current behavior).
    Audit metadata (raw/retained counts, head token, policy) is attached as
    ``related_queries_filter`` without any excluded query text.
    """
    if not isinstance(trends_data, dict) or "error" in trends_data:
        return trends_data
    raw_queries = trends_data.get("related_queries")
    if not isinstance(raw_queries, list):
        return trends_data
    head = product_head_token(source_keyword)
    copy = dict(trends_data)
    if head is None:
        copy["related_queries"] = list(raw_queries)
        return copy
    retained: list[dict[str, Any]] = []
    for item in raw_queries:
        if not isinstance(item, dict):
            continue  # non-dict items are skipped by the extractor and would crash pack rendering
        keyword = str(item.get("keyword", item.get("query", "")) or "").strip()
        if is_product_related(source_keyword, keyword):
            retained.append(item)
    copy["related_queries"] = retained
    copy["related_queries_filter"] = {
        "raw_count": len(raw_queries),
        "retained_count": len(retained),
        "head_token": head,
        "policy": _RELEVANCE_POLICY,
    }
    return copy


def extract_keyword_suggestions(
    source_keyword: str,
    trends_data: dict[str, Any] | None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return ranked proposed alternatives without mutating any configuration.

    Breakout queries always precede numeric queries; numeric queries then sort
    descending by their extracted value.  Input order is retained for ties.
    """
    if limit <= 0 or not trends_data or "error" in trends_data:
        return []

    raw_queries = trends_data.get("related_queries", [])
    if not isinstance(raw_queries, list):
        return []

    candidates: list[tuple[int, bool, float, dict[str, Any]]] = []
    seen: set[str] = set()
    source_folded = _fold(source_keyword)

    for index, item in enumerate(raw_queries):
        if not isinstance(item, dict):
            continue
        keyword = str(item.get("keyword", item.get("query", "")) or "").strip()
        folded = _fold(keyword)
        if not folded or folded == source_folded or folded in seen:
            continue
        if _is_url(keyword) or _is_interrogative(keyword):
            continue

        raw_score = item.get("score", item.get("value", item.get("extracted_value", "")))
        breakout = _is_breakout(raw_score, item)
        numeric = _numeric_score(raw_score)
        seen.add(folded)
        result_score: int | float | str
        if breakout:
            result_score = "Breakout"
        elif numeric.is_integer():
            result_score = int(numeric)
        else:
            result_score = numeric
        suggestion = {
            "keyword": keyword,
            "score": result_score,
            "status": "proposed",
        }
        candidates.append((index, breakout, numeric, suggestion))

    candidates.sort(key=lambda candidate: (not candidate[1], -candidate[2], candidate[0]))
    return [candidate[3] for candidate in candidates[:limit]]
