"""
migrate_add_groups.py
Creates training_groups and group_memberships tables,
and adds group_id column to sessions table.

Run with:
  DATABASE_URL="postgresql://..." python migrate_add_groups.py
"""
from app import app, db
from sqlalchemy import text

with app.app_context():
    db.create_all()
    print('Tables created/verified.')

    try:
        db.session.execute(text(
            'ALTER TABLE sessions ADD COLUMN group_id INTEGER REFERENCES training_groups(id)'
        ))
        db.session.commit()
        print('Added group_id column to sessions table.')
    except Exception as e:
        db.session.rollback()
        msg = str(e).lower()
        if 'already exists' in msg or 'duplicate' in msg:
            print('group_id column already exists — skipping.')
        else:
            print(f'Note: {e}')

    print('Migration complete.')
