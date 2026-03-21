"""
Adds movement_pattern and equipment columns to the exercises table.
Run: DATABASE_URL="postgresql://..." python migrate_exercise_fields.py
"""
from app import app, db

with app.app_context():
    with db.engine.connect() as conn:
        for col, ddl in [
            ('movement_pattern', 'ALTER TABLE exercises ADD COLUMN movement_pattern VARCHAR(80)'),
            ('equipment',        'ALTER TABLE exercises ADD COLUMN equipment VARCHAR(80)'),
        ]:
            try:
                conn.execute(db.text(ddl))
                conn.commit()
                print(f'  + added column: {col}')
            except Exception as e:
                conn.rollback()
                if 'already exists' in str(e).lower() or 'duplicate column' in str(e).lower():
                    print(f'  ~ column already exists: {col}')
                else:
                    print(f'  ! error on {col}: {e}')
    print('Done.')
