"""
seed_trainers.py — Create trainer accounts and missing client accounts.
Run locally (needs DATABASE_URL in .env) or via:  railway run python seed_trainers.py
"""
import os
from dotenv import load_dotenv
load_dotenv()

from app import app, db, User

# ── Trainer accounts ───────────────────────────────────────────────────────────
TRAINERS = [
    {"name": "Olivia",         "email": "olivia@fitforlife.com",    "password": "Olivia2026",  "role": "trainer"},
    {"name": "Raquel",         "email": "raquel@fitforlife.com",    "password": "Raquel2026",  "role": "trainer"},
    {"name": "Markis Edwards", "email": "medwards@fitforlife.com",  "password": "Edwards2026", "role": "trainer"},
]

# ── Clients not yet in the system ─────────────────────────────────────────────
NEW_CLIENTS = [
    {"name": "Travis Bailey",  "email": "tbailey251@fitforlife.com",  "password": "Bailey2026",  "role": "client"},
    {"name": "Helen",          "email": "helen251@fitforlife.com",     "password": "Helen2026",   "role": "client"},
    # Akennya Bowers (user said "Bowers"; "Barnes" already seeded — creating both)
    {"name": "Akennya Bowers", "email": "abowers251@fitforlife.com",   "password": "Bowers2026",  "role": "client"},
]

ALL_USERS = TRAINERS + NEW_CLIENTS

with app.app_context():
    created = []
    skipped = []
    for u in ALL_USERS:
        if User.query.filter_by(email=u["email"]).first():
            skipped.append(f"  SKIP  {u['role']:7} {u['name']} ({u['email']})")
        else:
            new_user = User(name=u["name"], email=u["email"], role=u["role"])
            new_user.set_password(u["password"])
            db.session.add(new_user)
            created.append(f"  CREATE {u['role']:7} {u['name']} ({u['email']})  pw={u['password']}")

    db.session.commit()

    print("\n=== seed_trainers.py complete ===")
    if created:
        print(f"\nCreated ({len(created)}):")
        for line in created:
            print(line)
    if skipped:
        print(f"\nSkipped — already exist ({len(skipped)}):")
        for line in skipped:
            print(line)
    print()
