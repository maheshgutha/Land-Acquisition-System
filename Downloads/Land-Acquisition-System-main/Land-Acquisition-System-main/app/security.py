"""Password hashing, JWT tokens, role checks and per-role data scoping."""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import SECRET_KEY, TOKEN_HOURS
from .db import get_db
from .models import Project, User

ROLES = ("central", "state", "district", "field", "agency", "auditor")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
_ITERATIONS = 120_000


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt = stored.split("$", 1)[0]
    return hmac.compare_digest(hash_password(password, salt), stored)


def create_token(user: User) -> str:
    payload = {
        "sub": user.username,
        "role": user.role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    cred_error = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise cred_error
    user = db.scalar(select(User).where(User.username == payload.get("sub")))
    if not user or not user.active:
        raise cred_error
    return user


def require_roles(*roles: str):
    """Dependency factory: the caller must hold one of `roles`."""

    def dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Role '{user.role}' is not allowed to do this")
        return user

    return dep


def project_scope(user: User):
    """SQL condition limiting which projects a user may see; None means all."""
    if user.role in ("central", "auditor"):
        return None
    if user.role == "state":
        return Project.state == user.state
    if user.role in ("district", "field"):
        return (Project.state == user.state) & (Project.district == user.district)
    if user.role == "agency":
        return Project.agency == user.agency
    return Project.id == -1  # unknown role sees nothing


def scoped_projects(user: User):
    q = select(Project)
    cond = project_scope(user)
    return q if cond is None else q.where(cond)


def get_project_or_404(db: Session, user: User, project_id: int) -> Project:
    """Out-of-scope projects return 404, so their existence is not leaked."""
    q = scoped_projects(user).where(Project.id == project_id)
    project = db.scalar(q)
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


def can_see_owner_names(user: User) -> bool:
    """Owner names are personal data: agencies see parcels but not who owns them."""
    return user.role != "agency"
