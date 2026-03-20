"""
setup_demo_client.py
Assigns Demo Client to Olivia and books a couple of demo sessions
so the demo login shows all portal features properly.

Run via: railway run python setup_demo_client.py
"""
import os
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timedelta, timezone
from app import app, db, User, Session, Location
from sqlalchemy import func

def find_user(email):
    return User.query.filter(func.lower(User.email) == email.lower()).first()

with app.app_context():
    demo = find_user('client@fitforlife.com')
    olivia = find_user('olivia@fitforlife.com')

    if not demo:
        print('ERROR: Demo Client not found.')
        exit(1)
    if not olivia:
        print('ERROR: Olivia not found.')
        exit(1)

    # Assign Demo Client to Olivia
    demo.trainer_id = olivia.id
    db.session.commit()
    print(f'Assigned Demo Client to Olivia (trainer_id={olivia.id})')

    # Check for existing demo sessions
    existing = Session.query.filter_by(client_id=demo.id).count()
    if existing > 0:
        print(f'Demo Client already has {existing} session(s) — skipping session creation.')
    else:
        location = Location.query.first()
        if not location:
            print('No locations found — skipping session creation.')
        else:
            now = datetime.now(timezone.utc).replace(tzinfo=None)

            demo_sessions = [
                Session(
                    trainer_id=olivia.id,
                    client_id=demo.id,
                    location_id=location.id,
                    scheduled_at=now + timedelta(days=2, hours=2),
                    duration=60,
                    status='scheduled',
                    notes='Demo session',
                    created_by_id=olivia.id,
                ),
                Session(
                    trainer_id=olivia.id,
                    client_id=demo.id,
                    location_id=location.id,
                    scheduled_at=now - timedelta(days=3),
                    duration=60,
                    status='completed',
                    notes='Demo session',
                    created_by_id=olivia.id,
                ),
                Session(
                    trainer_id=olivia.id,
                    client_id=demo.id,
                    location_id=location.id,
                    scheduled_at=now - timedelta(days=7),
                    duration=60,
                    status='completed',
                    notes='Demo session',
                    created_by_id=olivia.id,
                ),
            ]
            for s in demo_sessions:
                db.session.add(s)
            db.session.commit()
            print(f'Created 3 demo sessions (1 upcoming, 2 completed) at {location.name}.')

    print('\nDemo Client setup complete.')
    print('  Login:    client@fitforlife.com')
    print('  Password: Client123')
    print('  Trainer:  Olivia')
    print('  Chat:     shows Olivia tab + Director tab')
