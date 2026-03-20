"""
add_peyton_to_group3.py
-----------------------
1. Adds Peyton Atkins to Group 3 (GroupMembership) if not already there.
2. Finds every SCHEDULED Group 3 session.
3. Adds a matching Session row for Peyton on each date she isn't already booked.

Run with:
  DATABASE_URL="postgresql://..." python add_peyton_to_group3.py
"""
from app import app, db, User, TrainingGroup, GroupMembership, Session
from datetime import datetime, timezone
from sqlalchemy import func

with app.app_context():

    # ── 1. Find Peyton ──────────────────────────────────────────────────────
    peyton = User.query.filter(
        func.lower(User.name).like('%peyton%'),
        User.role == 'client'
    ).first()

    if not peyton:
        # Try alternate spelling used in seed script
        peyton = User.query.filter(
            func.lower(User.name).like('%payton%'),
            User.role == 'client'
        ).first()

    if not peyton:
        print("ERROR: Could not find a client named Peyton (or Payton) in the database.")
        exit(1)

    print(f"Found client: {peyton.name} (id={peyton.id})")

    # ── 2. Find Group 3 ─────────────────────────────────────────────────────
    group = TrainingGroup.query.filter_by(name='Group 3').first()
    if not group:
        print("ERROR: Group 3 not found.")
        exit(1)

    print(f"Found group: {group.name} (id={group.id})")

    # ── 3. Add GroupMembership if missing ───────────────────────────────────
    existing_membership = GroupMembership.query.filter_by(
        group_id=group.id, client_id=peyton.id
    ).first()

    if existing_membership:
        print(f"{peyton.name} is already a member of {group.name} — skipping membership add.")
    else:
        db.session.add(GroupMembership(group_id=group.id, client_id=peyton.id))
        db.session.flush()
        print(f"Added {peyton.name} to {group.name}.")

    # ── 4. Find all scheduled Group 3 sessions ──────────────────────────────
    group_sessions = Session.query.filter(
        Session.group_id == group.id,
        Session.status == 'scheduled'
    ).order_by(Session.scheduled_at).all()

    print(f"\nFound {len(group_sessions)} scheduled Group 3 sessions.")

    # Get unique time slots (de-duped by scheduled_at — one entry per group slot)
    seen_times = {}
    for s in group_sessions:
        key = s.scheduled_at
        if key not in seen_times:
            seen_times[key] = s  # keep one representative per slot

    print(f"Unique time slots: {len(seen_times)}")

    # ── 5. Add Peyton to each slot she's missing from ───────────────────────
    added = 0
    skipped = 0

    for slot_dt, ref_session in seen_times.items():
        already = Session.query.filter_by(
            client_id=peyton.id,
            scheduled_at=slot_dt
        ).first()

        if already:
            skipped += 1
            continue

        new_sess = Session(
            trainer_id=ref_session.trainer_id,
            client_id=peyton.id,
            location_id=ref_session.location_id,
            scheduled_at=slot_dt,
            duration=ref_session.duration,
            status='scheduled',
            notes=ref_session.notes,
            group_id=group.id,
            created_by_id=ref_session.created_by_id,
        )
        db.session.add(new_sess)
        added += 1
        print(f"  + {slot_dt.strftime('%a %b %d %Y %I:%M%p')}")

    db.session.commit()

    print(f"\nDone. Added {added} sessions for {peyton.name}. {skipped} already existed.")
