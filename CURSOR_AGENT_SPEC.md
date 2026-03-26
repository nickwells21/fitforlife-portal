# FitForLife Portal — Notification & Achievement System Build Spec

> **Purpose:** This document gives an AI coding agent complete context to build the FFL Portal's notification engine, PR tracking system, badge/achievement dashboard, daily logging, and streak system. Read this ENTIRE document before generating any code or agent plan.

---

## TABLE OF CONTENTS

1. [Project Context & Architecture](#1-project-context--architecture)
2. [Current Database Schema](#2-current-database-schema)
3. [New & Modified Models](#3-new--modified-models)
4. [Notification Engine](#4-notification-engine)
5. [PR Detection System](#5-pr-detection-system)
6. [Badge & Achievement System](#6-badge--achievement-system)
7. [Streak System](#7-streak-system)
8. [Daily Logging System](#8-daily-logging-system)
9. [Achievement Dashboard UI Spec](#9-achievement-dashboard-ui-spec)
10. [Notification Feed UI Spec](#10-notification-feed-ui-spec)
11. [FFL Design System Tokens](#11-ffl-design-system-tokens)
12. [Integration Points & Hooks](#12-integration-points--hooks)
13. [File Paths & Line References](#13-file-paths--line-references)
14. [Build Constraints & Rules](#14-build-constraints--rules)
15. [Leaderboard (Streaks Only)](#15-leaderboard-streaks-only)
16. [Volume Tracking System](#16-volume-tracking-system)
17. [Time-Domain PR System](#17-time-domain-pr-system)
18. [Complete Notification Message Templates](#18-complete-notification-message-templates)

---

## 1. PROJECT CONTEXT & ARCHITECTURE

### What This App Is
FitForLife Portal is a personal training management platform built by Nick Wells for his gym business "Fit For Life." It handles session scheduling, client management, workout programming, and progress tracking for 16 active clients across 2 locations (West Mobile and Midtown) with 4 trainers.

### Tech Stack
- **Backend:** Python 3 / Flask 3.1.0 with Flask-SQLAlchemy, Flask-Login, Flask-WTF (CSRF)
- **Database:** PostgreSQL on Railway (production), SQLite locally
- **Frontend:** Server-side Jinja2 templates, vanilla JavaScript, NO React/Vue/build tools
- **CSS:** Single `static/css/style.css` (1,604 lines) with CSS custom properties
- **UI Framework:** Bootstrap 5.3.3 + Bootstrap Icons 1.11.3 (CDN)
- **Charts:** Chart.js 4 (CDN)
- **Deployment:** Railway with auto-deploy from GitHub (`nickwells21/fitforlife-portal`)
- **Auth:** Flask-Login with role-based access (admin, trainer, client)

### What We Are Building
A comprehensive engagement system that rewards EVERY client action with positive feedback. The core philosophy: **clients should not be able to complete a session without being celebrated multiple times.** This creates a daily login habit through a constant positive feedback loop:

```
Log data → see progress → get rewarded → want to log more
```

### Long-Term Business Goal
Sell this portal to local gyms at $49-79/month. The notification/tracking system is what differentiates a "$50 gym tool" from a "$150 coaching platform."

---

## 2. CURRENT DATABASE SCHEMA

### Models That Already Exist and Are LIVE in Production

#### User (line 63)
```python
id              Integer, PK
name            String(120), not null
email           String(120), unique, not null
password_hash   String(200), not null
role            String(20), default 'client'  # 'admin', 'trainer', 'client'
phone           String(20), nullable
trainerize_url  String(300), nullable
trainer_id      Integer, FK(users.id), nullable
created_at      DateTime
```

#### Session (line 126)
```python
id              Integer, PK
client_id       Integer, FK(users.id)
trainer_id      Integer, FK(users.id)
location_id     Integer, FK(locations.id)
group_id        Integer, FK(training_groups.id), nullable
scheduled_at    DateTime, not null
status          String(20), default 'scheduled'  # 'scheduled', 'completed', 'cancelled', 'no_show'
notes           Text, nullable
created_at      DateTime
```

#### Notification (line 197) — LIVE, but limited triggers
```python
id              Integer, PK
user_id         Integer, FK(users.id), not null
title           String(120), nullable
message         String(400), not null
notif_type      String(50), default 'session_completed'
                # Current types: session_completed, streak_milestone
                # Defined but unused: pr, badge, streak, milestone, volume, nudge
icon            String(50), nullable  # Bootstrap icon class
level           String(20), nullable  # bronze, silver, gold, platinum
session_id      Integer, FK(sessions.id), nullable
is_read         Boolean, default False
created_at      DateTime
```

#### Exercise (line 1969)
```python
id              Integer, PK
name            String(150), not null
muscle_group    String(80), nullable   # Legs, Back, Chest, Shoulders, Arms, Core, Hamstrings, Hips, Full Body
category        String(40), nullable
movement_pattern String(80), nullable  # Push, Pull, Squat, Hinge, Lunge, Carry, Rotation, Isolation, Cardio, Mobility
equipment       String(80), nullable   # Barbell, Dumbbell, Kettlebell, Cable, Machine, Smith Machine, Bodyweight, Resistance Band, Landmine, Rings, TRX, Cardio Machine
instructions    Text, nullable         # Coaching cues separated by " | "
youtube_url     String(300), nullable
created_by_id   Integer, FK(users.id), nullable
created_at      DateTime
```

#### ExerciseLog (line 2107)
```python
id              Integer, PK
client_id       Integer, FK(users.id), not null
exercise_id     Integer, FK(exercises.id), not null
log_date        Date, not null
sets_completed  Integer, nullable
reps_completed  String(20), nullable  # Can be "8" or "8,8,6" (per-set)
weight_lbs      Float, nullable
notes           String(200), nullable
created_at      DateTime
```

#### ProgressEntry (line 2092)
```python
id              Integer, PK
client_id       Integer, FK(users.id), not null
log_date        Date, not null
weight_lbs      Float, nullable
chest_in        Float, nullable
waist_in        Float, nullable
hips_in         Float, nullable
arms_in         Float, nullable
legs_in         Float, nullable
notes           Text, nullable
created_at      DateTime
```

#### WorkoutCheckin (line 2121)
```python
id              Integer, PK
client_id       Integer, FK(users.id), not null
assignment_id   Integer, FK(program_assignments.id), not null
week            Integer, not null
day             Integer, not null
completed_at    DateTime
# Unique constraint: (client_id, assignment_id, week, day)
```

#### Workout (line 1983)
```python
id              Integer, PK
name            String(150), not null
description     Text, nullable
created_by_id   Integer, FK(users.id), nullable
created_at      DateTime
```

#### WorkoutExercise (line 1995)
```python
id              Integer, PK
workout_id      Integer, FK(workouts.id), not null
exercise_id     Integer, FK(exercises.id), not null
order           Integer, default 0
sets            Integer, nullable
reps            String(20), nullable
rest_seconds    Integer, nullable
group_id        Integer, nullable        # Links exercises in superset/circuit
group_type      String(20), nullable     # 'superset' or 'circuit'
notes           Text, nullable
```

#### Program, ProgramPhase, PhaseDay, ProgramAssignment, ProgressionOverride
These are all live and handle workout programming — phases, weekly progressions, and exercise prescriptions. They don't need modification for this build.

### Models That Are DEFINED in Code but NOT Migrated to the Database

These models exist in app.py but their tables have NOT been created:

#### PersonalRecord (line 2135) — DORMANT
```python
id              Integer, PK
user_id         Integer, FK(users.id), not null
exercise_id     Integer, FK(exercises.id), nullable
pr_type         String(30), not null
                # '1rm', '3rm', '5rm', '8rm', '10rm', '15rm',
                # 'time' (longest hold), 'speed' (fastest),
                # 'max_reps', 'distance', 'volume_session', 'volume_muscle'
value           Float, not null
unit            String(20), not null  # 'lbs', 'seconds', 'minutes', 'meters', 'miles', 'reps', 'lbs_volume'
previous_value  Float, nullable
muscle_group    String(80), nullable
achieved_at     DateTime
```

#### BadgeDefinition (line 2154) — DORMANT
```python
id              Integer, PK
key             String(60), unique, not null
name            String(100), not null
description     String(300), not null
icon            String(50), not null      # Bootstrap icon class
category        String(40), not null      # workout, pr, streak, consistency, special, volume
level           String(20), default 'bronze'  # bronze, silver, gold, platinum
threshold       Integer, nullable
repeatable      Boolean, default False
```

#### UserBadge (line 2168) — DORMANT
```python
id              Integer, PK
user_id         Integer, FK(users.id), not null
badge_id        Integer, FK(badge_definitions.id), not null
earned_at       DateTime
```

#### Streak (line 2179) — DORMANT
```python
id              Integer, PK
user_id         Integer, FK(users.id), not null
streak_type     String(30), not null  # 'workout', 'logging', 'weekly_target', 'checkin'
current_count   Integer, default 0
longest_count   Integer, default 0
last_activity_date  Date, nullable
freeze_used_this_week  Boolean, default False
# Unique constraint: (user_id, streak_type)
```

#### RepMilestone (line 2193) — DORMANT
```python
id              Integer, PK
user_id         Integer, FK(users.id), not null
exercise_id     Integer, FK(exercises.id), not null
milestone       Integer, not null   # 100, 200, 300, 400, 500, 600
reached_at      DateTime
```

---

## 3. NEW & MODIFIED MODELS

### Models to ADD (do not exist yet)

#### DailyLog — New Model
```python
class DailyLog(db.Model):
    __tablename__ = 'daily_logs'
    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    log_date        = db.Column(db.Date, nullable=False)
    log_type        = db.Column(db.String(30), nullable=False)
        # 'water', 'sleep', 'meal', 'weighin'
    # Water: value = oz consumed
    # Sleep: value = hours, quality = 1-5 rating
    # Meal: value = null, notes = description or photo URL
    # Weighin: value = weight in lbs
    value           = db.Column(db.Float, nullable=True)
    quality         = db.Column(db.Integer, nullable=True)  # 1-5, for sleep
    notes           = db.Column(db.String(300), nullable=True)
    photo_url       = db.Column(db.String(500), nullable=True)  # for meal photos
    created_at      = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', foreign_keys=[user_id])
    __table_args__ = (db.UniqueConstraint('user_id', 'log_date', 'log_type', name='uq_daily_log'),)
```

#### ExerciseVolumeTotal — New Model
```python
class ExerciseVolumeTotal(db.Model):
    """Running cumulative volume per user per exercise. Updated on every log.
    Avoids recalculating from scratch every time we check milestones."""
    __tablename__ = 'exercise_volume_totals'
    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    exercise_id     = db.Column(db.Integer, db.ForeignKey('exercises.id'), nullable=False)
    total_volume_lbs = db.Column(db.Float, default=0.0)   # Cumulative weight x reps
    total_reps      = db.Column(db.Integer, default=0)     # Cumulative total reps
    session_count   = db.Column(db.Integer, default=0)     # How many times they've done this exercise
    best_session_volume = db.Column(db.Float, default=0.0) # Single-session volume record
    last_updated    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', foreign_keys=[user_id])
    exercise = db.relationship('Exercise')
    __table_args__ = (db.UniqueConstraint('user_id', 'exercise_id', name='uq_user_exercise_volume'),)
```

#### WorkoutVolumeRecord — New Model
```python
class WorkoutVolumeRecord(db.Model):
    """Tracks best total volume for a named workout (all exercises combined)."""
    __tablename__ = 'workout_volume_records'
    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    workout_id      = db.Column(db.Integer, db.ForeignKey('workouts.id'), nullable=False)
    best_volume_lbs = db.Column(db.Float, default=0.0)
    achieved_at     = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', foreign_keys=[user_id])
    workout = db.relationship('Workout')
    __table_args__ = (db.UniqueConstraint('user_id', 'workout_id', name='uq_user_workout_volume'),)
```

### Models to MODIFY

#### PersonalRecord — Add these pr_type values
Expand the `pr_type` field to support:
```
'1rm', '3rm', '5rm', '8rm', '10rm'          — Rep max PRs (weight for N reps)
'volume_session'                              — Best single-session volume for an exercise
'volume_exercise_milestone'                   — Cumulative volume milestone reached
'volume_workout'                              — Best total workout volume (all exercises)
'longest_hold'                                — Time-domain: plank, wall sit, dead hang, L-sit (seconds, higher = better)
'fastest_time'                                — Time-domain: mile, 400m, shuttle, rowing (seconds, LOWER = better)
'max_reps'                                    — Most unbroken reps (push-ups, pull-ups, etc.)
```

#### ExerciseLog — Add time-domain fields
```python
# ADD these columns to ExerciseLog:
duration_seconds  = db.Column(db.Float, nullable=True)   # For holds (plank, wall sit, dead hang)
time_seconds      = db.Column(db.Float, nullable=True)   # For timed activities (mile run, 400m)
distance          = db.Column(db.Float, nullable=True)    # For distance-based (meters, miles)
distance_unit     = db.Column(db.String(10), nullable=True)  # 'meters', 'miles', 'yards'
```

#### Exercise — Add `is_timed` and `is_hold` flags
```python
# ADD these columns to Exercise:
is_timed          = db.Column(db.Boolean, default=False)  # True for runs, rows, bike sprints
is_hold           = db.Column(db.Boolean, default=False)  # True for planks, wall sits, dead hangs
```

#### Notification — Add more notif_types
Expand `notif_type` to include all of these:
```
'session_completed'           — (existing) Session marked complete by trainer
'streak_milestone'            — (existing) Weekly streak milestone hit
'pr_rep_max'                  — New rep max PR (1RM, 3RM, 5RM, 8RM, 10RM)
'pr_volume_exercise'          — New volume record for a specific exercise
'pr_volume_workout'           — New total volume record for a workout
'pr_volume_milestone'         — Cumulative volume milestone per exercise (1k, 5k, 10k, etc.)
'pr_longest_hold'             — New duration record (plank, dead hang, etc.)
'pr_fastest_time'             — New speed record (mile, 400m, etc.)
'pr_max_reps'                 — New max unbroken reps record
'badge_earned'                — Badge/achievement unlocked
'streak_started'              — Streak begins
'streak_broken'               — Streak ended (with comeback encouragement)
'comeback_nudge'              — Been away X days, come back!
'workout_completed'           — Full workout logged with volume summary
'exercise_first'              — First time ever doing an exercise
'exercise_count_milestone'    — Nth time doing an exercise (10, 25, 50, 100)
'exercise_rep_milestone'      — Total rep milestone per exercise (100, 500, 1k, 5k, 10k)
'weekly_target_hit'           — Hit weekly workout frequency goal
'consistency_badge'           — Multi-week consistency (e.g. 4x/week for 3 weeks)
'daily_log_water'             — Water logged
'daily_log_sleep'             — Sleep logged
'daily_log_meal'              — Meal logged
'daily_log_weighin'           — Weigh-in recorded
'early_bird'                  — Worked out before 7 AM
'night_owl'                   — Worked out after 8 PM
'week_started'                — First workout of the week
'month_summary'               — Monthly recap
```

---

## 4. NOTIFICATION ENGINE

### Central Function: `create_notification()`

```python
def create_notification(user_id, notif_type, message, title=None, icon=None, level=None, session_id=None, exercise_id=None):
    """Central notification creation point. ALL notifications flow through here."""
    notif = Notification(
        user_id=user_id,
        title=title,
        message=message,
        notif_type=notif_type,
        icon=icon,
        level=level,
        session_id=session_id,
    )
    db.session.add(notif)
    return notif
```

### Trigger Function: `process_workout_log()`

This is the BRAIN. Called after a workout is submitted via the workout_log form. It receives all the exercise data for that session and runs every check:

```python
def process_workout_log(user_id, workout_id, exercise_data_list, log_date):
    """
    Called after workout form submission.
    exercise_data_list = [
        {'exercise_id': 1, 'sets': 3, 'reps': '8,8,6', 'weight_lbs': 185.0,
         'duration_seconds': None, 'time_seconds': None},
        ...
    ]

    Runs ALL of the following checks:
    1. Rep max PR detection (per exercise)
    2. Session volume PR detection (per exercise)
    3. Cumulative volume milestone check (per exercise)
    4. Total workout volume PR check
    5. Time-domain PR detection (holds and timed)
    6. Max unbroken reps detection
    7. Exercise count milestone (1st, 10th, 25th, 50th, 100th time)
    8. Total rep milestone (100, 500, 1000, 5000, 10000 per exercise)
    9. Badge threshold checks
    10. Streak updates
    11. Time-of-day checks (early bird / night owl)
    12. Week-started check (first workout this week)
    13. Weekly target check (hit 3x or 4x this week)
    14. Workout completed notification with volume summary
    """
```

### Trigger Function: `process_daily_log()`

Called after any daily logging action (water, sleep, meal, weigh-in):

```python
def process_daily_log(user_id, log_type, value=None):
    """
    Runs:
    1. Create daily_log notification (water/sleep/meal/weighin)
    2. Update logging streak
    3. Check logging streak milestones (3, 7, 14, 30, 60, 90 days)
    4. Check daily-logging badges (Hydro Homie, Sleep Scholar, etc.)
    """
```

### Trigger Function: `check_comeback_nudge()`

Run on a schedule or on login — checks if user has been inactive:

```python
def check_comeback_nudge(user_id):
    """
    If user has no activity for 2+ days and had an active streak:
    - 2 days: "2 days off your streak — session tomorrow?"
    - 5 days: "We miss you! Your streak is waiting."
    - 7+ days: "Welcome back anytime. Let's restart together."
    """
```

---

## 5. PR DETECTION SYSTEM

### 5A. Rep Max PRs (1RM, 3RM, 5RM, 8RM, 10RM)

**When:** After each exercise is logged with weight + reps.

**Logic:**
```
For each set logged:
  reps = number of reps in this set
  weight = weight used

  Determine which RM categories this qualifies for:
    if reps == 1:  check against 1RM
    if reps <= 3:  check against 3RM  (use the weight, not estimated)
    if reps <= 5:  check against 5RM
    if reps <= 8:  check against 8RM
    if reps <= 10: check against 10RM

  For each qualifying category:
    previous_best = PersonalRecord.query.filter_by(
        user_id=user_id, exercise_id=exercise_id, pr_type=category
    ).order_by(PersonalRecord.value.desc()).first()

    if no previous_best OR weight > previous_best.value:
      Create PersonalRecord(pr_type=category, value=weight, previous_value=old, unit='lbs')
      Create notification: "NEW {category} on {exercise_name} — {weight} lbs!"
      If previous_best: append " Beat your old record by {diff} lbs"
```

**Important:** A single heavy set can trigger MULTIPLE PR notifications. If someone does 225 lbs x 3, that could be a new 3RM AND a new 5RM (if their previous 5RM was lower). This is intentional — celebrate everything.

### 5B. Volume PRs Per Movement (Cumulative Milestones)

**When:** After each exercise log.

**Milestones:** 1,000 / 5,000 / 10,000 / 25,000 / 50,000 / 100,000 / 250,000 / 500,000 / 1,000,000 lbs

**Logic:**
```
session_volume = weight_lbs * total_reps_this_session (for this exercise)

Update ExerciseVolumeTotal:
  total.total_volume_lbs += session_volume
  total.total_reps += total_reps_this_session
  total.session_count += 1

Check milestone thresholds:
  for milestone in [1000, 5000, 10000, 25000, 50000, 100000, 250000, 500000, 1000000]:
    old_volume = total.total_volume_lbs - session_volume
    if old_volume < milestone and total.total_volume_lbs >= milestone:
      Create PersonalRecord(pr_type='volume_exercise_milestone', value=milestone)
      Create notification: "{format_number(milestone)} lbs moved on {exercise_name}!"
```

### 5C. Best Session Volume Per Exercise

**When:** After all sets of an exercise are logged in a single workout.

**Logic:**
```
session_volume = sum(weight * reps for each set of this exercise today)

if session_volume > ExerciseVolumeTotal.best_session_volume:
  Update best_session_volume
  Create PersonalRecord(pr_type='volume_session', value=session_volume)
  Create notification: "New volume record on {exercise_name} — {session_volume} lbs in one session!"
```

### 5D. Best Total Workout Volume

**When:** After entire workout is submitted.

**Logic:**
```
total_workout_volume = sum(all exercise session volumes)

record = WorkoutVolumeRecord.query.filter_by(user_id, workout_id).first()
if not record or total_workout_volume > record.best_volume_lbs:
  Update or create record
  Create notification: "Biggest {workout_name} EVER — {total_workout_volume} lbs total volume!"
```

### 5E. Time-Domain PRs — Longest Hold

**Applies to:** Exercises where `exercise.is_hold == True` (plank, wall sit, dead hang, L-sit, farmer's carry hold)

**When:** After logging `duration_seconds` for a hold exercise.

**Logic:**
```
previous_best = PersonalRecord.query.filter_by(
    user_id=user_id, exercise_id=exercise_id, pr_type='longest_hold'
).order_by(PersonalRecord.value.desc()).first()

if not previous_best or duration_seconds > previous_best.value:
  Create PersonalRecord(pr_type='longest_hold', value=duration_seconds, unit='seconds')
  formatted = format_duration(duration_seconds)  # "2:45"
  Create notification: "New longest {exercise_name} — {formatted}!"
  If previous_best:
    diff = duration_seconds - previous_best.value
    append " That's {diff} seconds longer than your best!"
```

### 5F. Time-Domain PRs — Fastest Time

**Applies to:** Exercises where `exercise.is_timed == True` (mile run, 400m sprint, 500m row, shuttle run, bike sprint)

**When:** After logging `time_seconds` for a timed exercise.

**Logic (LOWER is better):**
```
previous_best = PersonalRecord.query.filter_by(
    user_id=user_id, exercise_id=exercise_id, pr_type='fastest_time'
).order_by(PersonalRecord.value.asc()).first()  # ASC because lower = better

if not previous_best or time_seconds < previous_best.value:
  Create PersonalRecord(pr_type='fastest_time', value=time_seconds, unit='seconds')
  formatted = format_time(time_seconds)  # "8:12"
  Create notification: "Fastest {exercise_name} — {formatted}!"
  If previous_best:
    diff = previous_best.value - time_seconds
    append " You shaved {diff} seconds off!"
```

### 5G. Max Unbroken Reps

**Applies to:** Bodyweight exercises logged with high rep single sets (push-ups, pull-ups, sit-ups, dips)

**When:** After logging a set with reps but no weight (or bodyweight exercises).

**Logic:**
```
if single_set_reps > previous max_reps PersonalRecord:
  Create PersonalRecord(pr_type='max_reps', value=single_set_reps, unit='reps')
  Create notification: "New max {exercise_name} — {reps} unbroken reps!"
```

---

## 6. BADGE & ACHIEVEMENT SYSTEM

### Existing Badge Seed Data (34 badges already defined in code)

| Key | Name | Category | Level | Threshold |
|-----|------|----------|-------|-----------|
| `first_timer` | First Timer | workout | bronze | 1 |
| `ten_club` | 10 Club | workout | bronze | 10 |
| `quarter_century` | Quarter Century | workout | silver | 25 |
| `half_century` | Half Century | workout | gold | 50 |
| `century_club` | Century Club | workout | platinum | 100 |
| `hat_trick` | Hat Trick | consistency | bronze | 3 (per week, repeatable) |
| `iron_week` | Iron Week | consistency | silver | 5 (per week, repeatable) |
| `pr_collector_10` | PR Collector | pr | bronze | 10 |
| `pr_collector_25` | PR Hunter | pr | silver | 25 |
| `pr_collector_50` | PR Machine | pr | gold | 50 |
| `pr_collector_100` | PR Legend | pr | platinum | 100 |
| `double_up` | Double Up | pr | silver | 2 PRs/week (repeatable) |
| `triple_up` | Triple Up | pr | gold | 3 PRs/week (repeatable) |
| `early_bird` | Early Bird | special | bronze | Before 7 AM (repeatable) |
| `night_owl` | Night Owl | special | bronze | After 8 PM (repeatable) |
| `muscle_map` | Muscle Map | consistency | gold | All muscle groups in 1 week (repeatable) |
| `variety_pack` | Variety Pack | consistency | silver | 8 exercises in 1 month |
| `volume_10k` | Volume Rising | volume | bronze | 10,000 lbs total |
| `volume_25k` | Volume Builder | volume | silver | 25,000 lbs total |
| `volume_50k` | Volume King | volume | gold | 50,000 lbs total |
| `volume_100k` | Volume Legend | volume | platinum | 100,000 lbs total |
| `consistent` | Consistent | streak | gold | 8 weeks |
| `unbreakable` | Unbreakable | streak | platinum | 30-day logging streak |
| `the_wall` | The Wall | special | gold | 3-minute plank |
| `comeback_kid` | Comeback Kid | special | silver | Return after 7+ days |

### NEW Badges to Add (expanding to ~70+ total)

#### Volume Per Movement Badges
| Key | Name | Description | Level | Threshold |
|-----|------|-------------|-------|-----------|
| `volume_exercise_1k` | Mover | 1,000 lbs on a single exercise | bronze | 1,000 |
| `volume_exercise_5k` | Grinder | 5,000 lbs on a single exercise | bronze | 5,000 |
| `volume_exercise_10k` | Workhorse | 10,000 lbs on a single exercise | silver | 10,000 |
| `volume_exercise_25k` | Volume Addict | 25,000 lbs on a single exercise | silver | 25,000 |
| `volume_exercise_50k` | Iron Mover | 50,000 lbs on a single exercise | gold | 50,000 |
| `volume_exercise_100k` | Volume Monster | 100,000 lbs on a single exercise | platinum | 100,000 |

These are **per exercise** — so a client could earn "Volume Addict" on bench press, squats, AND deadlifts separately. Each one generates a notification.

#### Time Domain Badges
| Key | Name | Description | Level |
|-----|------|-------------|-------|
| `plank_1min` | Core Starter | Hold a 1-minute plank | bronze |
| `plank_2min` | Core Warrior | Hold a 2-minute plank | silver |
| `plank_3min` | The Wall | Hold a 3-minute plank | gold |
| `plank_5min` | Plank Legend | Hold a 5-minute plank | platinum |
| `dead_hang_30s` | Hanging On | 30-second dead hang | bronze |
| `dead_hang_1min` | Iron Grip | 1-minute dead hang | silver |
| `dead_hang_2min` | Gorilla Grip | 2-minute dead hang | gold |
| `mile_sub_10` | Runner | Sub-10 minute mile | bronze |
| `mile_sub_8` | Speed Demon | Sub-8 minute mile | silver |
| `mile_sub_7` | Road Warrior | Sub-7 minute mile | gold |
| `mile_sub_6` | Lightning | Sub-6 minute mile | platinum |

#### Exercise Loyalty Badges
| Key | Name | Description | Level | Threshold |
|-----|------|-------------|-------|-----------|
| `exercise_10x` | Getting Started | Do an exercise 10 times | bronze | 10 |
| `exercise_25x` | Regular | Do an exercise 25 times | bronze | 25 |
| `exercise_50x` | Dedicated | Do an exercise 50 times | silver | 50 |
| `exercise_100x` | Specialist | Do an exercise 100 times | gold | 100 |
| `exercise_200x` | Master | Do an exercise 200 times | platinum | 200 |

#### Rep Milestone Badges (per exercise)
| Key | Name | Description | Level | Threshold |
|-----|------|-------------|-------|-----------|
| `reps_100` | Rep Starter | 100 total reps of an exercise | bronze | 100 |
| `reps_500` | 500 Club | 500 total reps of an exercise | silver | 500 |
| `reps_1000` | Thousand Repper | 1,000 total reps of an exercise | gold | 1,000 |
| `reps_5000` | 5K Repper | 5,000 total reps of an exercise | platinum | 5,000 |
| `reps_10000` | Ten Thousand | 10,000 total reps of an exercise | platinum | 10,000 |

#### Daily Logging Badges
| Key | Name | Description | Level | Threshold |
|-----|------|-------------|-------|-----------|
| `hydro_7` | Hydro Homie | Log water 7 days in a row | bronze | 7 |
| `hydro_30` | Water Warrior | Log water 30 days in a row | silver | 30 |
| `sleep_7` | Rest Up | Log sleep 7 days in a row | bronze | 7 |
| `sleep_30` | Sleep Scholar | Log sleep 30 days in a row | silver | 30 |
| `meal_7` | Fuel Up | Log meals 7 days in a row | bronze | 7 |
| `meal_30` | Nutrition Pro | Log meals 30 days in a row | silver | 30 |
| `weighin_7` | Scale Starter | Weigh in 7 days in a row | bronze | 7 |
| `weighin_30` | Weight Watcher | Weigh in 30 days in a row | silver | 30 |
| `daily_all` | Full Logger | Log water + sleep + meal + weigh-in in one day | gold | 1 |
| `daily_all_7` | Data Machine | Full log every category 7 days straight | platinum | 7 |

#### Streak Badges
| Key | Name | Description | Level | Threshold |
|-----|------|-------------|-------|-----------|
| `streak_3` | Getting Going | 3-day streak | bronze | 3 |
| `streak_7` | Week Warrior | 7-day streak | bronze | 7 |
| `streak_14` | Two Weeks Strong | 14-day streak | silver | 14 |
| `streak_30` | Month of Iron | 30-day streak | silver | 30 |
| `streak_60` | Two Month Titan | 60-day streak | gold | 60 |
| `streak_90` | Quarter Beast | 90-day streak | gold | 90 |
| `streak_180` | Half Year Hero | 180-day streak | platinum | 180 |
| `streak_365` | Year of Iron | 365-day streak | platinum | 365 |

### Badge Rarity System

Map to the React component's rarity colors, adapted for FFL theme:
```
common    → border-left: 3px solid var(--text-muted)   (gray)
rare      → border-left: 3px solid var(--accent)       (FFL blue)
epic      → border-left: 3px solid #a855f7             (purple)
legendary → border-left: 3px solid var(--amber)        (gold/amber)
```

Map from badge level:
- `bronze` → common
- `silver` → rare
- `gold` → epic
- `platinum` → legendary

### Badge Check Function

```python
def check_badges(user_id, context):
    """
    Called after every action. Context tells us what to check:
    context = {
        'action': 'workout_logged' | 'daily_log' | 'exercise_logged' | etc.,
        'exercise_id': int or None,
        'workout_id': int or None,
        'log_type': str or None,
    }

    For each badge definition:
      1. Skip if user already has it (unless repeatable)
      2. Calculate current progress toward threshold
      3. If threshold met: award badge + create notification

    Returns list of newly awarded badges for the notification engine.
    """
```

---

## 7. STREAK SYSTEM

### Streak Types

| Type | How it increments | Resets when |
|------|-------------------|-------------|
| `workout` | Each day with a completed workout | 2 consecutive days with no workout (1 freeze/week) |
| `logging` | Each day with ANY log entry (exercise, water, sleep, meal, weighin) | 2 consecutive days with no log |
| `weekly_target` | Each week hitting the target (3x or 4x per week, set per client) | A week below target |
| `checkin` | Each day the client logs into the app | Missing a day |

### Streak Freeze
- Each streak type gets 1 freeze per week
- If a client misses 1 day, the streak continues but `freeze_used_this_week` = True
- If they miss a 2nd day in the same week, streak breaks
- `freeze_used_this_week` resets every Monday

### Streak Milestone Notifications
Trigger at: 3, 7, 14, 21, 30, 45, 60, 90, 120, 150, 180, 270, 365 days

### Streak Update Logic
```python
def update_streak(user_id, streak_type):
    streak = Streak.query.filter_by(user_id=user_id, streak_type=streak_type).first()
    if not streak:
        streak = Streak(user_id=user_id, streak_type=streak_type)
        db.session.add(streak)

    today = date.today()

    if streak.last_activity_date == today:
        return  # Already logged today

    if streak.last_activity_date == today - timedelta(days=1):
        # Consecutive day — increment
        streak.current_count += 1
    elif streak.last_activity_date == today - timedelta(days=2) and not streak.freeze_used_this_week:
        # Missed 1 day — use freeze
        streak.current_count += 1
        streak.freeze_used_this_week = True
    else:
        # Streak broken — reset
        streak.current_count = 1

    streak.last_activity_date = today
    if streak.current_count > streak.longest_count:
        streak.longest_count = streak.current_count

    # Check milestones
    milestones = [3, 7, 14, 21, 30, 45, 60, 90, 120, 150, 180, 270, 365]
    if streak.current_count in milestones:
        create_notification(user_id, 'streak_milestone',
            f"🔥 {streak.current_count}-day {streak_type} streak! You're on fire!")

    db.session.commit()
```

---

## 8. DAILY LOGGING SYSTEM

### Routes to Add

```
GET  /daily-log                  — Daily logging page (all 4 categories)
POST /daily-log/water            — Log water intake
POST /daily-log/sleep            — Log sleep
POST /daily-log/meal             — Log meal
POST /daily-log/weighin          — Log weigh-in
GET  /api/daily-log/today        — Get today's log status (for dashboard widget)
```

### Daily Log Page UI

A simple, fast-tap interface. The user should be able to log all 4 things in under 30 seconds:

```
┌─────────────────────────────────────────────────┐
│  📅 Daily Check-In — March 25, 2026             │
├─────────────────────────────────────────────────┤
│                                                   │
│  💧 Water     [  ___  oz ]  [+ 8oz] [+ 16oz]    │
│               Today: 64 oz ✓                      │
│                                                   │
│  😴 Sleep     [  ___  hrs ]  Quality: ⭐⭐⭐⭐☆   │
│               Last night: 7.5 hrs ✓               │
│                                                   │
│  🍽️ Meal      [ Snap Photo ] or [ Quick Note ]   │
│               Logged: Breakfast ✓, Lunch ✓        │
│                                                   │
│  ⚖️ Weigh-In  [  ___  lbs ]                      │
│               Today: 185.2 lbs ✓                  │
│                                                   │
└─────────────────────────────────────────────────┘
```

Each entry triggers:
1. Create DailyLog record
2. Create notification (e.g. "Hydration on point. Keep it flowing.")
3. Update logging streak
4. Check daily-logging badges

---

## 9. ACHIEVEMENT DASHBOARD UI SPEC

### Route
```
GET /achievements    — Client achievement/badge dashboard
```

### Page Layout (Adapted from React Component to Jinja/Bootstrap)

This is a NEW page in the portal. It should use `{% extends 'base.html' %}` and follow the FFL dark theme.

```
┌──────────────────────────────────────────────────────────────┐
│  🏆 Your Achievements                                        │
│  Track your progress and unlock amazing rewards               │
├──────────────────────────────────────────────────────────────┤
│                                                                │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐        │
│  │ UNLOCKED │ │  STREAK  │ │ WORKOUTS │ │ PROGRESS │        │
│  │    12    │ │  5 days  │ │    67    │ │   89%    │        │
│  │  badges  │ │  active  │ │  total   │ │completion│        │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘        │
│                                                                │
│  ┌────────────────────────────────────┐ ┌──────────────────┐ │
│  │  [All] [Strength] [Volume] [Time] │ │  🔔 Notifications │ │
│  │  [Cardio] [Consistency] [Special] │ │                    │ │
│  │                                    │ │  ● NEW 5RM on...  │ │
│  │  ┌─────────────┐ ┌─────────────┐  │ │  5 min ago        │ │
│  │  │ 🏋️ Iron     │ │ 💪 Century  │  │ │                    │ │
│  │  │   Legend     │ │    Club     │  │ │  ● Biggest Push...│ │
│  │  │ ████████░░  │ │ ██████░░░░  │  │ │  2 hr ago         │ │
│  │  │  89/100     │ │  67/100     │  │ │                    │ │
│  │  │ ⚡ Strength │ │ 🎯 Mile-   │  │ │  ○ Workout done   │ │
│  │  │  LEGENDARY  │ │   stone     │  │ │  yesterday         │ │
│  │  └─────────────┘ └─────────────┘  │ │                    │ │
│  │                                    │ │  ○ Streak: 5 days │ │
│  │  ┌─────────────┐ ┌─────────────┐  │ │  2 days ago       │ │
│  │  │ 🔥 Week     │ │ ❤️ Cardio   │  │ │                    │ │
│  │  │   Warrior   │ │    King     │  │ │  ... (scrollable)  │ │
│  │  │ █████░░░░░  │ │ ██████░░░░  │  │ │                    │ │
│  │  │  5/7        │ │  32/50      │  │ └──────────────────┘ │
│  │  │ 🔥 Streak  │ │ ❤️ Cardio   │  │                       │
│  │  │  RARE       │ │  RARE       │  │                       │
│  │  └─────────────┘ └─────────────┘  │                       │
│  │                                    │                       │
│  │  ... more cards sorted by         │                       │
│  │  proximity to completion          │                       │
│  └────────────────────────────────────┘                       │
└──────────────────────────────────────────────────────────────┘
```

### Achievement Card HTML Structure

```html
<div class="achievement-card ffl-card {% if badge.unlocked %}achievement-unlocked{% endif %}"
     style="border-left: 3px solid {{ rarity_color }};">
  <div class="achievement-header">
    <div class="achievement-icon" style="background: {{ rarity_color }};">
      <i class="bi {{ badge.icon }}"></i>
    </div>
    <span class="achievement-rarity badge-{{ badge.level }}">{{ badge.level | upper }}</span>
  </div>
  <div class="achievement-body">
    <h4 class="achievement-title">{{ badge.name }}</h4>
    <p class="achievement-desc">{{ badge.description }}</p>
    <div class="achievement-progress">
      <div class="progress-info">
        <span>Progress</span>
        <span>{{ progress }}/{{ badge.threshold }}</span>
      </div>
      <div class="progress-bar-wrap">
        <div class="progress-bar-fill" style="width: {{ percent }}%;"></div>
      </div>
    </div>
    <div class="achievement-category">
      <i class="bi {{ category_icon }}"></i>
      <span>{{ badge.category | capitalize }}</span>
    </div>
  </div>
</div>
```

### CRITICAL: Sorting Logic

Achievements MUST be sorted in this order:
1. **Closest to completion (highest % complete, not yet unlocked)** — at the TOP
2. **Recently unlocked** — show with a "completed" state, celebration styling
3. **In progress (lower %)** — middle
4. **Not started (0%)** — bottom, dimmed

```python
# In the route handler:
def sort_achievements(badge_progress_list):
    """
    badge_progress_list = [
        {'badge': BadgeDefinition, 'progress': int, 'total': int, 'unlocked': bool, 'earned_at': datetime},
    ]
    """
    def sort_key(item):
        if item['unlocked']:
            return (0, -item['earned_at'].timestamp())  # Unlocked first, newest first
        elif item['progress'] > 0:
            percent = item['progress'] / item['total'] if item['total'] else 0
            return (1, -percent)  # In-progress sorted by % desc (closest to done = top)
        else:
            return (2, 0)  # Not started at bottom

    return sorted(badge_progress_list, key=sort_key)
```

### Category Tabs

The tab bar filters which badges are shown:

| Tab | Filter |
|-----|--------|
| All | Show everything |
| Strength | `category == 'workout'` + exercises with Strength movement patterns |
| Volume | `category == 'volume'` |
| Time | Time-domain badges (holds + speed) |
| Cardio | `category == 'cardio'` or movement_pattern == 'Cardio' |
| Consistency | `category == 'consistency'` + `category == 'streak'` |
| Special | `category == 'special'` |

Each tab applies the SAME sorting logic (closest to completion at top).

### Stats Row

Four stat cards at the top:

```python
stats = {
    'unlocked': UserBadge.query.filter_by(user_id=user_id).count(),
    'streak': get_best_active_streak(user_id),  # Longest active streak across all types
    'total_workouts': total_completed_workouts(user_id),
    'completion': calculate_badge_completion_percent(user_id),  # Earned / Total available
}
```

---

## 10. NOTIFICATION FEED UI SPEC

### On the Achievement Dashboard (Right Column)

A scrollable notification feed showing ALL notification types, most recent first.

```html
<div class="notif-feed ffl-card">
  <div class="notif-feed-header">
    <div>
      <i class="bi bi-bell-fill" style="color: var(--accent);"></i>
      <span class="card-title">Notifications</span>
      {% if unread_count > 0 %}
      <span class="notif-badge">{{ unread_count }}</span>
      {% endif %}
    </div>
    {% if unread_count > 0 %}
    <button class="btn-mark-all" onclick="markAllRead()">Mark all read</button>
    {% endif %}
  </div>

  <div class="notif-feed-list" style="max-height: 600px; overflow-y: auto;">
    {% for n in notifications %}
    <div class="notif-feed-item {% if not n.is_read %}notif-unread{% endif %}">
      <div class="notif-feed-icon notif-icon-{{ n.notif_type }}">
        {{ get_notif_emoji(n.notif_type) }}
      </div>
      <div class="notif-feed-body">
        <div class="notif-feed-msg">{{ n.message }}</div>
        <div class="notif-feed-time">{{ n.created_at | timeago }}</div>
      </div>
      <button class="notif-feed-dismiss" onclick="dismissNotif({{ n.id }}, this)">
        <i class="bi bi-x"></i>
      </button>
    </div>
    {% endfor %}

    {% if not notifications %}
    <div class="notif-feed-empty">
      <i class="bi bi-bell" style="font-size: 2rem; opacity: 0.3;"></i>
      <p>No notifications yet</p>
    </div>
    {% endif %}
  </div>
</div>
```

### Notification Icon/Emoji Mapping

```python
NOTIF_EMOJI_MAP = {
    'session_completed':          '💪',
    'streak_milestone':           '🔥',
    'pr_rep_max':                 '🏆',
    'pr_volume_exercise':         '📊',
    'pr_volume_workout':          '💥',
    'pr_volume_milestone':        '📈',
    'pr_longest_hold':            '⏱️',
    'pr_fastest_time':            '⚡',
    'pr_max_reps':                '💯',
    'badge_earned':               '🏅',
    'streak_started':             '🔥',
    'streak_broken':              '💔',
    'comeback_nudge':             '👋',
    'workout_completed':          '✅',
    'exercise_first':             '🆕',
    'exercise_count_milestone':   '🎯',
    'exercise_rep_milestone':     '🔢',
    'weekly_target_hit':          '🎉',
    'consistency_badge':          '⭐',
    'daily_log_water':            '💧',
    'daily_log_sleep':            '😴',
    'daily_log_meal':             '🍽️',
    'daily_log_weighin':          '⚖️',
    'early_bird':                 '🌅',
    'night_owl':                  '🌙',
    'week_started':               '📅',
    'month_summary':              '📋',
}
```

### Notification Color Coding (border-left)

```css
/* PR notifications — gold/amber accent */
.notif-icon-pr_rep_max,
.notif-icon-pr_volume_exercise,
.notif-icon-pr_volume_workout,
.notif-icon-pr_longest_hold,
.notif-icon-pr_fastest_time { border-left-color: var(--amber); background: var(--amber-dim); }

/* Badge notifications — purple accent */
.notif-icon-badge_earned { border-left-color: #a855f7; background: rgba(168,85,247,0.12); }

/* Streak notifications — fire/amber */
.notif-icon-streak_milestone,
.notif-icon-streak_started { border-left-color: var(--amber); background: var(--amber-dim); }

/* Session/workout — green accent */
.notif-icon-session_completed,
.notif-icon-workout_completed { border-left-color: var(--green); background: var(--green-dim); }

/* Daily logging — FFL blue accent */
.notif-icon-daily_log_water,
.notif-icon-daily_log_sleep,
.notif-icon-daily_log_meal,
.notif-icon-daily_log_weighin { border-left-color: var(--accent); background: var(--accent-dim); }

/* Comeback/nudge — soft red */
.notif-icon-comeback_nudge,
.notif-icon-streak_broken { border-left-color: var(--red); background: var(--red-dim); }
```

### On the Main Client Dashboard

Keep the existing notification panel (8 most recent) but update it to show the new notification types with their emojis and color coding. Add a "View All" link that goes to `/achievements`.

### API Routes for Notifications

```
POST /notifications/dismiss/<id>              — (exists) Mark as read
GET  /api/notifications/unread-count          — (exists) Get unread count
POST /notifications/mark-all-read             — NEW: Mark all as read
GET  /api/notifications/recent?limit=20       — NEW: Get recent notifications (JSON)
```

---

## 11. FFL DESIGN SYSTEM TOKENS

All new UI MUST use these exact CSS variables. Do NOT introduce new colors.

### Backgrounds
```css
--bg:           #080808
--bg-surface:   #0f0f0f
--bg-elevated:  #161616
--bg-hover:     rgba(255,255,255,0.035)
--bg-active:    rgba(255,255,255,0.06)
```

### Borders
```css
--border:       rgba(255,255,255,0.07)
--border-mid:   rgba(255,255,255,0.10)
--border-focus: rgba(30,157,241,0.5)
```

### Text
```css
--text:         #EDEDED
--text-soft:    rgba(255,255,255,0.55)
--text-muted:   rgba(255,255,255,0.30)
--text-faint:   rgba(255,255,255,0.15)
```

### Accent / Brand
```css
--accent:       #1e9df1
--accent-light: #69c0f5
--accent-dim:   rgba(30,157,241,0.15)
--accent-glow:  rgba(30,157,241,0.25)
```

### Status Colors
```css
--green:        #22c55e
--green-dim:    rgba(34,197,94,0.12)
--amber:        #f59e0b
--amber-dim:    rgba(245,158,11,0.12)
--red:          #ef4444
--red-dim:      rgba(239,68,68,0.12)
```

### Badge Rarity Colors
```css
--rarity-common:    rgba(255,255,255,0.30)        /* gray */
--rarity-rare:      #1e9df1                        /* FFL blue */
--rarity-epic:      #a855f7                        /* purple */
--rarity-legendary: #f59e0b                        /* amber/gold */
```

### Typography
```css
--font-sans: 'Open Sans', -apple-system, sans-serif
--font-mono: Menlo, monospace
```

### Spacing / Radius
```css
--radius-sm: 6px
--radius-md: 10px
--radius-lg: 14px
```

### Component Classes (use these, don't create new patterns)
- `.ffl-card` — Standard card container (bg-elevated, border, border-radius)
- `.card-title` — Card header text
- `.page-header` — Page top section
- `.page-title` — Page h1
- `.page-subtitle` — Page description text
- `.btn btn-outline-secondary` — Primary action buttons
- `.form-control`, `.form-select` — Bootstrap form inputs (styled dark in style.css)
- `.notif-badge` — Blue pill badge for unread counts

---

## 12. INTEGRATION POINTS & HOOKS

### Where to Hook Into Existing Code

#### 1. Workout Log Form Submission (`/workout-log/<assignment_id>/<phase_id>/<week>/<day>` POST handler)
**Currently:** Creates WorkoutCheckin records and ExerciseLog records.
**Add:** After successful save, call `process_workout_log()` with all the exercise data.

#### 2. Session Status Update (`/sessions/<id>/status` POST — line 1097)
**Currently:** Creates session_completed and streak_milestone notifications.
**Keep existing logic.** Add: Also call `update_streak(user_id, 'workout')` and `check_badges(user_id, {'action': 'session_completed'})`.

#### 3. Complete Week (`/sessions/complete-week` POST — line 1150)
**Currently:** Marks sessions as completed and creates notifications.
**Keep existing logic.** Add: Call streak and badge checks for each affected client.

#### 4. Progress Log (`/progress/log` POST — line 3036)
**Currently:** Creates ProgressEntry record.
**Add:** If `weight_lbs` provided, call `process_daily_log(user_id, 'weighin', value=weight_lbs)`.

#### 5. Exercise Log (`/progress/log-exercise` POST — line 3067)
**Currently:** Creates ExerciseLog record.
**Add:** Call `process_workout_log()` for single-exercise PR detection.

#### 6. New Daily Log Routes
**New:** `/daily-log/water`, `/daily-log/sleep`, `/daily-log/meal`, `/daily-log/weighin`
Each calls `process_daily_log()`.

#### 7. Dashboard Route (`/dashboard` GET — line 528)
**Add:** Pass additional context:
```python
# Add to template context:
active_streak = get_best_active_streak(user_id)
recent_badges = get_recent_badges(user_id, limit=3)
badge_count = UserBadge.query.filter_by(user_id=user_id).count()
today_logs = get_today_log_status(user_id)  # Which daily logs done today
```

#### 8. Sidebar Navigation
**Add:** New nav link for "Achievements" → `/achievements` with a badge icon. Show badge count or "NEW" indicator if recent unlocks.

---

## 13. FILE PATHS & LINE REFERENCES

### App Code
- **Main app:** `C:\Users\nickw\Claudes Folder\Fit For Life Program\FitForLife-Portal\app.py` (3,353 lines)
- **All models:** Lines 63–2203 in app.py
- **Notification model:** Lines 197–211
- **PersonalRecord model:** Lines 2135–2151
- **BadgeDefinition model:** Lines 2154–2165
- **UserBadge model:** Lines 2168–2176
- **Streak model:** Lines 2179–2189
- **ExerciseLog model:** Lines 2107–2118
- **Exercise model:** Lines 1969–1980
- **Badge seed data:** Lines 2208–2242
- **get_session_streak():** Lines 403–435
- **Session update route:** Lines 1097–1147
- **Dashboard route:** Lines 528–649
- **Progress routes:** Lines 3022–3110
- **Notification routes:** Lines 1243–1260

### Templates
- **Base template:** `templates/base.html`
- **Client dashboard:** `templates/dashboard_client.html`
- **Workout log:** `templates/workout_log.html`
- **Progress page:** `templates/progress_client.html`
- **NEW (to create):** `templates/achievements.html`
- **NEW (to create):** `templates/daily_log.html`

### Static Files
- **Main CSS:** `static/css/style.css` (1,604 lines)
- **Beam animation:** `static/js/beams-bg.js`
- **Gallery JS:** `static/js/gallery.js`

### Dependencies
- **requirements.txt:** Flask 3.1.0, Flask-Login, Flask-SQLAlchemy, Flask-WTF, Flask-Limiter, psycopg2-binary, gunicorn
- **CDN:** Bootstrap 5.3.3, Bootstrap Icons 1.11.3, Chart.js 4, Google Fonts (Open Sans)

---

## 14. BUILD CONSTRAINTS & RULES

### MUST Follow
1. **No React, no build tools.** Everything is Jinja2 templates + vanilla JavaScript.
2. **No new CSS frameworks.** Use existing CSS variables and Bootstrap classes.
3. **No new Python packages** unless absolutely necessary. Prefer stdlib + existing dependencies.
4. **CSRF protection on ALL POST routes.** Use Flask-WTF's CSRFProtect (already global).
5. **All new routes need `@login_required`.** Client-only routes should check `current_user.role == 'client'`.
6. **All new models must be migrated.** Use `db.create_all()` or write ALTER TABLE statements.
7. **Keep app.py as the single file.** This app is monolithic — don't split into blueprints or separate modules (yet).
8. **Match existing code patterns.** Look at how existing routes, templates, and JS work before writing new ones.
9. **FFL dark theme only.** No light mode. Use the exact CSS variables defined above.
10. **No toast/popup notifications.** Notification feed updates in the panel — no floating toasts.

### MUST NOT Do
1. **Never compare clients' weights or strength on leaderboards.** Streaks and consistency ONLY.
2. **Never show body weight on any shared/public view.** Weight is private to the individual client.
3. **Never create separate Python files/modules.** Keep everything in app.py for now.
4. **Never add Tailwind CSS.** This project uses vanilla CSS with custom properties.
5. **Never use React components.** The React code in the prompt was a REFERENCE DESIGN only — adapt the layout and UX to Jinja2 + Bootstrap + vanilla JS.
6. **Never store secrets in code.** Use environment variables.

### Performance Considerations
1. **ExerciseVolumeTotal exists to avoid expensive recalculations.** Always update it incrementally, never recalculate from all ExerciseLog records.
2. **Badge checks should be lightweight.** Compare counts against thresholds, don't query entire history on every action.
3. **Notification queries should be indexed.** Add index on `(user_id, is_read, created_at)`.
4. **Streak calculations should use the Streak model,** not recalculate from scratch like the current `get_session_streak()` function does.

---

## 15. LEADERBOARD (STREAKS ONLY)

### Route
```
GET /leaderboard    — Streak-only leaderboard, visible to all clients
```

### What to Show
```
┌─────────────────────────────────────────────┐
│  🏆 Community Leaderboard                    │
│  Who's staying the most consistent?          │
├─────────────────────────────────────────────┤
│                                               │
│  🔥 LONGEST ACTIVE STREAKS                   │
│  ┌───┬─────────────────┬──────────┐          │
│  │ 1 │ Sarah W.        │ 23 days  │ 🔥🔥🔥  │
│  │ 2 │ Clare M.        │ 18 days  │ 🔥🔥    │
│  │ 3 │ Travis B.       │ 14 days  │ 🔥🔥    │
│  │ 4 │ You             │ 12 days  │ 🔥      │
│  │ 5 │ Alison H.       │ 10 days  │ 🔥      │
│  └───┴─────────────────┴──────────┘          │
│                                               │
│  📅 WEEKLY CHECK-IN LEADERS                  │
│  ┌───┬─────────────────┬──────────┐          │
│  │ 1 │ Mary Alice M.   │ 5 / 5   │ ⭐       │
│  │ 2 │ Nick W.         │ 4 / 4   │ ⭐       │
│  │ 3 │ Helen           │ 4 / 5   │          │
│  └───┴─────────────────┴──────────┘          │
│                                               │
│  🏅 MOST BADGES EARNED                       │
│  ┌───┬─────────────────┬──────────┐          │
│  │ 1 │ Clare M.        │ 18      │ 🏅       │
│  │ 2 │ You             │ 14      │ 🏅       │
│  │ 3 │ Sarah W.        │ 12      │ 🏅       │
│  └───┴─────────────────┴──────────┘          │
│                                               │
│  ⚠️ NO weight, body stats, or strength       │
│     comparisons. Ever.                        │
└─────────────────────────────────────────────┘
```

### Rules
- Show first name + last initial only (privacy)
- Highlight the current user's row ("You")
- **NEVER show:** weight lifted, body weight, measurements, 1RM, or any strength metric
- **ONLY show:** streak days, check-in consistency, badges earned, logging streaks
- If a client has opted out, don't show them (future feature — for now, show all)

---

## 16. VOLUME TRACKING SYSTEM

### How Volume is Calculated

```
Per-set volume = weight_lbs × reps
Per-exercise session volume = sum of all sets in that session
Per-workout total volume = sum of all exercise session volumes
Cumulative exercise volume = running total across all sessions (stored in ExerciseVolumeTotal)
```

### Example Flow

Client logs a Push Day workout:
```
Bench Press:   3 sets × 8 reps × 185 lbs = 4,440 lbs
Incline DB:    3 sets × 10 reps × 60 lbs = 1,800 lbs
Cable Fly:     3 sets × 12 reps × 30 lbs = 1,080 lbs
Tricep Push:   3 sets × 15 reps × 40 lbs = 1,800 lbs
─────────────────────────────────────────────────────
Total workout volume: 9,120 lbs
```

This generates (potentially):
1. Per-exercise session volume PRs (if any beat previous best)
2. Total workout volume PR (if 9,120 > previous best Push Day)
3. Cumulative volume milestones (if bench press total crosses 25,000 lbs)
4. Rep max PRs (if 185×8 beats previous 8RM on bench)

### Reps Parsing

The `reps_completed` field in ExerciseLog can be:
- `"8"` — Same reps for all sets
- `"8,8,6"` — Different reps per set (comma-separated)

Volume calculation must handle both:
```python
def calculate_set_volume(sets, reps_str, weight):
    if not weight or not reps_str:
        return 0

    reps_list = [int(r.strip()) for r in str(reps_str).split(',') if r.strip().isdigit()]

    if len(reps_list) == 1 and sets:
        # Same reps for all sets
        return weight * reps_list[0] * sets
    else:
        # Per-set reps
        return weight * sum(reps_list)
```

---

## 17. TIME-DOMAIN PR SYSTEM

### Exercise Classification

Exercises need to be flagged so the system knows how to track them:

| Exercise Type | `is_hold` | `is_timed` | What We Track | Unit | Better = |
|--------------|-----------|------------|---------------|------|----------|
| Plank | True | False | `duration_seconds` | seconds | Higher |
| Wall Sit | True | False | `duration_seconds` | seconds | Higher |
| Dead Hang | True | False | `duration_seconds` | seconds | Higher |
| L-Sit | True | False | `duration_seconds` | seconds | Higher |
| Farmer's Carry Hold | True | False | `duration_seconds` | seconds | Higher |
| Mile Run | False | True | `time_seconds` | seconds | Lower |
| 400m Sprint | False | True | `time_seconds` | seconds | Lower |
| 500m Row | False | True | `time_seconds` | seconds | Lower |
| Shuttle Run | False | True | `time_seconds` | seconds | Lower |
| Bike Sprint (1 mile) | False | True | `time_seconds` | seconds | Lower |

### Workout Log UI Changes

When an exercise is `is_hold == True`:
- Hide the Sets/Reps/Weight inputs
- Show a Duration input: `[  ___ min ] [ ___ sec ]`
- "How long did you hold it?"

When an exercise is `is_timed == True`:
- Hide the Reps input (or make optional)
- Show a Time input: `[  ___ min ] [ ___ sec ]`
- Keep distance field if relevant
- "What was your time?"

When an exercise is neither (standard strength):
- Show normal Sets/Reps/Weight inputs (current behavior)

### Duration Formatting Helper

```python
def format_duration(seconds):
    """Format seconds into human-readable time."""
    if seconds < 60:
        return f"{seconds:.0f} seconds"
    minutes = int(seconds // 60)
    remaining = int(seconds % 60)
    if remaining == 0:
        return f"{minutes}:{remaining:02d}"
    return f"{minutes}:{remaining:02d}"
```

---

## 18. COMPLETE NOTIFICATION MESSAGE TEMPLATES

Every notification the system can create, with exact message format:

### Workout & Session
```python
# Session completed (existing — keep)
f"Session completed {date_str} with {trainer_name}. Great work! 💪"

# Workout completed (new)
f"Workout done! {workout_name} — {total_volume:,.0f} lbs total volume. 💪"

# First workout of the week
f"First workout of the week — {remaining} more to hit your goal!"

# Weekly target hit
f"🎉 {count} workouts this week — weekly target crushed!"

# Early bird
f"🌅 Workout before 7 AM — Early Bird energy!"

# Night owl
f"🌙 Late night grind — Night Owl mode activated!"
```

### Personal Records — Rep Max
```python
# New PR with previous record
f"🏆 NEW {pr_type.upper()} on {exercise_name} — {weight} lbs! Beat your old record by {diff} lbs!"

# New PR, first time
f"🏆 NEW {pr_type.upper()} on {exercise_name} — {weight} lbs! First recorded {pr_type}!"
```

### Personal Records — Volume
```python
# Best session volume for an exercise
f"📊 New volume record on {exercise_name} — {volume:,.0f} lbs in one session!"

# Best total workout volume
f"💥 Biggest {workout_name} EVER — {volume:,.0f} lbs total volume!"

# Cumulative volume milestone
f"📈 {format_number(milestone)} lbs moved on {exercise_name}. {'Beast mode.' if milestone >= 50000 else 'Keep grinding!'}"
```

### Personal Records — Time Domain
```python
# Longest hold — new record
f"⏱️ New longest {exercise_name} — {formatted_time}! {diff_msg}"
# Where diff_msg = f"That's {diff} seconds longer than your best!" or ""

# Fastest time — new record
f"⚡ Fastest {exercise_name} — {formatted_time}! {diff_msg}"
# Where diff_msg = f"You shaved {diff} seconds off!" or ""

# Max unbroken reps
f"💯 New max {exercise_name} — {reps} unbroken reps!"
```

### Exercise Milestones
```python
# First time doing an exercise
f"🆕 First time doing {exercise_name}. New movement unlocked!"

# Nth time doing an exercise
f"🎯 {count}{'th' if count not in [1,2,3] else ['st','nd','rd'][count-1]} time doing {exercise_name}. {'That\\'s commitment.' if count >= 50 else 'Building the habit!'}"
# Trigger at: 10, 25, 50, 100, 150, 200

# Total rep milestone
f"🔢 {format_number(milestone)} total reps of {exercise_name}. {'Half a thousand!' if milestone == 500 else 'Incredible.' if milestone >= 5000 else 'Keep stacking!'}"
# Trigger at: 100, 500, 1000, 5000, 10000
```

### Streaks
```python
# Streak milestone
f"🔥 {count}-day {streak_type} streak! {'You\\'re just getting started!' if count <= 7 else 'You\\'re on fire!' if count <= 30 else 'Absolutely unstoppable!' if count <= 90 else 'LEGENDARY.'}"

# Streak broken
f"💔 Your {count}-day {streak_type} streak ended. But you can start a new one today!"

# Comeback nudge (2 days)
f"👋 2 days off your streak — session tomorrow?"

# Comeback nudge (5 days)
f"👋 We miss you! Your {streak_type} streak is waiting."

# Comeback nudge (7+ days)
f"👋 Welcome back anytime. Let's restart together."
```

### Badges
```python
# Badge earned
f"🏅 Badge unlocked: {badge_name} — {badge_description}"
```

### Daily Logging
```python
# Water
f"💧 {value:.0f} oz logged. Hydration on point."

# Sleep
f"😴 {value:.1f} hours logged. Rest is where gains happen."

# Meal
f"🍽️ Meal logged. Fueling the machine."

# Weigh-in
f"⚖️ Weigh-in recorded. Consistency builds the picture."
```

### Monthly Summary
```python
# End of month
f"📋 {month_name} Recap: {workouts} workouts, {prs} PRs, {format_number(volume)} lbs moved, {badges} badges earned. {'Beast month!' if workouts >= 16 else 'Solid month!'}"
```

---

## SUMMARY — WHAT TO BUILD

### New Database Tables (migrate)
1. `daily_logs`
2. `exercise_volume_totals`
3. `workout_volume_records`
4. Activate dormant: `personal_records`, `badge_definitions`, `user_badges`, `streaks`, `rep_milestones`

### Modified Tables
1. `exercise_logs` — add `duration_seconds`, `time_seconds`, `distance`, `distance_unit`
2. `exercises` — add `is_timed`, `is_hold`
3. `notifications` — expand `notif_type` values

### New Routes
1. `GET /achievements` — Achievement dashboard
2. `GET /leaderboard` — Streak-only leaderboard
3. `GET /daily-log` — Daily logging page
4. `POST /daily-log/water` — Log water
5. `POST /daily-log/sleep` — Log sleep
6. `POST /daily-log/meal` — Log meal
7. `POST /daily-log/weighin` — Log weigh-in
8. `POST /notifications/mark-all-read` — Mark all read
9. `GET /api/notifications/recent` — Recent notifications JSON
10. `GET /api/daily-log/today` — Today's log status

### New Templates
1. `achievements.html` — Badge/achievement dashboard with notification feed
2. `daily_log.html` — Quick daily logging page

### Modified Templates
1. `dashboard_client.html` — Add daily log widget, badge preview, updated notification panel
2. `workout_log.html` — Add time-domain inputs for hold/timed exercises
3. `base.html` — Add "Achievements" nav link in sidebar

### New Backend Functions
1. `create_notification()` — Central notification creation
2. `process_workout_log()` — Main workout analysis engine
3. `process_daily_log()` — Daily log handler
4. `check_badges()` — Badge threshold checker
5. `update_streak()` — Streak update logic
6. `check_comeback_nudge()` — Inactivity checker
7. `calculate_set_volume()` — Volume math
8. `sort_achievements()` — Badge sorting for dashboard
9. `format_duration()` — Time formatting
10. `get_notif_emoji()` — Emoji mapper

### New CSS (add to style.css)
1. Achievement card styles
2. Achievement dashboard layout (2-column grid)
3. Badge rarity colors
4. Progress bar styles
5. Notification feed styles (updated)
6. Daily log quick-tap styles
7. Leaderboard table styles
8. Time-domain input styles

### Seed Data Updates
1. Expand BADGE_SEED from 34 → 70+ badges
2. Flag existing exercises with `is_timed` and `is_hold` where applicable
3. Initial ExerciseVolumeTotal records (backfill from existing ExerciseLog data)

---

**END OF SPEC — This document is the single source of truth for the build.**
