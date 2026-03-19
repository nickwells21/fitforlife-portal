"""
migrate_trainer_assignment.py
1. Adds trainer_id column to users table (if not exists)
2. Assigns every client to their correct trainer

Run via:  railway run python3 migrate_trainer_assignment.py
"""
import os
from dotenv import load_dotenv
load_dotenv()

from app import app, db, User
from sqlalchemy import text, func

def find_user(email):
    """Case-insensitive email lookup."""
    return User.query.filter(func.lower(User.email) == email.lower()).first()

# ── Trainer → client roster ───────────────────────────────────────────────────
ASSIGNMENTS = {
    "olivia@fitforlife.com": [
        "JMorgan251@fitforlife.com",      # Jennifer Morgan
        "SWilliams251@fitforlife.com",    # Sarah Williams
        "PAtkins251@fitforlife.com",      # Peyton Atkins
    ],
    "raquel@fitforlife.com": [
        "tbailey251@fitforlife.com",      # Travis Bailey
        "helen251@fitforlife.com",        # Helen
    ],
    "nick@fitforlife.com": [
        "CMcConnell251@fitforlife.com",   # Clare McConnell
        "KBradham251@fitforlife.com",     # Kari Bradham
        "MMathison251@fitforlife.com",    # Mary Alice Mathison
        "SSadler251@fitforlife.com",      # Shea Sadler
        "RPappas251@fitforlife.com",      # Ruth Pappas
        "DMcGowin251@fitforlife.com",     # Debbie McGowin
        "AHerlihy251@fitforlife.com",     # Alison Herlihy
        "CShaw251@fitforlife.com",        # Charlene Shaw
    ],
    "medwards@fitforlife.com": [
        "abowers251@fitforlife.com",      # Akennya Bowers (new)
        "ABarnes251@fitforlife.com",      # Akennya Barnes (existing)
    ],
}

with app.app_context():
    # Step 1: Add column if missing
    with db.engine.connect() as conn:
        try:
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS trainer_id INTEGER REFERENCES users(id)"
            ))
            conn.commit()
            print("Column trainer_id added to users table.")
        except Exception as e:
            print(f"Column may already exist: {e}")

    # Step 2: Assign clients
    assigned = 0
    not_found = []
    for trainer_email, client_emails in ASSIGNMENTS.items():
        trainer = find_user(trainer_email)
        if not trainer:
            not_found.append(f"  TRAINER NOT FOUND: {trainer_email}")
            continue
        for ce in client_emails:
            client = find_user(ce)
            if not client:
                not_found.append(f"  CLIENT NOT FOUND: {ce}")
                continue
            client.trainer_id = trainer.id
            assigned += 1

    db.session.commit()
    print(f"Assigned {assigned} clients to their trainers.")

    if not_found:
        print("\nNot found — check emails:")
        for line in not_found:
            print(line)

    # Step 3: Summary
    print("\n─── Trainer Assignments ────────────────────────────────────────")
    all_users = User.query.filter(User.role.in_(['admin', 'trainer'])).order_by(User.role, User.name).all()
    for t in all_users:
        clients = User.query.filter_by(trainer_id=t.id).all()
        names = ', '.join(c.name for c in clients) if clients else '(none assigned)'
        print(f"  {t.name:20} [{t.role}]: {names}")
    print()
