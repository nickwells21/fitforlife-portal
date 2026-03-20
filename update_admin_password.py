"""One-time script: set Nick Wells admin password to Wells2026."""
import os
from app import app, db, User

with app.app_context():
    user = User.query.filter_by(email='nick@fitforlife.com').first()
    if not user:
        print("ERROR: nick@fitforlife.com not found.")
    else:
        user.set_password('Wells2026')
        user.name = 'Nick Wells'
        user.role = 'admin'
        db.session.commit()
        print(f"Done. nick@fitforlife.com → Wells2026 (role: {user.role})")
