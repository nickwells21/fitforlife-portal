"""
Seeds 8 sample exercises into the exercises table.
Run: DATABASE_URL="postgresql://..." python seed_exercises.py
"""
from app import app, db
from app import Exercise, User

EXERCISES = [
    {
        'name': 'Barbell Bench Press',
        'muscle_group': 'Chest',
        'category': 'Strength',
        'instructions': (
            '1. Lie flat on a bench, feet firmly on the floor.\n'
            '2. Grip the bar slightly wider than shoulder-width.\n'
            '3. Unrack the bar and lower it to mid-chest with control.\n'
            '4. Press back up to full arm extension.\n'
            '5. Keep your shoulder blades retracted throughout.'
        ),
        'youtube_url': 'https://www.youtube.com/watch?v=rT7DgCr-3pg',
    },
    {
        'name': 'Pull-Up',
        'muscle_group': 'Back',
        'category': 'Strength',
        'instructions': (
            '1. Hang from a pull-up bar with an overhand grip, hands shoulder-width apart.\n'
            '2. Engage your core and pull your elbows toward your hips.\n'
            '3. Drive your chin above the bar.\n'
            '4. Lower with control back to a dead hang.\n'
            '5. Avoid kipping — keep the movement strict.'
        ),
        'youtube_url': 'https://www.youtube.com/watch?v=eGo4IYlbE5g',
    },
    {
        'name': 'Squat',
        'muscle_group': 'Legs',
        'category': 'Strength',
        'instructions': (
            '1. Stand with feet shoulder-width apart, toes slightly out.\n'
            '2. Brace your core and initiate by sitting back and down.\n'
            '3. Keep your chest tall and knees tracking over your toes.\n'
            '4. Squat until thighs are at least parallel to the floor.\n'
            '5. Drive through your heels to stand back up.'
        ),
        'youtube_url': 'https://www.youtube.com/watch?v=ultWZbUMPL8',
    },
    {
        'name': 'Plank',
        'muscle_group': 'Core',
        'category': 'Strength',
        'instructions': (
            '1. Start in a push-up position on your forearms.\n'
            '2. Keep your body in a straight line from head to heels.\n'
            '3. Squeeze your glutes and brace your core.\n'
            '4. Avoid letting your hips sag or rise.\n'
            '5. Hold for the prescribed time, breathing steadily.'
        ),
        'youtube_url': 'https://www.youtube.com/watch?v=pSHjTRCQxIw',
    },
    {
        'name': 'Treadmill Run',
        'muscle_group': 'Full Body',
        'category': 'Cardio',
        'instructions': (
            '1. Set the treadmill to your target pace.\n'
            '2. Maintain an upright posture with a slight forward lean.\n'
            '3. Land mid-foot, not on your heel.\n'
            '4. Swing arms naturally, elbows at 90°.\n'
            '5. Focus on consistent breathing rhythm.'
        ),
        'youtube_url': None,
    },
    {
        'name': 'Shoulder Press',
        'muscle_group': 'Shoulders',
        'category': 'Strength',
        'instructions': (
            '1. Sit or stand with dumbbells at shoulder height, palms forward.\n'
            '2. Press the weights directly overhead until arms are fully extended.\n'
            '3. Avoid arching your lower back — brace your core.\n'
            '4. Lower the weights back to shoulder height with control.\n'
            '5. Keep your wrists stacked over your elbows throughout.'
        ),
        'youtube_url': 'https://www.youtube.com/watch?v=qEwKCR5JCog',
    },
    {
        'name': 'Bicep Curl',
        'muscle_group': 'Arms',
        'category': 'Strength',
        'instructions': (
            '1. Stand holding dumbbells with arms fully extended, palms facing forward.\n'
            '2. Curl the weights up toward your shoulders, keeping elbows stationary.\n'
            '3. Squeeze the biceps at the top of the movement.\n'
            '4. Lower with control — don\'t let gravity do the work.\n'
            '5. Avoid swinging — keep your torso still.'
        ),
        'youtube_url': 'https://www.youtube.com/watch?v=ykJmrZ5v0Oo',
    },
    {
        'name': 'Hip Flexor Stretch',
        'muscle_group': 'Full Body',
        'category': 'Mobility',
        'instructions': (
            '1. Start in a lunge with your right foot forward, left knee on the floor.\n'
            '2. Shift your hips forward gently until you feel a stretch in the left hip flexor.\n'
            '3. Keep your torso tall and core lightly engaged.\n'
            '4. Hold for 30–60 seconds, breathing deeply.\n'
            '5. Switch sides and repeat.'
        ),
        'youtube_url': 'https://www.youtube.com/watch?v=YQmpO9VT2X4',
    },
]

with app.app_context():
    admin = User.query.filter_by(role='admin').first()
    admin_id = admin.id if admin else None

    added = 0
    for ex_data in EXERCISES:
        existing = Exercise.query.filter_by(name=ex_data['name']).first()
        if existing:
            print(f'  Already exists: {ex_data["name"]}')
            continue
        ex = Exercise(
            name=ex_data['name'],
            muscle_group=ex_data['muscle_group'],
            category=ex_data['category'],
            instructions=ex_data['instructions'],
            youtube_url=ex_data['youtube_url'],
            created_by_id=admin_id,
        )
        db.session.add(ex)
        added += 1
        print(f'  Added: {ex_data["name"]}')

    db.session.commit()
    print(f'\nDone. {added} exercise(s) added.')
