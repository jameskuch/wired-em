"""Login, sessions, and the user list for the web app.

Authentication is OFF until a users file exists, so a
local checkout runs exactly as before; the deployed server turns it on by
creating ``users.json`` with the bundled CLI::

    sbfsem-users add alice          # you type their password at a prompt
    sbfsem-users invite alice       # they choose their own, via a one-time link
    sbfsem-users remove alice
    sbfsem-users list

Everything lives in one directory (``$SBFSEM_AUTH_DIR``, default
``~/.sbfsem/auth``):

``users.json``
    ``{"name": "pbkdf2_sha256$..."}`` -- salted PBKDF2 hashes, never plain
    passwords.  Maintained by the CLI and re-read whenever its mtime changes,
    so granting or revoking access needs no restart.  Revocation is
    immediate: a session cookie is only honored while its user is still in
    the file.
``session.key``
    Random secret the session cookies are signed with, created on first use.
    Deleting it signs everyone out at once.

Sessions are stateless signed cookies (``user.expiry.hmac``), so they survive
service restarts and would work unchanged across several uvicorn workers.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

COOKIE_NAME = "sbfsem_session"
SESSION_TTL = 30 * 24 * 3600  # a month; expiry is baked into the token
INVITE_TTL = 7 * 24 * 3600  # how long a /welcome invite link stays redeemable
MIN_PASSWORD_LENGTH = 8  # for self-chosen passwords on the /welcome page
PBKDF2_ITERATIONS = 600_000  # OWASP's 2023+ floor for PBKDF2-HMAC-SHA256

#: Where invite links point; override for a different deployment.
PUBLIC_URL = os.environ.get("SBFSEM_PUBLIC_URL", "https://sbfsem-tools.com")


def default_auth_dir() -> Path:
    return Path(os.environ.get("SBFSEM_AUTH_DIR") or Path.home() / ".sbfsem" / "auth")


# --- passwords ---------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_hex, digest_hex = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def _hash_invite(code: str) -> str:
    # Plain SHA-256: invite codes are 24 random bytes, far beyond brute force,
    # so the slow salted KDF would buy nothing here.
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


# --- the user list -----------------------------------------------------------


class UserStore:
    """``users.json`` as a live mapping of username to password hash.

    The file is re-read whenever its mtime changes, so the CLI (or a text
    editor) can grant and revoke access while the service runs.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._users: dict[str, str] = {}
        self._exists = False
        self._mtime: int | None = None
        self._refresh()

    def _refresh(self) -> None:
        try:
            mtime = self.path.stat().st_mtime_ns
        except OSError:
            self._users, self._exists, self._mtime = {}, False, None
            return
        self._exists = True
        if mtime == self._mtime:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._users = {str(k): str(v) for k, v in data.items()}
            self._mtime = mtime
        except (json.JSONDecodeError, AttributeError, OSError) as exc:
            # A malformed file must fail closed (nobody gets in), not fall
            # back to "no users configured" (everybody gets in).
            log.error("unreadable users file %s: %s", self.path, exc)
            self._users, self._mtime = {}, None

    def names(self) -> list[str]:
        self._refresh()
        return sorted(self._users)

    def configured(self) -> bool:
        """Whether login is on: the file exists, even with zero users left.

        Removing the last user must lock the site to everyone, not silently
        fall open; turning login *off* takes deleting users.json itself.
        """
        self._refresh()
        return self._exists

    def check(self, username: str, password: str) -> bool:
        self._refresh()
        stored = self._users.get(username)
        if stored is None:
            # Same work as a real check, so a response-time probe cannot
            # enumerate which usernames exist.
            verify_password(password, hash_password("-"))
            return False
        return verify_password(password, stored)

    def has(self, username: str) -> bool:
        self._refresh()
        return username in self._users

    def add(self, username: str, password: str) -> None:
        self._refresh()
        self._users[username] = hash_password(password)
        self._write()

    def invite(self, username: str, ttl: int = INVITE_TTL, now: float | None = None) -> str:
        """Create (or reset) ``username`` as pending, keyed to a one-time code.

        The returned code goes into a ``/welcome?code=...`` link; whoever
        opens it chooses the account's password themselves.  Only the code's
        hash is stored, so reading users.json does not yield working invites.
        A pending entry cannot log in (its scheme fails password checks).
        """
        code = secrets.token_urlsafe(24)
        expiry = int(now if now is not None else time.time()) + ttl
        self._refresh()
        self._users[username] = f"invite${_hash_invite(code)}${expiry}"
        self._write()
        return code

    def find_invite(self, code: str, now: float | None = None) -> str | None:
        """The username a valid, unexpired invite code belongs to."""
        self._refresh()
        clock = now if now is not None else time.time()
        for name, stored in self._users.items():
            parts = stored.split("$")
            if (
                len(parts) == 3
                and parts[0] == "invite"
                and hmac.compare_digest(parts[1], _hash_invite(code))
                and int(parts[2]) >= clock
            ):
                return name
        return None

    def redeem(self, code: str, password: str, now: float | None = None) -> str | None:
        """Turn a pending invite into a real account with this password."""
        username = self.find_invite(code, now)
        if username is None:
            return None
        self.add(username, password)  # replaces the invite: the code is spent
        return username

    def status(self, username: str, now: float | None = None) -> str:
        """``active``, ``invited``, ``invite expired``, or ``unknown``."""
        self._refresh()
        stored = self._users.get(username)
        if stored is None:
            return "unknown"
        parts = stored.split("$")
        if len(parts) == 3 and parts[0] == "invite":
            clock = now if now is not None else time.time()
            return "invited" if int(parts[2]) >= clock else "invite expired"
        return "active"

    def remove(self, username: str) -> None:
        self._refresh()
        del self._users[username]
        self._write()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._users, indent=2) + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.path)
        self._mtime = None


# --- sessions ----------------------------------------------------------------


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SessionSigner:
    """Stateless session tokens: ``b64(user).expiry.hmac``."""

    def __init__(self, secret: bytes) -> None:
        self._secret = secret

    def _sign(self, payload: str) -> str:
        return hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).hexdigest()

    def make_token(self, username: str, now: float | None = None) -> str:
        expiry = int(now if now is not None else time.time()) + SESSION_TTL
        payload = f"{_b64(username.encode('utf-8'))}.{expiry}"
        return f"{payload}.{self._sign(payload)}"

    def read_token(self, token: str, now: float | None = None) -> str | None:
        """The username, or ``None`` for anything expired, forged, or garbled."""
        try:
            name_b64, expiry_text, signature = token.split(".")
            payload = f"{name_b64}.{expiry_text}"
            if not hmac.compare_digest(signature, self._sign(payload)):
                return None
            if int(expiry_text) < (now if now is not None else time.time()):
                return None
            return _unb64(name_b64).decode("utf-8")
        except (ValueError, TypeError):
            return None


# --- the piece the app talks to ----------------------------------------------


class AuthManager:
    def __init__(self, auth_dir: Path | None = None) -> None:
        self.auth_dir = auth_dir or default_auth_dir()
        self.users = UserStore(self.auth_dir / "users.json")
        self._signer: SessionSigner | None = None

    @property
    def enabled(self) -> bool:
        return self.users.configured()

    def _get_signer(self) -> SessionSigner:
        # Lazy so an auth-less local checkout never grows a key file.
        if self._signer is None:
            key_path = self.auth_dir / "session.key"
            try:
                secret = key_path.read_bytes()
            except OSError:
                secret = secrets.token_bytes(32)
                key_path.parent.mkdir(parents=True, exist_ok=True)
                key_path.write_bytes(secret)
                key_path.chmod(0o600)
                log.info("created session signing key %s", key_path)
            self._signer = SessionSigner(secret)
        return self._signer

    def login(self, username: str, password: str) -> str | None:
        """A session token on success, ``None`` on bad credentials."""
        if self.users.check(username, password):
            return self._get_signer().make_token(username)
        return None

    def redeem_invite(self, code: str, password: str) -> tuple[str, str] | None:
        """Claim an invite: ``(username, session token)``, signed straight in."""
        username = self.users.redeem(code, password)
        if username is None:
            return None
        return username, self._get_signer().make_token(username)

    def current_user(self, token: str | None) -> str | None:
        if not token:
            return None
        username = self._get_signer().read_token(token)
        # Honoring the cookie only while the user is still listed makes
        # `sbfsem-users remove` take effect immediately, not in a month.
        if username is not None and self.users.has(username):
            return username
        return None


# --- CLI: sbfsem-users -------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sbfsem-users",
        description="Maintain the web app's user list (passwords stored as salted hashes).",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="auth directory (default: $SBFSEM_AUTH_DIR or ~/.sbfsem/auth)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="add a user, or reset an existing user's password")
    add.add_argument("username")
    add.add_argument("--password", help="omit to be prompted (recommended)")
    invite = sub.add_parser(
        "invite",
        help="add a user with no password yet; prints a one-time link "
        "where they choose their own",
    )
    invite.add_argument("username")
    invite.add_argument(
        "--days", type=int, default=INVITE_TTL // 86400,
        help="how long the link stays redeemable (default: %(default)s)",
    )
    invite.add_argument(
        "--url", default=PUBLIC_URL,
        help="site the link points at (default: %(default)s)",
    )
    remove = sub.add_parser("remove", help="remove a user; their sessions stop working at once")
    remove.add_argument("username")
    sub.add_parser("list", help="list usernames and pending invites")
    args = parser.parse_args(argv)

    store = UserStore((args.dir or default_auth_dir()) / "users.json")
    if args.command == "add":
        password = args.password
        if password is None:
            password = getpass.getpass(f"Password for {args.username}: ")
            if password != getpass.getpass("Repeat: "):
                print("Passwords do not match.", file=sys.stderr)
                return 1
        if not password:
            print("Empty password refused.", file=sys.stderr)
            return 1
        verb = "Updated" if store.has(args.username) else "Added"
        store.add(args.username, password)
        print(f"{verb} {args.username!r} in {store.path}")
    elif args.command == "invite":
        code = store.invite(args.username, ttl=args.days * 86400)
        print(f"Invite for {args.username!r}, good for {args.days} day(s), one use:")
        print(f"  {args.url.rstrip('/')}/welcome?code={code}")
        print("Send them that link; they will choose their own password there.")
    elif args.command == "remove":
        if not store.has(args.username):
            print(f"No user {args.username!r} in {store.path}", file=sys.stderr)
            return 1
        store.remove(args.username)
        print(f"Removed {args.username!r}; {len(store.names())} user(s) remain")
    elif args.command == "list":
        names = store.names()
        if not names:
            print(f"No users in {store.path} (login is off)")
        for name in names:
            status = store.status(name)
            print(name if status == "active" else f"{name}  ({status})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
