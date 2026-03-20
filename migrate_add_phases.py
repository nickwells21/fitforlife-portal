"""
Migration: Add phase-based program tables.

Run locally against Railway DB:
  DATABASE_URL="postgresql://postgres:MrcigrdjzPWFwmKzUDcZKDgIjttGUeEJ@crossover.proxy.rlwy.net:30831/railway" python migrate_add_phases.py
"""
from app import app, db

with app.app_context():
    db.create_all()
    print("Phase tables created (program_phases, phase_days, progression_overrides).")
    print("Existing tables untouched.")
