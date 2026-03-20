"""
Find and delete duplicate user accounts.
Duplicates = same name (case-insensitive). Keeps the lowest ID (oldest).
Reassigns any sessions/packages from deleted accounts to the kept account.
Run: railway run python dedup_users.py
"""
from app import app, db, User, Session, SessionPackage, GroupMembership, Notification

with app.app_context():
    all_users = User.query.order_by(User.id).all()

    # Group by normalized name
    from collections import defaultdict
    by_name = defaultdict(list)
    for u in all_users:
        by_name[u.name.strip().lower()].append(u)

    dupes = {name: users for name, users in by_name.items() if len(users) > 1}

    if not dupes:
        print("No duplicate names found.")
    else:
        print(f"Found {len(dupes)} duplicate name(s):\n")
        for name, users in dupes.items():
            keep = users[0]  # lowest ID = oldest
            delete = users[1:]
            print(f"  '{users[0].name}'")
            print(f"    KEEP   → id={keep.id} | {keep.email} | {keep.role} | created {keep.created_at}")
            for d in delete:
                print(f"    DELETE → id={d.id} | {d.email} | {d.role} | created {d.created_at}")

                # Reassign sessions
                s_trainer = Session.query.filter_by(trainer_id=d.id).all()
                s_client  = Session.query.filter_by(client_id=d.id).all()
                for s in s_trainer:
                    s.trainer_id = keep.id
                for s in s_client:
                    s.client_id = keep.id

                # Reassign packages
                for p in SessionPackage.query.filter_by(client_id=d.id).all():
                    p.client_id = keep.id

                # Reassign group memberships (skip if keep already in same group)
                for gm in GroupMembership.query.filter_by(client_id=d.id).all():
                    exists = GroupMembership.query.filter_by(group_id=gm.group_id, client_id=keep.id).first()
                    if not exists:
                        gm.client_id = keep.id
                    else:
                        db.session.delete(gm)

                # Reassign notifications
                for n in Notification.query.filter_by(user_id=d.id).all():
                    n.user_id = keep.id

                db.session.delete(d)
                print(f"    → reassigned & deleted id={d.id}")

        db.session.commit()
        print("\nDone. All duplicates removed.")
