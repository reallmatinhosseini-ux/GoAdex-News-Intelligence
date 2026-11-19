"""Admin account management (command line only — there is no sign-up on the website).

    python3 -m app.admin create you@example.com      # prompts for a password (min 12 chars)
    python3 -m app.admin create you@example.com --generate   # prints a strong random password once
    python3 -m app.admin list
    python3 -m app.admin disable you@example.com
"""
import getpass
import secrets
import sys

from . import auth, db


def main(argv):
    db.init()
    auth.init()
    if len(argv) >= 2 and argv[0] == "create":
        email = argv[1]
        if "--generate" in argv:
            pw = secrets.token_urlsafe(18)
            auth.create_admin(email, pw)
            print(f"Admin {email} created. Password (shown once): {pw}")
            return
        pw = getpass.getpass("Password (min 12 characters): ")
        if pw != getpass.getpass("Repeat password: "):
            sys.exit("Passwords do not match.")
        auth.create_admin(email, pw)
        print(f"Admin {email} created.")
    elif argv[:1] == ["list"]:
        for r in db.rows("SELECT email, created_at, last_login, disabled FROM admins ORDER BY created_at"):
            print(r["email"], "| created", r["created_at"], "| last login", r["last_login"] or "never", "| disabled" if r["disabled"] else "")
    elif len(argv) == 2 and argv[0] == "disable":
        with db.tx() as c:
            c.execute("UPDATE admins SET disabled=1 WHERE email=?", (argv[1].lower(),))
            c.execute("DELETE FROM sessions WHERE email=?", (argv[1].lower(),))
        print(f"Disabled {argv[1]} and ended its sessions.")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
