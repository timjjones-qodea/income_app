from __future__ import annotations

import re
from difflib import SequenceMatcher

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.importers import normalize_name
from app.models import Security, SecurityAlias


COMMON_MATCH_STOPWORDS = {
    "A",
    "B",
    "C",
    "CLASS",
    "GBP",
    "GBX",
    "ORD",
    "P",
}


def _match_tokens(value: str) -> set[str]:
    tokens = set()
    for token in normalize_name(value).split():
        if token in COMMON_MATCH_STOPWORDS:
            continue
        if len(token) > 4 and token.endswith("S"):
            token = token[:-1]
        tokens.add(token)
    return tokens


def _score_name_match(query: str, candidate: Security) -> float:
    query_key = normalize_name(query)
    candidate_key = normalize_name(candidate.name)
    if not query_key or not candidate_key:
        return 0
    if query_key == candidate_key:
        return 1

    query_tokens = _match_tokens(query)
    candidate_tokens = _match_tokens(candidate.name)
    if not query_tokens or not candidate_tokens:
        return 0

    overlap = len(query_tokens & candidate_tokens)
    precision = overlap / len(query_tokens)
    recall = overlap / len(candidate_tokens)
    token_score = (precision + recall) / 2
    if query_tokens <= candidate_tokens or candidate_tokens <= query_tokens:
        token_score = max(token_score, 0.93)

    sequence_score = SequenceMatcher(None, query_key, candidate_key).ratio()
    return max(token_score, sequence_score)


def match_security(
    db: Session,
    *,
    isin: str | None = None,
    sedol: str | None = None,
    ticker: str | None = None,
    name: str | None = None,
    source: str = "AJ Bell",
) -> Security | None:
    if isin:
        found = db.scalar(select(Security).where(func.upper(Security.isin) == isin.upper()))
        if found:
            return found
    if sedol:
        found = db.scalar(select(Security).where(func.upper(Security.sedol) == sedol.upper()))
        if found:
            return found
    if ticker:
        found = db.scalar(select(Security).where(func.upper(Security.ticker) == ticker.upper()))
        if found:
            return found
    if name:
        alias_key = normalize_name(name)
        alias = db.scalar(
            select(SecurityAlias).where(
                SecurityAlias.source == source,
                SecurityAlias.external_identifier == alias_key,
            )
        )
        if alias:
            return alias.security
        candidates = db.scalars(select(Security)).all()
        for candidate in candidates:
            if normalize_name(candidate.name) == alias_key:
                return candidate
        scored = sorted(
            (
                (_score_name_match(name, candidate), candidate)
                for candidate in candidates
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        if scored and scored[0][0] >= 0.82 and (
            len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.08
        ):
            return scored[0][1]
    return None


def create_or_match_security(db: Session, data: dict, *, source: str = "AJ Bell") -> Security:
    security = match_security(
        db,
        isin=data.get("isin"),
        sedol=data.get("sedol"),
        ticker=data.get("ticker"),
        name=data.get("name"),
        source=source,
    )
    if security:
        for field in ("ticker", "isin", "sedol", "exchange"):
            if not getattr(security, field) and data.get(field):
                setattr(security, field, data[field])
        if security.asset_type == "Other" and data.get("asset_type"):
            security.asset_type = data["asset_type"]
        return security
    name = data.get("name") or data.get("ticker") or data.get("isin") or "Unknown security"
    security = Security(
        name=re.sub(r"\s*\(LSE:[^)]+\)\s*", "", name).strip(),
        ticker=data.get("ticker"),
        isin=data.get("isin"),
        sedol=data.get("sedol"),
        currency=data.get("currency") or "GBP",
        exchange=data.get("exchange"),
        asset_type=data.get("asset_type") or "Other",
    )
    db.add(security)
    db.flush()
    if name:
        db.add(
            SecurityAlias(
                source=source,
                external_identifier=normalize_name(name),
                security_id=security.id,
            )
        )
    return security


def save_manual_mapping(db: Session, external_name: str, security_id: int, source: str = "AJ Bell"):
    key = normalize_name(external_name)
    alias = db.scalar(
        select(SecurityAlias).where(
            SecurityAlias.source == source,
            SecurityAlias.external_identifier == key,
        )
    )
    if alias:
        alias.security_id = security_id
    else:
        alias = SecurityAlias(
            source=source, external_identifier=key, security_id=security_id
        )
        db.add(alias)
    db.flush()
    return alias
