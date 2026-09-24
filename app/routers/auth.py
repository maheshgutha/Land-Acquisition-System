from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import DEMO_MODE
from ..db import get_db
from ..models import User
from ..security import create_token, get_current_user, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


def user_dict(u: User) -> dict:
    return {
        "username": u.username, "full_name": u.full_name, "role": u.role,
        "state": u.state, "district": u.district, "agency": u.agency,
    }


@router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == form.username))
    if not user or not user.active or not verify_password(form.password, user.password_hash):
        audit.log(db, form.username[:64], "login_failed", "user", form.username[:40])
        db.commit()
        raise HTTPException(401, "Incorrect username or password")
    audit.log(db, user, "login", "user", user.id)
    db.commit()
    return {"access_token": create_token(user), "token_type": "bearer", "user": user_dict(user)}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return user_dict(user)


@router.get("/demo-users")
def demo_users(db: Session = Depends(get_db)):
    """Lets the login screen list demo accounts. Disabled when LANDSCAN_DEMO=0."""
    if not DEMO_MODE:
        raise HTTPException(404, "Not available")
    return [
        {**user_dict(u), "hint": "password: demo1234"}
        for u in db.scalars(select(User).order_by(User.id))
    ]
