from __future__ import annotations

from typing import Optional, Tuple

from app.models import LoginSpec


def prompt_credentials(site_name: str, login: LoginSpec) -> Tuple[str, str]:
    """Ask for credentials only when a site login was planned / prompted."""
    import getpass
    import os

    print(f"\nLogin required for: {site_name}")
    print(f"Login URL: {login.login_url}")

    username = ""
    password = ""
    if login.username_env:
        username = os.getenv(login.username_env, "")
    if login.password_env:
        password = os.getenv(login.password_env, "")

    if not username:
        username = input("Username / email: ").strip()
    if not password:
        password = getpass.getpass("Password (hidden): ")

    if not username or not password:
        raise RuntimeError("Login credentials were not provided.")
    return username, password


def maybe_env_credentials(login: Optional[LoginSpec]) -> Optional[Tuple[str, str]]:
    if not login:
        return None
    import os

    u = os.getenv(login.username_env, "") if login.username_env else ""
    p = os.getenv(login.password_env, "") if login.password_env else ""
    if u and p:
        return u, p
    return None
