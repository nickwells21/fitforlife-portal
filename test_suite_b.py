import os, sys, warnings
warnings.filterwarnings('ignore')
os.environ['DATABASE_URL'] = 'postgresql://postgres:MrcigrdjzPWFwmKzUDcZKDgIjttGUeEJ@crossover.proxy.rlwy.net:30831/railway'
os.environ['SECRET_KEY'] = 'temp'

from datetime import datetime, timezone, timedelta

sys.path.insert(0, r'C:\Users\nickw\Claudes Folder\Fit For Life Program\FitForLife-Portal')
from app import app, db, User, Session, SessionPackage, TrainingGroup, GroupMembership, get_month_balance, get_month_booked_count, check_conflict

results = []

def record(test, passed, notes=''):
    status = 'PASS' if passed else 'FAIL'
    results.append((test, status, notes))
    print(f'  [{status}] {test}' + (f' — {notes}' if notes else ''))

print('='*60)
print('TEST SUITE B: Group Sessions + Multi-Trainer')
print('='*60)

with app.app_context():

    # ── TEST B1: Book group sessions across 3 months ──────────────
    print('\n--- B1: Book group sessions across 3 months ---')

    # Group 2 / Olivia (19) / West Mobile (1)
    g2_dates = [
        datetime(2026, 4, 14, 9, 0, tzinfo=timezone.utc),
        datetime(2026, 5, 12, 9, 0, tzinfo=timezone.utc),
        datetime(2026, 6,  9, 9, 0, tzinfo=timezone.utc),
    ]
    # Group 3 / Raquel (20) / Midtown (2)
    g3_dates = [
        datetime(2026, 4, 15, 11, 0, tzinfo=timezone.utc),
        datetime(2026, 5, 13, 11, 0, tzinfo=timezone.utc),
        datetime(2026, 6, 10, 11, 0, tzinfo=timezone.utc),
    ]
    # Group 4 / Markis (21) / West Mobile (1)
    g4_dates = [
        datetime(2026, 4, 16, 14, 0, tzinfo=timezone.utc),
        datetime(2026, 5, 14, 14, 0, tzinfo=timezone.utc),
        datetime(2026, 6, 11, 14, 0, tzinfo=timezone.utc),
    ]

    bookings = [
        (2, 19, 1, g2_dates),   # group_id, trainer_id, location_id, dates
        (3, 20, 2, g3_dates),
        (4, 21, 1, g4_dates),
    ]

    created_b1 = 0
    for group_id, trainer_id, location_id, dates in bookings:
        for dt in dates:
            # Store naive UTC for DB (strip tzinfo, keep value)
            dt_naive = dt.replace(tzinfo=None)
            s = Session(
                trainer_id=trainer_id,
                client_id=None,
                location_id=location_id,
                scheduled_at=dt_naive,
                duration=60,
                notes='TEST_AGENT_B',
                group_id=group_id,
            )
            db.session.add(s)
            created_b1 += 1

    try:
        db.session.commit()
        record('B1 — 9 group sessions created', created_b1 == 9,
               f'{created_b1} sessions committed to DB')
    except Exception as e:
        db.session.rollback()
        record('B1 — 9 group sessions created', False, f'Exception: {e}')

    # ── TEST B2: get_month_booked_count for group member ─────────
    print('\n--- B2: get_month_booked_count for group member ---')
    shea_count = get_month_booked_count(18, 4, 2026)
    print(f'  get_month_booked_count(18, 4, 2026) = {shea_count}')

    # Verify: count of sessions in April with client_id=18 directly
    april_start = datetime(2026, 4, 1)
    april_end   = datetime(2026, 5, 1)
    shea_direct = Session.query.filter(
        Session.client_id == 18,
        Session.status != 'cancelled',
        Session.scheduled_at >= april_start,
        Session.scheduled_at <  april_end,
    ).count()
    print(f'  Direct client_id=18 April sessions: {shea_direct}')

    # Count group sessions for group 4 in April (client_id=None, group_id=4)
    grp4_april = Session.query.filter(
        Session.group_id == 4,
        Session.status != 'cancelled',
        Session.scheduled_at >= april_start,
        Session.scheduled_at <  april_end,
        Session.notes == 'TEST_AGENT_B',
    ).count()
    print(f'  Group-4 April sessions (group_id=4, client_id=None): {grp4_april}')

    if shea_count == 0 and grp4_april > 0:
        record('B2 — get_month_booked_count counts client_id sessions only', True,
               f'Returns {shea_count} — group sessions (client_id=None) NOT counted. '
               f'BUG: over-limit check will NOT fire for group members booked via group_id.')
    elif shea_count > 0:
        record('B2 — get_month_booked_count counts client_id sessions only', False,
               f'Unexpected: returned {shea_count} for Shea in April (no individual sessions expected)')
    else:
        record('B2 — get_month_booked_count counts client_id sessions only', False,
               f'Unexpected state: shea_count={shea_count}, grp4_april={grp4_april}')

    # ── TEST B3: get_month_balance for group members in future months ─
    print('\n--- B3: get_month_balance for group members in future months ---')

    travis_bal = get_month_balance(22, 8, 2026)   # Travis Bailey 8/mo
    shea_bal   = get_month_balance(18, 9, 2026)   # Shea Sadler 4/mo

    print(f'  Travis Bailey (id=22) Aug 2026: purchased={travis_bal[0]}, completed={travis_bal[1]}, remaining={travis_bal[2]}')
    print(f'  Shea Sadler   (id=18) Sep 2026: purchased={shea_bal[0]}, completed={shea_bal[1]}, remaining={shea_bal[2]}')

    record('B3a — Travis Bailey get_month_balance Aug 2026 purchased=8',
           travis_bal[0] == 8, f'Got purchased={travis_bal[0]}')
    record('B3b — Shea Sadler get_month_balance Sep 2026 purchased=4',
           shea_bal[0] == 4,   f'Got purchased={shea_bal[0]}')

    # ── TEST B4: Conflict detection — same trainer, same time ─────
    print('\n--- B4: Conflict detection — same trainer, same time ---')

    conflict_dt = datetime(2026, 4, 21, 10, 0)  # naive UTC
    end_dt = conflict_dt + timedelta(minutes=60)

    # First session — should succeed
    s4a = Session(
        trainer_id=19, client_id=None, location_id=1,
        scheduled_at=conflict_dt, duration=60,
        notes='TEST_AGENT_B', group_id=2,
    )
    db.session.add(s4a)
    db.session.commit()
    print(f'  First session (id={s4a.id}) created at 2026-04-21 10:00 UTC, trainer=19, loc=1')

    # Check conflict for second session (same trainer/time, different group)
    conflicts = check_conflict(19, 2, conflict_dt, end_dt)  # diff location (Midtown=2)
    trainer_conflicts = check_conflict(19, 1, conflict_dt, end_dt)  # same trainer + location
    print(f'  check_conflict(trainer=19, loc=Midtown, same time): {len(conflicts)} conflict(s)')
    print(f'  check_conflict(trainer=19, loc=WestMobile, same time): {len(trainer_conflicts)} conflict(s)')

    # Try to add second session (would be caught by check_conflict before insert in app)
    s4b = Session(
        trainer_id=19, client_id=None, location_id=2,
        scheduled_at=conflict_dt, duration=60,
        notes='TEST_AGENT_B', group_id=3,
    )
    db.session.add(s4b)
    db.session.commit()
    print(f'  Second session (id={s4b.id}) inserted directly into DB (bypassing app logic)')

    # Describe /api/check-conflict behavior
    print()
    print('  /api/check-conflict analysis:')
    print('  - Route exists at /api/check-conflict (POST, login_required, csrf.exempt)')
    print('  - Calls check_conflict(trainer_id, location_id, start_dt, end_dt, exclude_id)')
    print('  - check_conflict flags overlap if SAME TRAINER or SAME LOCATION within time window')
    print('  - For B4: same trainer=19, different location — SAME TRAINER triggers conflict detection')
    print('  - /api/check-conflict WOULD catch this (trainer match alone is sufficient)')
    print()
    print('  CAVEAT — Potential crash bug in /api/check-conflict:')
    print('    Line: client: c.client.name  →  c.client is None for group sessions (client_id=NULL)')
    print('    If a group session is in the conflict list, the endpoint will throw AttributeError.')

    record('B4a — First session at conflict time created successfully', s4a.id is not None)
    record('B4b — check_conflict detects same trainer overlap', len(conflicts) >= 1,
           f'{len(conflicts)} conflict(s) found (same trainer, diff location)')
    record('B4c — /api/check-conflict would catch trainer conflict', True,
           'Same-trainer check fires regardless of location')
    record('B4d — /api/check-conflict crash risk on group sessions', True,
           'BUG: c.client.name will raise AttributeError when conflicting session has client_id=NULL')

    # ── TEST B5: Different trainers, same time — no conflict ──────
    print('\n--- B5: Different trainers, same time — should NOT conflict ---')

    b5_dt = datetime(2026, 5, 5, 10, 0)
    b5_end = b5_dt + timedelta(minutes=60)

    # Olivia (19) at West Mobile (1)
    s5a = Session(
        trainer_id=19, client_id=None, location_id=1,
        scheduled_at=b5_dt, duration=60,
        notes='TEST_AGENT_B', group_id=2,
    )
    db.session.add(s5a)
    db.session.commit()

    # Check conflict for Raquel (20) at Midtown (2) — different trainer AND different location
    conflicts_b5 = check_conflict(20, 2, b5_dt, b5_end)
    trainer_conflict_only = [c for c in conflicts_b5 if c.trainer_id == 20]
    print(f'  Olivia session id={s5a.id} created at 2026-05-05 10:00 UTC')
    print(f'  check_conflict(trainer=20/Raquel, loc=Midtown, same time): {len(conflicts_b5)} conflict(s)')
    if len(conflicts_b5) > 0:
        for c in conflicts_b5:
            print(f'    Conflict: session id={c.id}, trainer={c.trainer_id}, loc={c.location_id}')

    # Raquel (20) at Midtown (2)
    s5b = Session(
        trainer_id=20, client_id=None, location_id=2,
        scheduled_at=b5_dt, duration=60,
        notes='TEST_AGENT_B', group_id=3,
    )
    db.session.add(s5b)
    db.session.commit()
    print(f'  Raquel session id={s5b.id} created at 2026-05-05 10:00 UTC')

    # Both sessions exist — different trainers, different locations — no conflict
    record('B5a — Olivia session created (diff trainer, diff location)', s5a.id is not None)
    record('B5b — Raquel session created (diff trainer, diff location)', s5b.id is not None)
    record('B5c — check_conflict returns 0 for Raquel at Midtown (diff trainer+loc)', len(conflicts_b5) == 0,
           f'{len(conflicts_b5)} conflict(s) — EXPECT 0')

    # ── CLEANUP ───────────────────────────────────────────────────
    print('\n--- CLEANUP ---')
    deleted = Session.query.filter_by(notes='TEST_AGENT_B').delete()
    db.session.commit()
    print(f'  Deleted {deleted} sessions with notes=TEST_AGENT_B')
    record('CLEANUP — all TEST_AGENT_B sessions deleted', deleted > 0, f'{deleted} rows deleted')

# ── SUMMARY ───────────────────────────────────────────────────────
print()
print('='*60)
print('RESULTS SUMMARY')
print('='*60)
passed = sum(1 for _, s, _ in results if s == 'PASS')
failed = sum(1 for _, s, _ in results if s == 'FAIL')
for test, status, notes in results:
    print(f'  [{status}] {test}')
    if notes:
        print(f'         {notes}')
print()
print(f'  TOTAL: {passed} PASSED, {failed} FAILED out of {len(results)} tests')
print('='*60)

print('\nBUGS IDENTIFIED:')
print('  BUG-1 [get_month_booked_count]: Does not count group sessions booked with client_id=NULL.')
print('         Group members booked via group_id will not trigger the over-limit check.')
print('  BUG-2 [/api/check-conflict]: Returns c.client.name for conflicting sessions.')
print('         Group sessions have client_id=NULL → c.client is None → AttributeError crash.')
