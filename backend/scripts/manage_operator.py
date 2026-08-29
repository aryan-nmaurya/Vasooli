"""Create and maintain named dashboard operator accounts.

Run from ``backend/`` as ``uv run python -m scripts.manage_operator ...``. Passwords
are read from a TTY (or stdin when explicitly requested) and never accepted as a
command-line argument, where shell history and process listings could expose them.
"""

import argparse
import getpass
import re
import sys

from sqlmodel import Session, select

from app.core.clock import utcnow
from app.core.db import engine
from app.core.passwords import hash_password
from app.models import OperatorAccount

USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{2,64}$")


def _username(value: str) -> str:
    normalized = value.casefold()
    if not USERNAME_RE.fullmatch(normalized):
        raise argparse.ArgumentTypeError("use 2-64 letters, digits, underscores, or hyphens")
    return normalized


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("Password (minimum 12 characters): ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            raise SystemExit("Passwords did not match")
    if len(password) < 12:
        raise SystemExit("Password must be at least 12 characters")
    if len(password) > 512:
        raise SystemExit("Password must be at most 512 characters")
    return password


def _get(session: Session, username: str) -> OperatorAccount:
    account = session.exec(
        select(OperatorAccount).where(OperatorAccount.username == username)
    ).first()
    if account is None:
        raise SystemExit(f"No operator account named {username!r}")
    return account


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("username", type=_username)
    create.add_argument("--display-name", required=True)
    create.add_argument("--role", choices=("admin", "operator", "auditor"), default="operator")
    create.add_argument("--password-stdin", action="store_true")

    reset = sub.add_parser("reset-password")
    reset.add_argument("username", type=_username)
    reset.add_argument("--password-stdin", action="store_true")

    for command in ("disable", "enable", "unlock"):
        child = sub.add_parser(command)
        child.add_argument("username", type=_username)
    sub.add_parser("list")

    args = parser.parse_args()
    with Session(engine) as session:
        if args.command == "list":
            accounts = session.exec(
                select(OperatorAccount).order_by(OperatorAccount.username)
            ).all()
            for account in accounts:
                state = "active" if account.is_active else "disabled"
                print(f"{account.username}\t{account.role}\t{state}\t{account.display_name}")
            return

        if args.command == "create":
            if session.exec(
                select(OperatorAccount).where(OperatorAccount.username == args.username)
            ).first():
                raise SystemExit(f"Operator {args.username!r} already exists")
            account = OperatorAccount(
                username=args.username,
                display_name=args.display_name.strip(),
                role=args.role,
                password_hash=hash_password(_read_password(args.password_stdin)),
            )
            if not account.display_name:
                raise SystemExit("Display name must not be empty")
            session.add(account)
        else:
            account = _get(session, args.username)
            if args.command == "reset-password":
                account.password_hash = hash_password(_read_password(args.password_stdin))
                account.session_version += 1
                account.failed_login_attempts = 0
                account.locked_until = None
            elif args.command == "disable":
                account.is_active = False
                account.session_version += 1
            elif args.command == "enable":
                account.is_active = True
            elif args.command == "unlock":
                account.failed_login_attempts = 0
                account.locked_until = None
            account.updated_at = utcnow()
            session.add(account)

        session.commit()
        print(f"{args.command}: {account.username}")


if __name__ == "__main__":
    main()
