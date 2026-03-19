"""
seed_groups.py
Creates the 4 FFL training groups with their members.
Auto-assigns each group to the shared trainer of its members (if any).

Run with:
  DATABASE_URL="postgresql://..." python seed_groups.py
"""
from app import app, db, User, TrainingGroup, GroupMembership
from sqlalchemy import func

GROUPS = [
    ('Group 1', ['Clare', 'KB']),
    ('Group 2', ['Travis', 'Helen']),
    ('Group 3', ['Payton', 'Sarah', 'Jennifer']),
    ('Group 4', ['Shea', 'Mary Alice', 'Ruth']),
]


def find_client(name):
    return User.query.filter(
        func.lower(User.name).like(f'%{name.lower()}%'),
        User.role == 'client'
    ).first()


with app.app_context():
    for group_name, member_names in GROUPS:
        existing = TrainingGroup.query.filter_by(name=group_name).first()
        if existing:
            print(f'  "{group_name}" already exists — skipping.')
            continue

        group = TrainingGroup(name=group_name)
        db.session.add(group)
        db.session.flush()

        trainer_id = None
        for member_name in member_names:
            user = find_client(member_name)
            if user:
                db.session.add(GroupMembership(group_id=group.id, client_id=user.id))
                print(f'  + {user.name} -> {group_name}')
                if not trainer_id and user.trainer_id:
                    trainer_id = user.trainer_id
            else:
                print(f'  WARNING: client "{member_name}" not found in DB')

        group.trainer_id = trainer_id
        if trainer_id:
            t = db.session.get(User, trainer_id)
            print(f'  Assigned trainer: {t.name if t else trainer_id}')
        else:
            print(f'  No trainer assigned (assign via Admin > Groups)')

        db.session.commit()
        print(f'Created "{group_name}"')

    print('\nDone. Run migrate_add_groups.py first if you have not already.')
