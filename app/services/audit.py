from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import AuditLog

_FORBIDDEN_KEYS = {"password", "token", "secret", "api_key", "authorization"}


def audit(db: Session, *, actor: str, action: str, business_id: str | None = None, detail: dict | None = None) -> None:
    """Write one line to the audit log. Secret-looking keys are removed as a safety net."""
    clean = {k: v for k, v in (detail or {}).items() if k.lower() not in _FORBIDDEN_KEYS}
    db.add(AuditLog(business_id=business_id, actor=actor, action=action, detail=clean))
