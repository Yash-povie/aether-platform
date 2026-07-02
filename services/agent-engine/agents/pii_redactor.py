import logging
import re
from typing import Dict, Any, Tuple

from ..schemas import PipelineState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional Presidio import — falls back to regex if not installed
# ---------------------------------------------------------------------------
try:
    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine
    _analyzer = AnalyzerEngine()
    _anonymizer = AnonymizerEngine()
    PRESIDIO_AVAILABLE = True
    logger.info("[PIIRedactor] Microsoft Presidio loaded — using NLP-based redaction")
except ImportError:
    _analyzer = None
    _anonymizer = None
    PRESIDIO_AVAILABLE = False
    logger.warning(
        "[PIIRedactor] presidio-analyzer / presidio-anonymizer not installed — "
        "falling back to regex-based redaction"
    )

# ---------------------------------------------------------------------------
# Regex fallback patterns (compiled once at import time)
# ---------------------------------------------------------------------------
_REGEX_PATTERNS = [
    # Email
    (re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'), "[EMAIL]"),
    # US phone  (various separators)
    (re.compile(r'\b(?:\+?1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}\b'), "[PHONE]"),
    # US SSN
    (re.compile(r'\b\d{3}-\d{2}-\d{4}\b'), "[SSN]"),
    # Credit / debit card (13-16 digits, optional separators)
    (re.compile(r'\b(?:\d[ \-]?){13,16}\b'), "[CARD]"),
    # IPv4 address
    (re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'), "[IP_ADDRESS]"),
    # US passport  (letter + 8 digits)
    (re.compile(r'\b[A-Z]\d{8}\b'), "[PASSPORT]"),
    # Date of birth patterns  (MM/DD/YYYY or YYYY-MM-DD)
    (re.compile(
        r'\b(?:0?[1-9]|1[0-2])[/\-](?:0?[1-9]|[12]\d|3[01])[/\-]\d{4}\b'
        r'|\b\d{4}[/\-](?:0?[1-9]|1[0-2])[/\-](?:0?[1-9]|[12]\d|3[01])\b'
    ), "[DATE_OF_BIRTH]"),
]


def _redact_with_presidio(text: str) -> Tuple[str, list]:
    """Use Microsoft Presidio for NER-based PII detection and anonymisation."""
    results = _analyzer.analyze(text=text, language="en")
    anonymized = _anonymizer.anonymize(text=text, analyzer_results=results)
    redactions = [
        {
            "type": r.entity_type,
            "start": r.start,
            "end": r.end,
            "score": round(r.score, 4),
        }
        for r in results
    ]
    return anonymized.text, redactions


def _redact_with_regex(text: str) -> Tuple[str, list]:
    """Regex-based PII redaction used when Presidio is unavailable."""
    redacted = text
    redactions = []
    for pattern, replacement in _REGEX_PATTERNS:
        for match in pattern.finditer(redacted):
            redactions.append({
                "type": replacement.strip("[]"),
                "start": match.start(),
                "end": match.end(),
                "score": 1.0,  # regex is deterministic
            })
        redacted = pattern.sub(replacement, redacted)
    return redacted, redactions


def redact_text(text: str) -> Tuple[str, list]:
    """Redact PII from *text*.

    Returns ``(redacted_text, redaction_list)`` where each redaction is a dict
    with keys ``type``, ``start``, ``end``, ``score``.

    Tries Presidio first; falls back to regex if Presidio is unavailable or
    raises an exception.
    """
    if not text or not text.strip():
        return text, []

    if PRESIDIO_AVAILABLE:
        try:
            return _redact_with_presidio(text)
        except Exception as exc:
            logger.warning(
                "[PIIRedactor] Presidio failed (%s) — falling back to regex", exc
            )

    return _redact_with_regex(text)


def pii_redactor_node(state: PipelineState) -> Dict[str, Any]:
    """Scan and redact PII from all loaded artifact content.

    Operates on the ``content`` field (populated by the coordinator) and
    records per-artifact redaction counts in the audit trail.  The
    ``content_uri`` is intentionally left intact — only the in-memory content
    string is sanitised.
    """
    artifacts = state.get("artifacts", [])
    logger.info("[PIIRedactor] Scanning %d artifact(s) for PII", len(artifacts))

    sanitized = []
    total_redactions = 0

    for art in artifacts:
        content = art.get("content", "")
        if content and not content.startswith("[Content unavailable"):
            redacted_content, redactions = redact_text(content)
            count = len(redactions)
            total_redactions += count
            sanitized.append({
                **art,
                "content": redacted_content,
                "pii_redacted": count > 0,
                "redaction_count": count,
            })
        else:
            sanitized.append({
                **art,
                "pii_redacted": False,
                "redaction_count": 0,
            })

    logger.info(
        "[PIIRedactor] Redacted %d PII instance(s) across %d artifact(s)",
        total_redactions,
        len(artifacts),
    )

    return {
        "artifacts": sanitized,
        "audit_trail": state.get("audit_trail", []) + [
            {
                "step": "pii_redactor",
                "engine": "presidio" if PRESIDIO_AVAILABLE else "regex",
                "artifacts_scanned": len(artifacts),
                "total_redactions": total_redactions,
            }
        ],
    }
