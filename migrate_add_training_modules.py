"""
Adds tables for: exercises, workouts, workout_exercises, programs,
program_days, program_assignments, progress_entries, exercise_logs,
workout_checkins.
Run: DATABASE_URL="postgresql://..." python migrate_add_training_modules.py
"""
from app import app, db
with app.app_context():
    db.create_all()
    print("All new tables created.")
