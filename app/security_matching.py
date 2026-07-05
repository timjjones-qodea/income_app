from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.importers import normalize_name
from app.models import Security, SecurityAlias


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

