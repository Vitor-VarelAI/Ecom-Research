"""Canonical absence placeholders, selected by field meaning.

The existing pipeline maps unknown evidence to a family of fixed texts.  A
field knows *what kind* of absence it is (evidence never found, value not yet
validated, mandatory documentation missing, next action is research); the
renderer-neutral model stores that meaning and resolves nulls to the
canonical placeholder so the Rich and HTML-fallback renderers always agree.
Unknown meanings fail loudly instead of inventing a label.
"""

from __future__ import annotations

#: Evidence that was never found (proof links, suggestions, full-report URL).
MISSING = "NÃO ENCONTRADO"
#: Value that needs validation (margins, shipping, technical data).
TO_VALIDATE = "POR VALIDAR"
#: Mandatory documentation that is absent for the current stage.
DOCUMENTATION_MISSING = "DOCUMENTAÇÃO EM FALTA"
#: Next action is research (suppliers, legislation, Safety Gate).
RESEARCH_NEEDED = "PESQUISA NECESSÁRIA"
#: Dropshipping supplier not yet confirmed.
SUPPLIER_MISSING = "FORNECEDOR EM FALTA"
#: Sale test blocked until the applicable documentation exists.
PENDING_DOCUMENTATION = "AGUARDAR DOCUMENTAÇÃO APLICÁVEL"
#: Rule genuinely not applicable (no invented exemption is implied).
NOT_APPLICABLE = "NÃO APLICÁVEL"

_MEANINGS: dict[str, str] = {
    "missing": MISSING,
    "unvalidated": TO_VALIDATE,
    "documentation_missing": DOCUMENTATION_MISSING,
    "research_needed": RESEARCH_NEEDED,
    "supplier_missing": SUPPLIER_MISSING,
    "pending_documentation": PENDING_DOCUMENTATION,
    "not_applicable": NOT_APPLICABLE,
}


def placeholder_for(meaning: str) -> str:
    """Canonical placeholder for a field's absence meaning.

    Raises ``ValueError`` for unknown meanings so a typo can never silently
    render an invented label into a message.
    """
    try:
        return _MEANINGS[meaning]
    except KeyError:
        raise ValueError(f"meaning de ausência desconhecido: {meaning!r}") from None


def resolve_value(value: object | None, meaning: str) -> str:
    """Resolve a field value: null/blank becomes the placeholder of the
    field's meaning; real strings pass through unchanged.  Non-string,
    non-null values are rejected (fail loud, validation-safe)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return placeholder_for(meaning)
    if not isinstance(value, str):
        raise ValueError(f"value inválido para renderização: {value!r}")
    return value
