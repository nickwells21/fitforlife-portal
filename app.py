import os
import warnings
from datetime import datetime, timedelta, timezone
from calendar import month_name
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

_secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
if _secret_key == 'dev-secret-change-in-production':
    warnings.warn('SECRET_KEY is not set — using insecure dev key. Set SECRET_KEY in environment!', stacklevel=1)
app.config['SECRET_KEY'] = _secret_key

db_url = os.environ.get('DATABASE_URL', 'sqlite:///fitforlife.db')
if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 280,
}
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', 'False') == 'True'
app.config['SESSION_COOKIE_SAMESITE'] = os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax')
app.config['SESSION_COOKIE_HTTPONLY'] = True

csrf = CSRFProtect(app)
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'


# ─── Models ──────────────────────────────────────────────────────────────────

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # admin, trainer, client
    trainerize_url = db.Column(db.String(300))
    trainer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    sessions_as_trainer = db.relationship('Session', foreign_keys='Session.trainer_id', backref='trainer', lazy='dynamic')
    sessions_as_client = db.relationship('Session', foreign_keys='Session.client_id', backref='client', lazy='dynamic')
    packages = db.relationship('SessionPackage', backref='client_user', lazy='dynamic')

    @property
    def assigned_trainer(self):
        if self.trainer_id:
            return db.session.get(User, self.trainer_id)
        return None

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Location(db.Model):
    __tablename__ = 'locations'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    color = db.Column(db.String(20), default='#2563eb')
    sessions = db.relationship('Session', backref='location', lazy='dynamic')


class TrainingGroup(db.Model):
    __tablename__ = 'training_groups'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    trainer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    memberships = db.relationship('GroupMembership', backref='group', lazy='dynamic',
                                  cascade='all, delete-orphan')
    trainer = db.relationship('User', foreign_keys='TrainingGroup.trainer_id')

    @property
    def client_list(self):
        return [db.session.get(User, m.client_id) for m in self.memberships.all()]


class GroupMembership(db.Model):
    __tablename__ = 'group_memberships'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('training_groups.id'), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    __table_args__ = (db.UniqueConstraint('group_id', 'client_id', name='uq_group_client'),)

    client = db.relationship('User', foreign_keys='GroupMembership.client_id')


class Session(db.Model):
    __tablename__ = 'sessions'
    id = db.Column(db.Integer, primary_key=True)
    trainer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=False)
    scheduled_at = db.Column(db.DateTime, nullable=False)
    duration = db.Column(db.Integer, default=60)  # minutes
    status = db.Column(db.String(20), default='scheduled')  # scheduled, completed, missed, cancelled
    notes = db.Column(db.Text)
    group_id = db.Column(db.Integer, db.ForeignKey('training_groups.id'), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    training_group = db.relationship('TrainingGroup', foreign_keys='Session.group_id')

    @property
    def end_time(self):
        return self.scheduled_at + timedelta(minutes=self.duration)


class MembershipPlan(db.Model):
    __tablename__ = 'membership_plans'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    plan_type = db.Column(db.String(20), nullable=False)   # '1on1','group-2','group-3','group-4'
    sessions_per_month = db.Column(db.Integer, nullable=False)   # 4, 8, 12
    commitment = db.Column(db.String(20), nullable=False)  # 'month-to-month','6-month'
    is_crew = db.Column(db.Boolean, default=False)
    price_cents = db.Column(db.Integer)                    # monthly price in cents


class SessionPackage(db.Model):
    __tablename__ = 'session_packages'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    month = db.Column(db.Integer, nullable=False)
    year = db.Column(db.Integer, nullable=False)
    sessions_purchased = db.Column(db.Integer, nullable=False)
    plan_id = db.Column(db.Integer, db.ForeignKey('membership_plans.id'), nullable=True)
    is_crew = db.Column(db.Boolean, default=False)
    notes = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    plan = db.relationship('MembershipPlan', foreign_keys=[plan_id])


class Promo(db.Model):
    __tablename__ = 'promos'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    promo_type = db.Column(db.String(50), default='general')
    is_active = db.Column(db.Boolean, default=True)
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Event(db.Model):
    __tablename__ = 'events'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    event_date = db.Column(db.DateTime, nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    location = db.relationship('Location')


class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    message = db.Column(db.String(400), nullable=False)
    notif_type = db.Column(db.String(50), default='session_completed')
    session_id = db.Column(db.Integer, db.ForeignKey('sessions.id'), nullable=True)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', foreign_keys=[user_id])


class Message(db.Model):
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    recipient_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    body = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    sender = db.relationship('User', foreign_keys=[sender_id], backref=db.backref('sent_messages', lazy='dynamic'))
    recipient = db.relationship('User', foreign_keys=[recipient_id], backref=db.backref('received_messages', lazy='dynamic'))


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ─── Decorators ──────────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.role != 'admin':
            abort(403)
        return f(*args, **kwargs)
    return decorated


def staff_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.role not in ('admin', 'trainer'):
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ─── Template Context Processors ─────────────────────────────────────────────

@app.context_processor
def inject_admin_id():
    """Make admin_id available in all templates."""
    admin = User.query.filter_by(role='admin', is_active=True).first()
    return {'admin_id': admin.id if admin else 1}


# ─── Helpers ─────────────────────────────────────────────────────────────────

TRAINER_COLORS = ['#2563eb', '#16a34a', '#d97706', '#7c3aed', '#db2777', '#0891b2']

STATUS_COLORS = {
    'completed': '#16a34a',
    'missed': '#dc2626',
    'cancelled': '#9ca3af',
}

# 2026 FFL Program Year — update dates to match your actual schedule
PROGRAM_QUARTERS = [
    {'name': 'Q1', 'label': 'Jan – Mar', 'start': datetime(2026, 1, 6), 'end': datetime(2026, 3, 29)},
    {'name': 'Q2', 'label': 'Apr – Jun', 'start': datetime(2026, 4, 6), 'end': datetime(2026, 6, 28)},
    {'name': 'Q3', 'label': 'Jul – Sep', 'start': datetime(2026, 7, 6), 'end': datetime(2026, 9, 27)},
    {'name': 'Q4', 'label': 'Oct – Dec', 'start': datetime(2026, 10, 5), 'end': datetime(2026, 12, 20)},
]


def get_program_quarter_progress(now_dt=None):
    """Returns the current quarter and percentage through it."""
    if now_dt is None:
        now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
    for i, q in enumerate(PROGRAM_QUARTERS):
        if q['start'] <= now_dt <= q['end']:
            total_secs = (q['end'] - q['start']).total_seconds()
            elapsed_secs = (now_dt - q['start']).total_seconds()
            pct = min(100, max(0, int(elapsed_secs / total_secs * 100)))
            return {
                'index': i,
                'name': q['name'],
                'label': q['label'],
                'pct': pct,
                'start': q['start'],
                'end': q['end'],
                'quarters': PROGRAM_QUARTERS,
                'active': True,
            }
    # Between quarters or before/after program year
    # Find the next upcoming quarter
    next_q = next((q for q in PROGRAM_QUARTERS if q['start'] > now_dt), None)
    return {
        'index': None,
        'name': None,
        'label': 'Program Break' if not next_q else f'Next: {next_q["name"]}',
        'pct': 0,
        'start': None,
        'end': next_q['start'] if next_q else None,
        'quarters': PROGRAM_QUARTERS,
        'active': False,
    }


def check_conflict(trainer_id, location_id, start_dt, end_dt, exclude_session_id=None):
    """Returns list of conflicting sessions. Two sessions conflict if they
    share the same trainer OR same location AND their time windows overlap."""
    query = Session.query.filter(Session.status != 'cancelled')
    if exclude_session_id:
        query = query.filter(Session.id != exclude_session_id)

    conflicts = []
    for sess in query.all():
        sess_end = sess.scheduled_at + timedelta(minutes=sess.duration)
        overlaps = sess.scheduled_at < end_dt and sess_end > start_dt
        same_trainer = sess.trainer_id == trainer_id
        same_location = sess.location_id == location_id
        if overlaps and (same_trainer or same_location):
            conflicts.append(sess)
    return conflicts


def get_month_booked_count(client_id, month, year):
    """Count all non-cancelled sessions for a client in a given month (including scheduled/pending)."""
    month_start = datetime(year, month, 1)
    month_end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return Session.query.filter(
        Session.client_id == client_id,
        Session.status != 'cancelled',
        Session.scheduled_at >= month_start,
        Session.scheduled_at < month_end,
    ).count()


def get_month_balance(client_id, month, year):
    """Returns (purchased, completed, remaining) for a client in a given month.
    If no package exists for this exact month, falls back to the client's most
    recent package as the recurring subscription rate."""
    package = SessionPackage.query.filter_by(
        client_id=client_id, month=month, year=year
    ).first()
    if package:
        purchased = package.sessions_purchased
    else:
        latest = SessionPackage.query.filter_by(client_id=client_id).order_by(
            SessionPackage.year.desc(), SessionPackage.month.desc()
        ).first()
        purchased = latest.sessions_purchased if latest else 0

    month_start = datetime(year, month, 1)
    if month == 12:
        month_end = datetime(year + 1, 1, 1)
    else:
        month_end = datetime(year, month + 1, 1)

    completed = Session.query.filter(
        Session.client_id == client_id,
        Session.status == 'completed',
        Session.scheduled_at >= month_start,
        Session.scheduled_at < month_end,
    ).count()

    return purchased, completed, max(0, purchased - completed)


def trainer_color(trainer_id):
    return TRAINER_COLORS[trainer_id % len(TRAINER_COLORS)]


def get_session_streak(client_id):
    """Consecutive weeks (going back) with at least 1 completed session."""
    now = datetime.now(timezone.utc)
    week_start = now.date() - timedelta(days=now.weekday())
    streak = 0
    for _ in range(52):
        week_end = week_start + timedelta(days=7)
        count = Session.query.filter(
            Session.client_id == client_id,
            Session.status == 'completed',
            Session.scheduled_at >= datetime(week_start.year, week_start.month, week_start.day),
            Session.scheduled_at < datetime(week_end.year, week_end.month, week_end.day),
        ).count()
        if count > 0:
            streak += 1
            week_start -= timedelta(days=7)
        else:
            break
    return streak


def get_week_sessions(client_id):
    """Returns a 7-element list (Mon–Sun) of sessions for the current week."""
    now = datetime.now(timezone.utc)
    week_start = now.date() - timedelta(days=now.weekday())
    result = []
    for i in range(7):
        day = week_start + timedelta(days=i)
        day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        sessions = Session.query.filter(
            Session.client_id == client_id,
            Session.status != 'cancelled',
            Session.scheduled_at >= day_start,
            Session.scheduled_at < day_end,
        ).all()
        result.append({'date': day, 'sessions': sessions, 'is_today': day == now.date()})
    return result


def get_month_session_dots(client_id, month, year):
    """Returns list of session statuses for every session this month — for dot display."""
    month_start = datetime(year, month, 1, tzinfo=timezone.utc)
    month_end = datetime(year + 1, 1, 1, tzinfo=timezone.utc) if month == 12 else datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return Session.query.filter(
        Session.client_id == client_id,
        Session.status != 'cancelled',
        Session.scheduled_at >= month_start,
        Session.scheduled_at < month_end,
    ).order_by(Session.scheduled_at).all()


# ─── Auth ────────────────────────────────────────────────────────────────────


@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = User.query.filter_by(email=email).first()
        if user and user.is_active and user.check_password(password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid email or password.', 'danger')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# ─── Dashboard ───────────────────────────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    now = datetime.now(timezone.utc)
    today = now.date()
    month, year = now.month, now.year

    if current_user.role == 'client':
        upcoming = Session.query.filter(
            Session.client_id == current_user.id,
            Session.status == 'scheduled',
            Session.scheduled_at >= now
        ).order_by(Session.scheduled_at).limit(8).all()

        next_session = upcoming[0] if upcoming else None

        # Days until next session (naive datetime safe)
        if next_session:
            next_dt = next_session.scheduled_at
            if next_dt.tzinfo is None:
                next_dt = next_dt.replace(tzinfo=timezone.utc)
            days_until_next = (next_dt.date() - now.date()).days
        else:
            days_until_next = None

        purchased, completed, remaining = get_month_balance(current_user.id, month, year)
        sessions_this_month = completed  # alias for template stat card
        total_sessions = Session.query.filter(
            Session.client_id == current_user.id,
            Session.status == 'completed'
        ).count()
        month_dots = get_month_session_dots(current_user.id, month, year)
        week_days = get_week_sessions(current_user.id)
        streak = get_session_streak(current_user.id)

        # Time-of-day greeting
        hour = now.hour
        if hour < 12:
            greeting = 'Good morning'
        elif hour < 17:
            greeting = 'Good afternoon'
        else:
            greeting = 'Good evening'

        promos = Promo.query.filter(
            Promo.is_active == True,
            db.or_(Promo.end_date == None, Promo.end_date >= today)
        ).all()

        events = Event.query.filter(
            Event.is_active == True,
            Event.event_date >= now
        ).order_by(Event.event_date).limit(6).all()

        # Notifications (most recent 8, unread first)
        notifications = Notification.query.filter_by(
            user_id=current_user.id
        ).order_by(Notification.is_read.asc(), Notification.created_at.desc()).limit(8).all()
        unread_notif_count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()

        # Program year quarter progress
        now_naive = now.replace(tzinfo=None)
        quarter_progress = get_program_quarter_progress(now_naive)

        return render_template('dashboard_client.html',
            upcoming=upcoming,
            next_session=next_session,
            days_until_next=days_until_next,
            purchased=purchased,
            completed=completed,
            remaining=remaining,
            sessions_this_month=sessions_this_month,
            total_sessions=total_sessions,
            month_dots=month_dots,
            week_days=week_days,
            streak=streak,
            greeting=greeting,
            promos=promos,
            events=events,
            month_name=month_name[month],
            now=now,
            notifications=notifications,
            unread_notif_count=unread_notif_count,
            quarter_progress=quarter_progress,
        )

    elif current_user.role == 'trainer':
        two_weeks_ahead = now + timedelta(weeks=2)
        raw_upcoming = Session.query.filter(
            Session.trainer_id == current_user.id,
            Session.status == 'scheduled',
            Session.scheduled_at >= now,
            Session.scheduled_at < two_weeks_ahead,
        ).order_by(Session.scheduled_at).all()

        # Collapse group sessions: one slot per (time, group) instead of one per client
        seen_slots = set()
        upcoming = []
        for s in raw_upcoming:
            if s.group_id:
                key = (s.scheduled_at, s.group_id)
                if key in seen_slots:
                    continue
                seen_slots.add(key)
            upcoming.append(s)
            if len(upcoming) >= 6:
                break

        raw_today = Session.query.filter(
            Session.trainer_id == current_user.id,
            Session.status != 'cancelled',
            db.func.date(Session.scheduled_at) == today
        ).order_by(Session.scheduled_at).all()

        # Same dedup for today's sessions
        seen_today = set()
        today_sessions = []
        for s in raw_today:
            if s.group_id:
                key = (s.scheduled_at, s.group_id)
                if key in seen_today:
                    continue
                seen_today.add(key)
            today_sessions.append(s)

        # Only show clients assigned to this trainer
        all_clients = User.query.filter_by(role='client', is_active=True, trainer_id=current_user.id).order_by(User.name).all()

        my_groups = TrainingGroup.query.filter_by(trainer_id=current_user.id).order_by(TrainingGroup.name).all()

        return render_template('dashboard_trainer.html',
            upcoming=upcoming,
            today_sessions=today_sessions,
            all_clients=all_clients,
            my_groups=my_groups,
            month_name=month_name[month]
        )

    else:  # admin
        total_clients = User.query.filter_by(role='client', is_active=True).count()
        total_trainers = User.query.filter_by(role='trainer', is_active=True).count()

        month_start = datetime(year, month, 1)
        month_end = datetime(year, month + 1, 1) if month < 12 else datetime(year + 1, 1, 1)

        sessions_this_month = Session.query.filter(
            Session.scheduled_at >= month_start,
            Session.scheduled_at < month_end,
        ).count()

        completed_this_month = Session.query.filter(
            Session.scheduled_at >= month_start,
            Session.scheduled_at < month_end,
            Session.status == 'completed'
        ).count()

        clients = User.query.filter_by(role='client', is_active=True).all()
        at_risk = []
        for c in clients:
            p, comp, rem = get_month_balance(c.id, month, year)
            if p > 0 and rem > 0:
                at_risk.append({'client': c, 'remaining': rem, 'purchased': p, 'completed': comp})
        at_risk.sort(key=lambda x: x['remaining'], reverse=True)

        _today_raw = Session.query.filter(
            db.func.date(Session.scheduled_at) == today,
            Session.status != 'cancelled'
        ).order_by(Session.scheduled_at).all()
        # Deduplicate group sessions — show once per group slot
        _seen = {}
        today_sessions = []
        for s in _today_raw:
            if s.group_id:
                key = (s.group_id, s.scheduled_at)
                if key not in _seen:
                    _seen[key] = True
                    today_sessions.append(s)
            else:
                today_sessions.append(s)

        return render_template('dashboard_admin.html',
            total_clients=total_clients,
            total_trainers=total_trainers,
            sessions_this_month=sessions_this_month,
            completed_this_month=completed_this_month,
            at_risk=at_risk,
            today_sessions=today_sessions,
            month_name=month_name[month],
            year=year
        )


# ─── Calendar API ─────────────────────────────────────────────────────────────

@app.route('/calendar')
@login_required
def calendar_view():
    if current_user.role == 'client':
        return redirect(url_for('client_calendar'))
    trainers = User.query.filter(User.role.in_(['admin', 'trainer']), User.is_active == True).order_by(User.name).all()
    locations = Location.query.all()
    return render_template('calendar.html', trainers=trainers, locations=locations)


@app.route('/my-calendar')
@login_required
def client_calendar():
    if current_user.role != 'client':
        return redirect(url_for('calendar_view'))
    now = datetime.now(timezone.utc)
    month, year = now.month, now.year
    purchased, completed, remaining = get_month_balance(current_user.id, month, year)
    return render_template('calendar_client.html',
        purchased=purchased, completed=completed, remaining=remaining,
        month_name=month_name[month], year=year
    )


@app.route('/api/sessions')
@login_required
def api_sessions():
    start = request.args.get('start')
    end = request.args.get('end')

    query = Session.query.filter(Session.status != 'cancelled')

    if start:
        query = query.filter(Session.scheduled_at >= start[:19])
    if end:
        query = query.filter(Session.scheduled_at <= end[:19])

    if current_user.role == 'client':
        # Only return this client's own sessions + any group sessions they belong to
        client_group_ids = [gm.group_id for gm in GroupMembership.query.filter_by(client_id=current_user.id).all()]
        if client_group_ids:
            query = query.filter(
                db.or_(Session.client_id == current_user.id, Session.group_id.in_(client_group_ids))
            )
        else:
            query = query.filter(Session.client_id == current_user.id)
    elif current_user.role == 'trainer':
        query = query.filter(Session.trainer_id == current_user.id)

    sessions = query.all()
    events = []
    seen_group_slots = {}  # (group_id, scheduled_at) -> index in events list
    for s in sessions:
        is_mine = current_user.role != 'client' or s.client_id == current_user.id
        color = STATUS_COLORS.get(s.status) or (s.location.color if s.location else '#2563eb')

        if s.group_id:
            key = (s.group_id, s.scheduled_at)
            if key in seen_group_slots:
                # Already emitted this group slot — just update is_mine if this client matches
                if is_mine:
                    events[seen_group_slots[key]]['extendedProps']['is_mine'] = True
                continue
            seen_group_slots[key] = len(events)
            group_name = s.training_group.name if s.training_group else 'Group'
            trainer_name = s.trainer.name if s.trainer else 'Trainer'
            events.append({
                'id': s.id,
                'title': f'{trainer_name} + {group_name}',
                'start': s.scheduled_at.isoformat(),
                'end': s.end_time.isoformat(),
                'color': color,
                'extendedProps': {
                    'location': s.location.name if s.location else '',
                    'location_id': s.location_id,
                    'trainer': trainer_name,
                    'trainer_id': s.trainer_id,
                    'client': group_name,
                    'status': s.status,
                    'notes': s.notes or '',
                    'session_id': s.id,
                    'group_id': s.group_id,
                    'group_name': group_name,
                    'is_mine': is_mine,
                }
            })
        else:
            _trainer = s.trainer.name if s.trainer else 'Trainer'
            _client = s.client.name if s.client else 'Client'
            events.append({
                'id': s.id,
                'title': f'{_trainer} + {_client}',
                'start': s.scheduled_at.isoformat(),
                'end': s.end_time.isoformat(),
                'color': color,
                'extendedProps': {
                    'location': s.location.name if s.location else '',
                    'location_id': s.location_id,
                    'trainer': _trainer,
                    'trainer_id': s.trainer_id,
                    'client': _client,
                    'status': s.status,
                    'notes': s.notes or '',
                    'session_id': s.id,
                    'group_id': s.group_id,
                    'group_name': None,
                    'is_mine': is_mine,
                }
            })
    return jsonify(events)


@app.route('/api/check-conflict', methods=['POST'])
@login_required
@csrf.exempt
def api_check_conflict():
    data = request.json
    trainer_id = int(data['trainer_id'])
    location_id = int(data['location_id'])
    start_dt = datetime.fromisoformat(data['start'])
    duration = int(data.get('duration', 60))
    end_dt = start_dt + timedelta(minutes=duration)
    exclude_id = data.get('session_id')

    conflicts = check_conflict(trainer_id, location_id, start_dt, end_dt, exclude_id)
    result = []
    for c in conflicts:
        reason = []
        if c.trainer_id == trainer_id:
            reason.append('trainer')
        if c.location_id == location_id:
            reason.append('location')
        result.append({
            'id': c.id,
            'trainer': c.trainer.name if c.trainer else '—',
            'client': c.client.name if c.client else (c.training_group.name if c.training_group else '—'),
            'location': c.location.name if c.location else '—',
            'start': c.scheduled_at.strftime('%I:%M %p'),
            'end': c.end_time.strftime('%I:%M %p'),
            'conflict_type': ' & '.join(reason),
        })
    return jsonify({'conflicts': result})


# ─── Sessions ────────────────────────────────────────────────────────────────

@app.route('/sessions/new', methods=['GET', 'POST'])
@staff_required
def new_session():
    trainers = User.query.filter(User.role.in_(['admin', 'trainer']), User.is_active == True).order_by(User.name).all()
    if current_user.role == 'trainer':
        clients = User.query.filter_by(role='client', is_active=True, trainer_id=current_user.id).order_by(User.name).all()
        booking_groups = TrainingGroup.query.filter_by(trainer_id=current_user.id).order_by(TrainingGroup.name).all()
    else:
        clients = User.query.filter_by(role='client', is_active=True).order_by(User.name).all()
        booking_groups = TrainingGroup.query.order_by(TrainingGroup.name).all()
    locations = Location.query.all()

    if request.method == 'POST':
        trainer_id = int(request.form['trainer_id'])
        location_id = int(request.form['location_id'])
        scheduled_at = datetime.fromisoformat(request.form['scheduled_at'])
        duration = int(request.form.get('duration', 60))
        notes = request.form.get('notes', '').strip()

        # ── Determine clients ──────────────────────────────────────────
        booking_type = request.form.get('booking_type', 'individual')
        sess_group_id = None
        if booking_type == 'group' and request.form.get('group_id'):
            grp = TrainingGroup.query.get_or_404(int(request.form['group_id']))
            client_ids = [m.client_id for m in grp.memberships.all()]
            sess_group_id = grp.id
            if not client_ids:
                flash('That group has no members.', 'danger')
                client_ids = []
        else:
            cid = request.form.get('client_id')
            client_ids = [int(cid)] if cid else []

        # ── Determine session times ────────────────────────────────────
        repeat_on = request.form.get('repeat') == '1'
        if repeat_on:
            repeat_days = [int(d) for d in request.form.get('repeat_days', '').split(',') if d.strip()]
            repeat_frequency = int(request.form.get('repeat_frequency', 1))
            repeat_weeks = min(int(request.form.get('repeat_weeks', 4)), 52)
            if not repeat_days:
                flash('Select at least one day to repeat on.', 'danger')
                session_times = []
            else:
                session_times = []
                session_time = scheduled_at.time()
                for day_num in repeat_days:
                    days_ahead = (day_num - scheduled_at.weekday()) % 7
                    first_date = scheduled_at.date() + timedelta(days=days_ahead)
                    for week in range(repeat_weeks):
                        session_times.append(datetime.combine(
                            first_date + timedelta(weeks=week * repeat_frequency), session_time))
                session_times.sort()
        else:
            session_times = [scheduled_at]

        # ── Create sessions ────────────────────────────────────────────
        if client_ids and session_times:
            created, skipped = 0, []
            for dt in session_times:
                end_dt = dt + timedelta(minutes=duration)
                if check_conflict(trainer_id, location_id, dt, end_dt):
                    skipped.append(dt.strftime('%a %b %-d'))
                else:
                    for cid in client_ids:
                        db.session.add(Session(
                            trainer_id=trainer_id,
                            client_id=cid,
                            location_id=location_id,
                            scheduled_at=dt,
                            duration=duration,
                            notes=notes,
                            group_id=sess_group_id,
                            created_by_id=current_user.id
                        ))
                        created += 1
            db.session.commit()

            if created == 0:
                flash('No sessions created — all time slots had conflicts.', 'danger')
            else:
                if created == 1 and len(session_times) == 1:
                    flash('Session booked!', 'success')
                else:
                    msg = f'{created} session{"s" if created != 1 else ""} booked!'
                    if skipped:
                        msg += f' {len(skipped)} slot{"s" if len(skipped) > 1 else ""} skipped due to conflicts: {", ".join(skipped[:4])}{"…" if len(skipped) > 4 else ""}.'
                    flash(msg, 'success')
                return redirect(url_for('calendar_view'))

    prefill_date = request.args.get('date', '')
    prefill_trainer = request.args.get('trainer_id', str(current_user.id) if current_user.role == 'trainer' else '')
    prefill_client = request.args.get('client_id', '')

    return render_template('session_form.html',
        sess=None,
        trainers=trainers,
        clients=clients,
        locations=locations,
        booking_groups=booking_groups,
        prefill_date=prefill_date,
        prefill_trainer=prefill_trainer,
        prefill_client=prefill_client
    )


@app.route('/sessions/<int:session_id>')
@login_required
def session_detail(session_id):
    sess = Session.query.get_or_404(session_id)
    if current_user.role == 'client' and sess.client_id != current_user.id:
        abort(403)
    return render_template('session_detail.html', sess=sess)


@app.route('/sessions/<int:session_id>/edit', methods=['GET', 'POST'])
@staff_required
def edit_session(session_id):
    sess = Session.query.get_or_404(session_id)
    trainers = User.query.filter(User.role.in_(['admin', 'trainer']), User.is_active == True).order_by(User.name).all()
    clients = User.query.filter_by(role='client', is_active=True).order_by(User.name).all()
    locations = Location.query.all()

    if request.method == 'POST':
        trainer_id = int(request.form['trainer_id'])
        client_id = int(request.form['client_id'])
        location_id = int(request.form['location_id'])
        scheduled_at = datetime.fromisoformat(request.form['scheduled_at'])
        duration = int(request.form.get('duration', 60))
        notes = request.form.get('notes', '').strip()

        end_dt = scheduled_at + timedelta(minutes=duration)
        conflicts = check_conflict(trainer_id, location_id, scheduled_at, end_dt, exclude_session_id=sess.id)

        edit_all = request.form.get('edit_all') == '1'

        if conflicts and not edit_all:
            msgs = [f'{c.trainer.name} + {c.client.name} at {c.location.name}' for c in conflicts]
            flash('Conflict: ' + ', '.join(msgs), 'danger')
        else:
            # Always update this session
            sess.trainer_id = trainer_id
            sess.client_id = client_id
            sess.location_id = location_id
            sess.scheduled_at = scheduled_at
            sess.duration = duration
            sess.notes = notes

            if edit_all:
                # Apply trainer/location/duration/notes to all future sessions in the same series.
                # Keep each session's date but update the time component.
                orig_dow = sess.scheduled_at.weekday()
                orig_time = sess.scheduled_at.time()
                new_time = scheduled_at.time()
                if sess.group_id:
                    siblings = Session.query.filter(
                        Session.trainer_id == sess.trainer_id,
                        Session.group_id == sess.group_id,
                        Session.scheduled_at > sess.scheduled_at,
                    ).all()
                else:
                    siblings = Session.query.filter(
                        Session.client_id == sess.client_id,
                        Session.scheduled_at > sess.scheduled_at,
                    ).all()
                updated = 0
                for s in siblings:
                    if s.scheduled_at.weekday() == orig_dow and s.scheduled_at.time() == orig_time:
                        s.trainer_id = trainer_id
                        s.location_id = location_id
                        s.duration = duration
                        s.notes = notes
                        s.scheduled_at = datetime.combine(s.scheduled_at.date(), new_time)
                        updated += 1
                db.session.commit()
                flash(f'Session updated · {updated} future repeat{"s" if updated != 1 else ""} also updated.', 'success')
            else:
                db.session.commit()
                flash('Session updated.', 'success')
            return redirect(url_for('session_detail', session_id=sess.id))

    return render_template('session_form.html',
        sess=sess,
        trainers=trainers,
        clients=clients,
        locations=locations,
        booking_groups=[],
        prefill_date='',
        prefill_trainer=''
    )


@app.route('/sessions/<int:session_id>/status', methods=['POST'])
@staff_required
def update_session_status(session_id):
    sess = Session.query.get_or_404(session_id)
    new_status = request.form.get('status')
    if new_status in ('completed', 'missed', 'cancelled', 'scheduled'):
        old_status = sess.status
        sess.status = new_status

        # Auto-create notification when a session is marked completed
        if new_status == 'completed' and old_status != 'completed':
            date_str = sess.scheduled_at.strftime('%b %d').replace(' 0', ' ')
            notif = Notification(
                user_id=sess.client_id,
                message=f'Session completed {date_str} with {sess.trainer.name}. Great work! 💪',
                notif_type='session_completed',
                session_id=sess.id
            )
            db.session.add(notif)

            # Check for streak milestones after this completion
            streak = get_session_streak(sess.client_id)
            milestone_msgs = {
                1:  '🔥 Your streak starts now — keep it going!',
                3:  '🔥🔥🔥 3-week streak — you\'re building momentum!',
                5:  '🔥 5-week streak — incredible consistency!',
                10: '⚡ 10-week streak — you are unstoppable!',
                15: '🏅 15-week streak — elite level dedication!',
                20: '🏆 20-week streak — absolute legend!',
            }
            if streak in milestone_msgs:
                db.session.add(Notification(
                    user_id=sess.client_id,
                    message=milestone_msgs[streak],
                    notif_type='streak_milestone',
                    session_id=sess.id
                ))

        db.session.commit()
        flash(f'Session marked as {new_status}.', 'success')
    return redirect(request.referrer or url_for('calendar_view'))


@app.route('/sessions/<int:session_id>/delete', methods=['POST'])
@admin_required
def delete_session(session_id):
    sess = Session.query.get_or_404(session_id)
    delete_all = request.form.get('delete_all') == '1'

    if delete_all:
        # Find all future sessions in the same series:
        # same trainer, same client (or group), same time-of-day, same weekday, on or after this session
        t = sess.scheduled_at.time()
        dow = sess.scheduled_at.weekday()
        if sess.group_id:
            siblings = Session.query.filter(
                Session.trainer_id == sess.trainer_id,
                Session.group_id == sess.group_id,
                Session.scheduled_at >= sess.scheduled_at,
            ).all()
        else:
            siblings = Session.query.filter(
                Session.trainer_id == sess.trainer_id,
                Session.client_id == sess.client_id,
                Session.scheduled_at >= sess.scheduled_at,
            ).all()
        # Keep only those on the same weekday and same time
        to_delete = [s for s in siblings
                     if s.scheduled_at.weekday() == dow
                     and s.scheduled_at.time() == t]
        count = len(to_delete)
        for s in to_delete:
            db.session.delete(s)
        db.session.commit()
        flash(f'{count} session{"s" if count != 1 else ""} deleted.', 'success')
    else:
        db.session.delete(sess)
        db.session.commit()
        flash('Session deleted.', 'success')

    return redirect(url_for('calendar_view'))


@app.route('/notifications/dismiss/<int:notif_id>', methods=['POST'])
@login_required
def dismiss_notification(notif_id):
    notif = Notification.query.get_or_404(notif_id)
    if notif.user_id != current_user.id:
        abort(403)
    notif.is_read = True
    db.session.commit()
    return ('', 204)


@app.route('/api/notifications/unread-count')
@login_required
def api_notifications_unread_count():
    if current_user.role != 'client':
        return jsonify({'count': 0})
    count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
    return jsonify({'count': count})


# ─── Admin Groups ─────────────────────────────────────────────────────────────

@app.route('/admin/groups')
@admin_required
def admin_groups():
    groups = TrainingGroup.query.order_by(TrainingGroup.name).all()
    trainers = User.query.filter(User.role.in_(['admin', 'trainer']), User.is_active == True).order_by(User.name).all()
    clients = User.query.filter_by(role='client', is_active=True).order_by(User.name).all()
    return render_template('admin_groups.html', groups=groups, trainers=trainers, clients=clients)


@app.route('/admin/groups/new', methods=['POST'])
@admin_required
def admin_new_group():
    name = request.form.get('name', '').strip()
    tid = request.form.get('trainer_id') or None
    trainer_id = int(tid) if tid else None
    if not name:
        flash('Group name is required.', 'danger')
        return redirect(url_for('admin_groups'))
    group = TrainingGroup(name=name, trainer_id=trainer_id)
    db.session.add(group)
    db.session.flush()
    for cid in request.form.getlist('client_ids'):
        db.session.add(GroupMembership(group_id=group.id, client_id=int(cid)))
    db.session.commit()
    flash(f'Group "{name}" created.', 'success')
    return redirect(url_for('admin_groups'))


@app.route('/admin/groups/<int:group_id>/edit', methods=['POST'])
@admin_required
def admin_edit_group(group_id):
    group = TrainingGroup.query.get_or_404(group_id)
    group.name = request.form.get('name', group.name).strip()
    tid = request.form.get('trainer_id') or None
    group.trainer_id = int(tid) if tid else None
    GroupMembership.query.filter_by(group_id=group.id).delete()
    for cid in request.form.getlist('client_ids'):
        db.session.add(GroupMembership(group_id=group.id, client_id=int(cid)))
    db.session.commit()
    flash(f'Group "{group.name}" updated.', 'success')
    return redirect(url_for('admin_groups'))


@app.route('/admin/groups/<int:group_id>/delete', methods=['POST'])
@admin_required
def admin_delete_group(group_id):
    group = TrainingGroup.query.get_or_404(group_id)
    name = group.name
    db.session.delete(group)
    db.session.commit()
    flash(f'Group "{name}" deleted.', 'success')
    return redirect(url_for('admin_groups'))


# ─── Clients ─────────────────────────────────────────────────────────────────

@app.route('/clients')
@staff_required
def clients():
    now = datetime.now(timezone.utc)
    month, year = now.month, now.year
    if current_user.role == 'trainer':
        all_clients = User.query.filter_by(role='client', is_active=True, trainer_id=current_user.id).order_by(User.name).all()
    else:
        all_clients = User.query.filter_by(role='client', is_active=True).order_by(User.name).all()

    client_data = []
    for c in all_clients:
        p, comp, rem = get_month_balance(c.id, month, year)
        next_sess = Session.query.filter(
            Session.client_id == c.id,
            Session.status == 'scheduled',
            Session.scheduled_at >= now
        ).order_by(Session.scheduled_at).first()
        pkg_count = SessionPackage.query.filter_by(client_id=c.id).count()
        client_data.append({
            'client': c, 'purchased': p, 'completed': comp, 'remaining': rem,
            'next_session': next_sess, 'pkg_count': pkg_count,
        })

    return render_template('clients.html', client_data=client_data, month_name=month_name[month])


@app.route('/clients/<int:client_id>')
@login_required
def client_detail(client_id):
    if current_user.role == 'client' and current_user.id != client_id:
        abort(403)
    client = User.query.get_or_404(client_id)
    if client.role != 'client':
        abort(404)
    # Trainers can only view their own assigned clients
    if current_user.role == 'trainer' and client.trainer_id != current_user.id:
        abort(403)

    now = datetime.now(timezone.utc)
    month, year = now.month, now.year
    p, comp, rem = get_month_balance(client.id, month, year)

    upcoming = Session.query.filter(
        Session.client_id == client.id,
        Session.status == 'scheduled',
        Session.scheduled_at >= now
    ).order_by(Session.scheduled_at).limit(10).all()

    recent = Session.query.filter(
        Session.client_id == client.id,
        Session.status.in_(['completed', 'missed']),
    ).order_by(Session.scheduled_at.desc()).limit(15).all()

    packages = SessionPackage.query.filter_by(client_id=client.id).order_by(
        SessionPackage.year.desc(), SessionPackage.month.desc()
    ).all()

    return render_template('client_detail.html',
        client=client,
        purchased=p, completed=comp, remaining=rem,
        upcoming=upcoming,
        recent=recent,
        packages=packages,
        month_name=month_name[month],
        year=year
    )


# ─── Admin: Users ────────────────────────────────────────────────────────────

@app.route('/admin/dedup-users', methods=['POST'])
@admin_required
def admin_dedup_users():
    from collections import defaultdict
    all_users = User.query.order_by(User.id).all()
    by_name = defaultdict(list)
    for u in all_users:
        by_name[u.name.strip().lower()].append(u)

    deleted = []
    for name, users in by_name.items():
        if len(users) < 2:
            continue
        keep = users[0]
        for d in users[1:]:
            for s in Session.query.filter_by(trainer_id=d.id).all():
                s.trainer_id = keep.id
            for s in Session.query.filter_by(client_id=d.id).all():
                s.client_id = keep.id
            for p in SessionPackage.query.filter_by(client_id=d.id).all():
                p.client_id = keep.id
            for gm in GroupMembership.query.filter_by(client_id=d.id).all():
                exists = GroupMembership.query.filter_by(group_id=gm.group_id, client_id=keep.id).first()
                if not exists:
                    gm.client_id = keep.id
                else:
                    db.session.delete(gm)
            for n in Notification.query.filter_by(user_id=d.id).all():
                n.user_id = keep.id
            for m in Message.query.filter_by(sender_id=d.id).all():
                m.sender_id = keep.id
            for m in Message.query.filter_by(recipient_id=d.id).all():
                m.recipient_id = keep.id
            deleted.append(f'{d.name} (id={d.id}, {d.email}) → merged into id={keep.id}')
            db.session.delete(d)
    db.session.commit()

    if deleted:
        flash(f'Deleted {len(deleted)} duplicate(s): ' + ' | '.join(deleted), 'success')
    else:
        flash('No duplicates found.', 'info')
    return redirect(url_for('admin_users'))


@app.route('/admin/users')
@admin_required
def admin_users():
    now = datetime.now(timezone.utc)
    users = User.query.order_by(User.role, User.name).all()
    trainers = User.query.filter(User.role.in_(['trainer', 'admin']), User.is_active == True).order_by(User.name).all()

    # Build client-specific data (package rate, session balance, next session)
    client_data = {}
    for u in users:
        if u.role == 'client':
            p, comp, rem = get_month_balance(u.id, now.month, now.year)
            next_sess = Session.query.filter(
                Session.client_id == u.id,
                Session.status == 'scheduled',
                Session.scheduled_at >= now,
            ).order_by(Session.scheduled_at).first()
            pkg_count = SessionPackage.query.filter_by(client_id=u.id).count()
            client_data[u.id] = {
                'purchased': p, 'completed': comp, 'remaining': rem,
                'next_session': next_sess, 'pkg_count': pkg_count,
            }

    return render_template('admin_users.html', users=users, trainers=trainers,
                           client_data=client_data, month_name=month_name[now.month])


@app.route('/admin/users/<int:user_id>/assign-trainer', methods=['POST'])
@admin_required
def admin_assign_trainer(user_id):
    user = User.query.get_or_404(user_id)
    if user.role != 'client':
        abort(400)
    raw = request.form.get('trainer_id', '').strip()
    user.trainer_id = int(raw) if raw else None
    db.session.commit()
    flash(f'Trainer assignment updated for {user.name}.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/new', methods=['GET', 'POST'])
@admin_required
def admin_new_user():
    trainers = User.query.filter(User.role.in_(['trainer', 'admin']), User.is_active == True).order_by(User.name).all()
    if request.method == 'POST':
        name = request.form['name'].strip()
        email = request.form['email'].strip().lower()
        role = request.form['role']
        password = request.form['password']
        trainerize_url = request.form.get('trainerize_url', '').strip() or None

        existing_email = User.query.filter_by(email=email).first()
        existing_name = User.query.filter(
            db.func.lower(User.name) == name.lower()
        ).first()
        if existing_email:
            flash('Email already in use.', 'danger')
        elif existing_name:
            flash(
                f'A user named "{existing_name.name}" already exists '
                f'({existing_name.role}, {existing_name.email}). '
                f'This would create a duplicate. Edit the existing account instead.',
                'danger'
            )
        else:
            raw = request.form.get('trainer_id', '').strip()
            trainer_id = int(raw) if raw and role == 'client' else None
            user = User(name=name, email=email, role=role, trainerize_url=trainerize_url, trainer_id=trainer_id)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash(f'{role.capitalize()} account created for {name}.', 'success')
            return redirect(url_for('admin_users'))
    return render_template('admin_user_form.html', user=None, trainers=trainers)


@app.route('/admin/users/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_edit_user(user_id):
    user = User.query.get_or_404(user_id)
    trainers = User.query.filter(User.role.in_(['trainer', 'admin']), User.is_active == True).order_by(User.name).all()
    if request.method == 'POST':
        user.name = request.form['name'].strip()
        user.email = request.form['email'].strip().lower()
        user.role = request.form['role']
        user.trainerize_url = request.form.get('trainerize_url', '').strip() or None
        user.is_active = 'is_active' in request.form
        if request.form.get('password'):
            user.set_password(request.form['password'])
        # Trainer assignment (clients only)
        if user.role == 'client':
            raw = request.form.get('trainer_id', '').strip()
            user.trainer_id = int(raw) if raw else None
        db.session.commit()
        flash('User updated.', 'success')
        return redirect(url_for('admin_users'))
    return render_template('admin_user_form.html', user=user, trainers=trainers)


# ─── Admin: Packages ─────────────────────────────────────────────────────────

@app.route('/admin/packages')
@admin_required
def admin_packages():
    now = datetime.now(timezone.utc)
    packages = SessionPackage.query.order_by(
        SessionPackage.year.desc(), SessionPackage.month.desc()
    ).all()
    clients = User.query.filter_by(role='client', is_active=True).order_by(User.name).all()
    plans = MembershipPlan.query.order_by(
        MembershipPlan.plan_type, MembershipPlan.is_crew, MembershipPlan.commitment, MembershipPlan.sessions_per_month
    ).all()
    return render_template('admin_packages.html',
        packages=packages, clients=clients, plans=plans,
        current_month=now.month, current_year=now.year,
        month_name=month_name
    )


@app.route('/admin/packages/new', methods=['POST'])
@admin_required
def admin_new_package():
    client_ids = [int(x) for x in request.form.getlist('client_ids') if x]
    if not client_ids:
        flash('Select at least one client.', 'danger')
        return redirect(url_for('admin_packages'))

    month = int(request.form['month'])
    year = int(request.form['year'])
    sessions_purchased = int(request.form['sessions_purchased'])
    notes = request.form.get('notes', '').strip()
    raw_plan = request.form.get('plan_id', '').strip()
    plan_id = int(raw_plan) if raw_plan else None
    plan_obj = db.session.get(MembershipPlan, plan_id) if plan_id else None
    is_crew = bool(plan_obj and plan_obj.is_crew)

    created, updated = 0, 0
    for client_id in client_ids:
        existing = SessionPackage.query.filter_by(client_id=client_id, month=month, year=year).first()
        if existing:
            existing.sessions_purchased = sessions_purchased
            existing.plan_id = plan_id
            existing.is_crew = is_crew
            existing.notes = notes
            updated += 1
        else:
            pkg = SessionPackage(
                client_id=client_id, month=month, year=year,
                sessions_purchased=sessions_purchased,
                plan_id=plan_id, is_crew=is_crew, notes=notes
            )
            db.session.add(pkg)
            created += 1

    db.session.commit()
    parts = []
    if created: parts.append(f'{created} created')
    if updated: parts.append(f'{updated} updated')
    flash(f"Packages saved for {len(client_ids)} client(s) — {', '.join(parts)}.", 'success')
    return redirect(url_for('admin_packages'))


@app.route('/admin/packages/<int:pkg_id>/delete', methods=['POST'])
@admin_required
def admin_delete_package(pkg_id):
    pkg = db.get_or_404(SessionPackage, pkg_id)
    try:
        db.session.delete(pkg)
        db.session.commit()
        flash('Package deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Could not delete package: {e}', 'danger')
    return redirect(url_for('admin_packages'))


# ─── Admin: Finances ──────────────────────────────────────────────────────────

@app.route('/admin/finances')
@admin_required
def admin_finances():
    now = datetime.now(timezone.utc)

    # Build last 6 months of data
    months_data = []
    for i in range(5, -1, -1):
        m = now.month - i
        y = now.year
        while m <= 0:
            m += 12
            y -= 1

        pkgs = SessionPackage.query.filter_by(month=m, year=y).all()
        revenue_cents = sum(
            p.plan.price_cents for p in pkgs if p.plan and p.plan.price_cents
        )

        if m == 12:
            month_start = datetime(y, m, 1, tzinfo=timezone.utc)
            month_end   = datetime(y + 1, 1, 1, tzinfo=timezone.utc)
        else:
            month_start = datetime(y, m, 1, tzinfo=timezone.utc)
            month_end   = datetime(y, m + 1, 1, tzinfo=timezone.utc)

        total_sessions = Session.query.filter(
            Session.scheduled_at >= month_start,
            Session.scheduled_at < month_end,
        ).count()
        completed = Session.query.filter(
            Session.scheduled_at >= month_start,
            Session.scheduled_at < month_end,
            Session.status == 'completed',
        ).count()
        missed = Session.query.filter(
            Session.scheduled_at >= month_start,
            Session.scheduled_at < month_end,
            Session.status == 'missed',
        ).count()

        avg_cost = (revenue_cents / 100 / total_sessions) if total_sessions else 0

        months_data.append({
            'month': m,
            'year': y,
            'month_name': month_name[m],
            'revenue_cents': revenue_cents,
            'total_sessions': total_sessions,
            'completed': completed,
            'missed': missed,
            'avg_cost': avg_cost,
        })

    # This week
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_sessions = Session.query.filter(
        Session.scheduled_at >= week_start,
        Session.scheduled_at < week_start + timedelta(days=7),
    ).count()

    # Session log: completed + missed (most recent 100)
    session_log = Session.query.filter(
        Session.status.in_(['completed', 'missed']),
    ).order_by(Session.scheduled_at.desc()).limit(100).all()

    current_month = months_data[-1]
    return render_template(
        'admin_finances.html',
        months_data=months_data,
        week_sessions=week_sessions,
        session_log=session_log,
        current_month=current_month,
        month_name=month_name,
    )


# ─── Admin: Promos ────────────────────────────────────────────────────────────

@app.route('/admin/promos')
@admin_required
def admin_promos():
    promos = Promo.query.order_by(Promo.created_at.desc()).all()
    return render_template('admin_promos.html', promos=promos)


@app.route('/admin/promos/new', methods=['POST'])
@admin_required
def admin_new_promo():
    start_raw = request.form.get('start_date') or None
    end_raw = request.form.get('end_date') or None
    from datetime import date as date_type
    promo = Promo(
        title=request.form['title'].strip(),
        description=request.form['description'].strip(),
        promo_type=request.form.get('promo_type', 'general'),
        start_date=date_type.fromisoformat(start_raw) if start_raw else None,
        end_date=date_type.fromisoformat(end_raw) if end_raw else None,
    )
    db.session.add(promo)
    db.session.commit()
    flash('Promo created.', 'success')
    return redirect(url_for('admin_promos'))


@app.route('/admin/promos/<int:promo_id>/toggle', methods=['POST'])
@admin_required
def toggle_promo(promo_id):
    promo = Promo.query.get_or_404(promo_id)
    promo.is_active = not promo.is_active
    db.session.commit()
    return redirect(url_for('admin_promos'))


@app.route('/admin/promos/<int:promo_id>/delete', methods=['POST'])
@admin_required
def delete_promo(promo_id):
    promo = Promo.query.get_or_404(promo_id)
    db.session.delete(promo)
    db.session.commit()
    flash('Promo deleted.', 'success')
    return redirect(url_for('admin_promos'))


# ─── Admin: Events ────────────────────────────────────────────────────────────

@app.route('/admin/events')
@admin_required
def admin_events():
    events = Event.query.order_by(Event.event_date).all()
    locations = Location.query.all()
    return render_template('admin_events.html', events=events, locations=locations,
                           now=datetime.now(timezone.utc))


@app.route('/admin/events/new', methods=['POST'])
@admin_required
def admin_new_event():
    location_id = request.form.get('location_id') or None
    event = Event(
        title=request.form['title'].strip(),
        description=request.form.get('description', '').strip(),
        event_date=datetime.fromisoformat(request.form['event_date']),
        location_id=int(location_id) if location_id else None,
    )
    db.session.add(event)
    db.session.commit()
    flash('Event created.', 'success')
    return redirect(url_for('admin_events'))


@app.route('/admin/events/<int:event_id>/toggle', methods=['POST'])
@admin_required
def toggle_event(event_id):
    event = Event.query.get_or_404(event_id)
    event.is_active = not event.is_active
    db.session.commit()
    return redirect(url_for('admin_events'))


@app.route('/admin/events/<int:event_id>/delete', methods=['POST'])
@admin_required
def delete_event(event_id):
    event = Event.query.get_or_404(event_id)
    db.session.delete(event)
    db.session.commit()
    flash('Event deleted.', 'success')
    return redirect(url_for('admin_events'))


# ─── Messages ─────────────────────────────────────────────────────────────────

@app.route('/api/messages/conversation/<int:user_id>')
@login_required
def api_get_conversation(user_id):
    """Get messages between current_user and user_id, newest last."""
    other = db.session.get(User, user_id)
    if not other:
        return jsonify({'error': 'User not found'}), 404
    msgs = Message.query.filter(
        db.or_(
            db.and_(Message.sender_id == current_user.id, Message.recipient_id == user_id),
            db.and_(Message.sender_id == user_id, Message.recipient_id == current_user.id)
        )
    ).order_by(Message.created_at.asc()).all()
    # Mark incoming as read
    for m in msgs:
        if m.recipient_id == current_user.id and not m.is_read:
            m.is_read = True
    db.session.commit()
    return jsonify([{
        'id': m.id,
        'body': m.body,
        'sender_id': m.sender_id,
        'sender_name': m.sender.name,
        'created_at': m.created_at.strftime('%I:%M %p'),
        'created_at_iso': m.created_at.isoformat(),
        'is_mine': m.sender_id == current_user.id,
    } for m in msgs])


@app.route('/api/messages/send', methods=['POST'])
@login_required
@csrf.exempt
def api_send_message():
    data = request.get_json()
    recipient_id = data.get('recipient_id')
    body = (data.get('body') or '').strip()
    if not recipient_id or not body:
        return jsonify({'error': 'Missing fields'}), 400
    recipient = db.session.get(User, recipient_id)
    if not recipient:
        return jsonify({'error': 'Recipient not found'}), 404
    msg = Message(sender_id=current_user.id, recipient_id=recipient_id, body=body)
    db.session.add(msg)
    db.session.commit()
    return jsonify({
        'id': msg.id,
        'body': msg.body,
        'sender_id': msg.sender_id,
        'sender_name': msg.sender.name,
        'created_at': msg.created_at.strftime('%I:%M %p'),
        'is_mine': True,
    })


@app.route('/api/messages/unread-count')
@login_required
def api_messages_unread_count():
    count = Message.query.filter_by(recipient_id=current_user.id, is_read=False).count()
    return jsonify({'count': count})


@app.route('/chat')
@login_required
def chat():
    """Full-screen chat page for clients."""
    if current_user.role != 'client':
        return redirect(url_for('dashboard'))
    return render_template('chat.html')


@app.route('/messages')
@login_required
def messages_inbox():
    """Staff inbox: see all conversations. Clients redirect to dashboard."""
    if current_user.role == 'client':
        return redirect(url_for('dashboard'))
    # Get unique conversation partners
    sent_to = db.session.query(Message.recipient_id).filter_by(sender_id=current_user.id)
    received_from = db.session.query(Message.sender_id).filter_by(recipient_id=current_user.id)
    partner_ids = set([r[0] for r in sent_to] + [r[0] for r in received_from])
    conversations = []
    for pid in partner_ids:
        partner = db.session.get(User, pid)
        if not partner:
            continue
        last_msg = Message.query.filter(
            db.or_(
                db.and_(Message.sender_id == current_user.id, Message.recipient_id == pid),
                db.and_(Message.sender_id == pid, Message.recipient_id == current_user.id)
            )
        ).order_by(Message.created_at.desc()).first()
        unread = Message.query.filter_by(sender_id=pid, recipient_id=current_user.id, is_read=False).count()
        conversations.append({'partner': partner, 'last_msg': last_msg, 'unread': unread})
    conversations.sort(key=lambda x: x['last_msg'].created_at if x['last_msg'] else datetime.min, reverse=True)
    return render_template('messages_inbox.html', conversations=conversations)


# ─── Training Modules: Models ────────────────────────────────────────────────

import re as _re

def extract_youtube_id(url):
    """Extract YouTube video ID from watch or short URL."""
    if not url:
        return None
    m = _re.search(r'(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_-]{11})', url)
    return m.group(1) if m else None


MOVEMENT_PATTERNS = ['Push', 'Pull', 'Squat', 'Hinge', 'Carry', 'Rotation', 'Isolation', 'Cardio']
EQUIPMENT_OPTIONS = ['Barbell', 'Dumbbell', 'Kettlebell', 'Cable', 'Machine', 'Bodyweight', 'Resistance Band', 'TRX / Suspension', 'Smith Machine', 'Other']

class Exercise(db.Model):
    __tablename__ = 'exercises'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    muscle_group = db.Column(db.String(80))
    category = db.Column(db.String(40))
    movement_pattern = db.Column(db.String(80))
    equipment = db.Column(db.String(80))
    instructions = db.Column(db.Text)
    youtube_url = db.Column(db.String(300))
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Workout(db.Model):
    __tablename__ = 'workouts'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text)
    trainer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    trainer = db.relationship('User', foreign_keys='Workout.trainer_id')
    exercises = db.relationship('WorkoutExercise', backref='workout', lazy='dynamic',
                                order_by='WorkoutExercise.order', cascade='all, delete-orphan')


class WorkoutExercise(db.Model):
    __tablename__ = 'workout_exercises'
    id = db.Column(db.Integer, primary_key=True)
    workout_id = db.Column(db.Integer, db.ForeignKey('workouts.id'), nullable=False)
    exercise_id = db.Column(db.Integer, db.ForeignKey('exercises.id'), nullable=False)
    order = db.Column(db.Integer, default=0)
    sets = db.Column(db.Integer, default=3)
    reps = db.Column(db.String(20), default='10')
    rest_seconds = db.Column(db.Integer, default=60)
    notes = db.Column(db.String(200))
    exercise = db.relationship('Exercise')


class Program(db.Model):
    __tablename__ = 'programs'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text)
    weeks = db.Column(db.Integer, default=4)
    trainer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    trainer = db.relationship('User', foreign_keys='Program.trainer_id')
    days = db.relationship('ProgramDay', backref='program', lazy='dynamic',
                           order_by='ProgramDay.week, ProgramDay.day',
                           cascade='all, delete-orphan')
    assignments = db.relationship('ProgramAssignment', backref='program', lazy='dynamic',
                                  cascade='all, delete-orphan')


class ProgramDay(db.Model):
    __tablename__ = 'program_days'
    id = db.Column(db.Integer, primary_key=True)
    program_id = db.Column(db.Integer, db.ForeignKey('programs.id'), nullable=False)
    week = db.Column(db.Integer, nullable=False)
    day = db.Column(db.Integer, nullable=False)
    workout_id = db.Column(db.Integer, db.ForeignKey('workouts.id'), nullable=True)
    label = db.Column(db.String(100))
    workout = db.relationship('Workout')


class ProgramAssignment(db.Model):
    __tablename__ = 'program_assignments'
    id = db.Column(db.Integer, primary_key=True)
    program_id = db.Column(db.Integer, db.ForeignKey('programs.id'), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    assigned_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    client = db.relationship('User', foreign_keys='ProgramAssignment.client_id')
    assigned_by = db.relationship('User', foreign_keys='ProgramAssignment.assigned_by_id')


class ProgramPhase(db.Model):
    __tablename__ = 'program_phases'
    id = db.Column(db.Integer, primary_key=True)
    program_id = db.Column(db.Integer, db.ForeignKey('programs.id'), nullable=False)
    phase_num = db.Column(db.Integer, nullable=False)   # 1, 2, 3, 4
    weeks = db.Column(db.Integer, nullable=False)        # 2, 3, or 4
    name = db.Column(db.String(100))                     # e.g. "Foundation", "Intensification"
    program = db.relationship('Program', backref=db.backref('phases', lazy='dynamic',
                              order_by='ProgramPhase.phase_num'))


class PhaseDay(db.Model):
    """One row per day slot in a phase. The same workout repeats every week of the phase."""
    __tablename__ = 'phase_days'
    id = db.Column(db.Integer, primary_key=True)
    phase_id = db.Column(db.Integer, db.ForeignKey('program_phases.id'), nullable=False)
    day_num = db.Column(db.Integer, nullable=False)      # 1=Mon, 2=Tue, ... 7=Sun
    workout_id = db.Column(db.Integer, db.ForeignKey('workouts.id'), nullable=True)
    label = db.Column(db.String(100))
    __table_args__ = (db.UniqueConstraint('phase_id', 'day_num', name='uq_phase_day'),)
    phase = db.relationship('ProgramPhase', backref=db.backref('days', lazy='dynamic'))
    workout = db.relationship('Workout')


class ProgressionOverride(db.Model):
    """Per-exercise, per-week set/rep/weight override within a phase."""
    __tablename__ = 'progression_overrides'
    id = db.Column(db.Integer, primary_key=True)
    phase_id = db.Column(db.Integer, db.ForeignKey('program_phases.id'), nullable=False)
    exercise_id = db.Column(db.Integer, db.ForeignKey('exercises.id'), nullable=False)
    week_num = db.Column(db.Integer, nullable=False)    # 1-based within the phase
    sets = db.Column(db.Integer)
    reps = db.Column(db.String(20))                     # "10", "8-12", "AMRAP"
    weight_note = db.Column(db.String(100))             # "65% 1RM", "RPE 8", "135 lbs"
    __table_args__ = (db.UniqueConstraint('phase_id', 'exercise_id', 'week_num', name='uq_progression'),)
    phase = db.relationship('ProgramPhase')
    exercise = db.relationship('Exercise')


class ProgressEntry(db.Model):
    __tablename__ = 'progress_entries'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    log_date = db.Column(db.Date, nullable=False)
    weight_lbs = db.Column(db.Float)
    chest_in = db.Column(db.Float)
    waist_in = db.Column(db.Float)
    hips_in = db.Column(db.Float)
    arms_in = db.Column(db.Float)
    legs_in = db.Column(db.Float)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class ExerciseLog(db.Model):
    __tablename__ = 'exercise_logs'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    exercise_id = db.Column(db.Integer, db.ForeignKey('exercises.id'), nullable=False)
    log_date = db.Column(db.Date, nullable=False)
    sets_completed = db.Column(db.Integer)
    reps_completed = db.Column(db.String(20))
    weight_lbs = db.Column(db.Float)
    notes = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    exercise = db.relationship('Exercise')


class WorkoutCheckin(db.Model):
    __tablename__ = 'workout_checkins'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    assignment_id = db.Column(db.Integer, db.ForeignKey('program_assignments.id'), nullable=False)
    week = db.Column(db.Integer, nullable=False)
    day = db.Column(db.Integer, nullable=False)
    completed_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (db.UniqueConstraint('client_id', 'assignment_id', 'week', 'day', name='uq_checkin'),)
    assignment = db.relationship('ProgramAssignment')


# ─── Training Modules: Routes ─────────────────────────────────────────────────

# MODULE 1: Exercise Library

@app.route('/exercises')
@login_required
def exercises_list():
    exercises = Exercise.query.order_by(Exercise.name).all()
    return render_template('exercises_list.html', exercises=exercises,
                           extract_youtube_id=extract_youtube_id)


@app.route('/exercises/new', methods=['GET', 'POST'])
@staff_required
def exercise_new():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Exercise name is required.', 'danger')
            return redirect(url_for('exercise_new'))
        ex = Exercise(
            name=name,
            muscle_group=request.form.get('muscle_group', '').strip() or None,
            category=request.form.get('category', '').strip() or None,
            movement_pattern=request.form.get('movement_pattern', '').strip() or None,
            equipment=request.form.get('equipment', '').strip() or None,
            instructions=request.form.get('instructions', '').strip() or None,
            youtube_url=request.form.get('youtube_url', '').strip() or None,
            created_by_id=current_user.id,
        )
        db.session.add(ex)
        db.session.commit()
        flash(f'Exercise "{ex.name}" created.', 'success')
        return redirect(url_for('exercises_list'))
    return render_template('exercise_form.html', ex=None,
                           movement_patterns=MOVEMENT_PATTERNS, equipment_options=EQUIPMENT_OPTIONS)


@app.route('/exercises/<int:ex_id>')
@login_required
def exercise_detail(ex_id):
    ex = Exercise.query.get_or_404(ex_id)
    return render_template('exercise_detail.html', ex=ex,
                           youtube_id=extract_youtube_id(ex.youtube_url))


@app.route('/exercises/<int:ex_id>/edit', methods=['GET', 'POST'])
@staff_required
def exercise_edit(ex_id):
    ex = Exercise.query.get_or_404(ex_id)
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Exercise name is required.', 'danger')
            return redirect(url_for('exercise_edit', ex_id=ex_id))
        ex.name = name
        ex.muscle_group = request.form.get('muscle_group', '').strip() or None
        ex.category = request.form.get('category', '').strip() or None
        ex.movement_pattern = request.form.get('movement_pattern', '').strip() or None
        ex.equipment = request.form.get('equipment', '').strip() or None
        ex.instructions = request.form.get('instructions', '').strip() or None
        ex.youtube_url = request.form.get('youtube_url', '').strip() or None
        db.session.commit()
        flash(f'Exercise "{ex.name}" updated.', 'success')
        return redirect(url_for('exercise_detail', ex_id=ex.id))
    return render_template('exercise_form.html', ex=ex,
                           movement_patterns=MOVEMENT_PATTERNS, equipment_options=EQUIPMENT_OPTIONS)


@app.route('/exercises/<int:ex_id>/delete', methods=['POST'])
@admin_required
def exercise_delete(ex_id):
    ex = Exercise.query.get_or_404(ex_id)
    name = ex.name
    db.session.delete(ex)
    db.session.commit()
    flash(f'Exercise "{name}" deleted.', 'success')
    return redirect(url_for('exercises_list'))


# MODULE 2: Workout Builder

@app.route('/workouts')
@staff_required
def workouts_list():
    if current_user.role == 'admin':
        workouts = Workout.query.order_by(Workout.created_at.desc()).all()
    else:
        workouts = Workout.query.filter_by(trainer_id=current_user.id).order_by(Workout.created_at.desc()).all()
    return render_template('workouts_list.html', workouts=workouts)


@app.route('/workouts/new', methods=['GET', 'POST'])
@staff_required
def workout_new():
    exercises = Exercise.query.order_by(Exercise.name).all()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Workout name is required.', 'danger')
            return render_template('workout_form.html', workout=None, exercises=exercises,
                               movement_patterns=MOVEMENT_PATTERNS, equipment_options=EQUIPMENT_OPTIONS)
        workout = Workout(
            name=name,
            description=request.form.get('description', '').strip() or None,
            trainer_id=current_user.id,
        )
        db.session.add(workout)
        db.session.flush()
        ex_ids = request.form.getlist('exercise_id[]')
        sets_list = request.form.getlist('sets[]')
        reps_list = request.form.getlist('reps[]')
        rest_list = request.form.getlist('rest[]')
        notes_list = request.form.getlist('notes_ex[]')
        for i, eid in enumerate(ex_ids):
            if not eid:
                continue
            we = WorkoutExercise(
                workout_id=workout.id,
                exercise_id=int(eid),
                order=i,
                sets=int(sets_list[i]) if i < len(sets_list) and sets_list[i] else 3,
                reps=reps_list[i] if i < len(reps_list) and reps_list[i] else '10',
                rest_seconds=int(rest_list[i]) if i < len(rest_list) and rest_list[i] else 60,
                notes=notes_list[i] if i < len(notes_list) and notes_list[i] else None,
            )
            db.session.add(we)
        db.session.commit()
        flash(f'Workout "{workout.name}" created.', 'success')
        return redirect(url_for('workout_detail', workout_id=workout.id))
    return render_template('workout_form.html', workout=None, exercises=exercises, movement_patterns=MOVEMENT_PATTERNS, equipment_options=EQUIPMENT_OPTIONS)


@app.route('/workouts/<int:workout_id>')
@login_required
def workout_detail(workout_id):
    workout = Workout.query.get_or_404(workout_id)
    # Clients can only view via /my-workout/<id>; staff can view any workout
    if current_user.role == 'client':
        abort(403)
    return render_template('workout_detail.html', workout=workout)


@app.route('/workouts/<int:workout_id>/edit', methods=['GET', 'POST'])
@staff_required
def workout_edit(workout_id):
    workout = Workout.query.get_or_404(workout_id)
    if current_user.role != 'admin' and workout.trainer_id != current_user.id:
        abort(403)
    exercises = Exercise.query.order_by(Exercise.name).all()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Workout name is required.', 'danger')
            return render_template('workout_form.html', workout=workout, exercises=exercises, movement_patterns=MOVEMENT_PATTERNS, equipment_options=EQUIPMENT_OPTIONS)
        workout.name = name
        workout.description = request.form.get('description', '').strip() or None
        # Clear existing exercises
        WorkoutExercise.query.filter_by(workout_id=workout.id).delete()
        ex_ids = request.form.getlist('exercise_id[]')
        sets_list = request.form.getlist('sets[]')
        reps_list = request.form.getlist('reps[]')
        rest_list = request.form.getlist('rest[]')
        notes_list = request.form.getlist('notes_ex[]')
        for i, eid in enumerate(ex_ids):
            if not eid:
                continue
            we = WorkoutExercise(
                workout_id=workout.id,
                exercise_id=int(eid),
                order=i,
                sets=int(sets_list[i]) if i < len(sets_list) and sets_list[i] else 3,
                reps=reps_list[i] if i < len(reps_list) and reps_list[i] else '10',
                rest_seconds=int(rest_list[i]) if i < len(rest_list) and rest_list[i] else 60,
                notes=notes_list[i] if i < len(notes_list) and notes_list[i] else None,
            )
            db.session.add(we)
        db.session.commit()
        flash(f'Workout "{workout.name}" updated.', 'success')
        return redirect(url_for('workout_detail', workout_id=workout.id))
    return render_template('workout_form.html', workout=workout, exercises=exercises, movement_patterns=MOVEMENT_PATTERNS, equipment_options=EQUIPMENT_OPTIONS)


@app.route('/workouts/<int:workout_id>/delete', methods=['POST'])
@staff_required
def workout_delete(workout_id):
    workout = Workout.query.get_or_404(workout_id)
    if current_user.role != 'admin' and workout.trainer_id != current_user.id:
        abort(403)
    name = workout.name
    db.session.delete(workout)
    db.session.commit()
    flash(f'Workout "{name}" deleted.', 'success')
    return redirect(url_for('workouts_list'))


# MODULE 3: Program Templates

@app.route('/programs')
@staff_required
def programs_list():
    if current_user.role == 'admin':
        progs = Program.query.order_by(Program.created_at.desc()).all()
    else:
        progs = Program.query.filter_by(trainer_id=current_user.id).order_by(Program.created_at.desc()).all()
    return render_template('programs_list.html', programs=progs)


@app.route('/programs/new', methods=['GET', 'POST'])
@staff_required
def program_new():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Program name is required.', 'danger')
            return render_template('program_form.html')
        num_phases = int(request.form.get('num_phases', 2))
        num_phases = max(2, min(4, num_phases))
        # Compute total weeks from phase lengths
        total_weeks = 0
        phase_data = []
        for i in range(1, num_phases + 1):
            ph_weeks = int(request.form.get(f'phase_{i}_weeks', 4))
            ph_weeks = max(2, min(4, ph_weeks))
            ph_name = request.form.get(f'phase_{i}_name', '').strip() or None
            total_weeks += ph_weeks
            phase_data.append({'phase_num': i, 'weeks': ph_weeks, 'name': ph_name})
        prog = Program(
            name=name,
            description=request.form.get('description', '').strip() or None,
            weeks=total_weeks,
            trainer_id=current_user.id,
        )
        db.session.add(prog)
        db.session.flush()
        for pd in phase_data:
            db.session.add(ProgramPhase(
                program_id=prog.id,
                phase_num=pd['phase_num'],
                weeks=pd['weeks'],
                name=pd['name'],
            ))
        db.session.commit()
        flash(f'Program "{prog.name}" created. Now build your phase schedule.', 'success')
        return redirect(url_for('program_phase_builder', program_id=prog.id))
    return render_template('program_form.html')


@app.route('/programs/<int:program_id>')
@staff_required
def program_detail(program_id):
    prog = Program.query.get_or_404(program_id)
    if current_user.role != 'admin' and prog.trainer_id != current_user.id:
        abort(403)
    if current_user.role == 'admin':
        clients = User.query.filter_by(role='client', is_active=True).order_by(User.name).all()
    else:
        clients = User.query.filter_by(role='client', is_active=True, trainer_id=current_user.id).order_by(User.name).all()
    day_map = {}
    for pd in prog.days.all():
        day_map[(pd.week, pd.day)] = pd
    from datetime import date as date_type
    assignments = prog.assignments.order_by(ProgramAssignment.start_date.desc()).all()
    return render_template('program_detail.html', prog=prog, day_map=day_map, clients=clients,
                           assignments=assignments, now_date=date_type.today().isoformat())


@app.route('/programs/<int:program_id>/builder')
@staff_required
def program_builder(program_id):
    """Legacy builder — redirect to new phase-based builder."""
    return redirect(url_for('program_phase_builder', program_id=program_id))


@app.route('/programs/<int:program_id>/phase-builder')
@staff_required
def program_phase_builder(program_id):
    prog = Program.query.get_or_404(program_id)
    if current_user.role != 'admin' and prog.trainer_id != current_user.id:
        abort(403)
    phases = prog.phases.all()
    if current_user.role == 'admin':
        workouts = Workout.query.order_by(Workout.name).all()
    else:
        workouts = Workout.query.filter_by(trainer_id=current_user.id).order_by(Workout.name).all()
    exercises = Exercise.query.order_by(Exercise.muscle_group, Exercise.name).all()
    # Build phase_day_map: {phase_id: {day_num: PhaseDay}}
    phase_day_map = {}
    for ph in phases:
        phase_day_map[ph.id] = {pd.day_num: pd for pd in ph.days.all()}
    return render_template('phase_builder.html', prog=prog, phases=phases,
                           workouts=workouts, exercises=exercises,
                           phase_day_map=phase_day_map)


@app.route('/api/workouts/inline', methods=['POST'])
@staff_required
@csrf.exempt
def api_workout_inline():
    """Create a workout inline from the phase builder. Returns {ok, workout:{id,name}}."""
    data = request.get_json()
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'Name required'}), 400
    workout = Workout(
        name=name,
        description=data.get('description', '').strip() or None,
        trainer_id=current_user.id,
    )
    db.session.add(workout)
    db.session.flush()
    for i, item in enumerate(data.get('exercises', [])):
        eid = item.get('exercise_id')
        if not eid:
            continue
        db.session.add(WorkoutExercise(
            workout_id=workout.id,
            exercise_id=int(eid),
            order=i,
            sets=3,
            reps='10',
            rest_seconds=60,
            notes=item.get('cues', '').strip() or None,
        ))
    db.session.commit()
    return jsonify({'ok': True, 'workout': {'id': workout.id, 'name': workout.name}})


@app.route('/programs/<int:program_id>/day', methods=['POST'])
@staff_required
@csrf.exempt
def program_set_day(program_id):
    prog = Program.query.get_or_404(program_id)
    if current_user.role != 'admin' and prog.trainer_id != current_user.id:
        return jsonify({'ok': False, 'error': 'Not authorized'}), 403
    data = request.get_json()
    week = int(data.get('week', 1))
    day = int(data.get('day', 1))
    workout_id = data.get('workout_id')
    label = data.get('label', '').strip() or None
    pd = ProgramDay.query.filter_by(program_id=prog.id, week=week, day=day).first()
    if pd is None:
        pd = ProgramDay(program_id=prog.id, week=week, day=day)
        db.session.add(pd)
    pd.workout_id = int(workout_id) if workout_id else None
    pd.label = label
    db.session.commit()
    workout_name = None
    if pd.workout_id:
        w = db.session.get(Workout, pd.workout_id)
        workout_name = w.name if w else None
    return jsonify({'ok': True, 'workout_name': workout_name, 'label': pd.label})


@app.route('/programs/<int:program_id>/phase/<int:phase_id>/day', methods=['POST'])
@staff_required
@csrf.exempt
def phase_set_day(program_id, phase_id):
    prog = Program.query.get_or_404(program_id)
    if current_user.role != 'admin' and prog.trainer_id != current_user.id:
        return jsonify({'ok': False, 'error': 'Not authorized'}), 403
    phase = ProgramPhase.query.get_or_404(phase_id)
    if phase.program_id != prog.id:
        return jsonify({'ok': False, 'error': 'Phase not in program'}), 400
    data = request.get_json()
    day_num = int(data.get('day_num', 1))
    workout_id = data.get('workout_id')
    label = data.get('label', '').strip() or None
    phd = PhaseDay.query.filter_by(phase_id=phase.id, day_num=day_num).first()
    if phd is None:
        phd = PhaseDay(phase_id=phase.id, day_num=day_num)
        db.session.add(phd)
    phd.workout_id = int(workout_id) if workout_id else None
    phd.label = label
    db.session.commit()
    workout_name = None
    if phd.workout_id:
        w = db.session.get(Workout, phd.workout_id)
        workout_name = w.name if w else None
    return jsonify({'ok': True, 'workout_name': workout_name, 'label': phd.label})


@app.route('/programs/<int:program_id>/phase/<int:phase_id>/progression')
@staff_required
def phase_progression(program_id, phase_id):
    prog = Program.query.get_or_404(program_id)
    if current_user.role != 'admin' and prog.trainer_id != current_user.id:
        abort(403)
    phase = ProgramPhase.query.get_or_404(phase_id)
    if phase.program_id != prog.id:
        abort(404)
    # Build workout groups: one entry per unique workout assigned in this phase,
    # in the order they first appear across the days (Mon→Sun).
    phase_days = sorted(phase.days.all(), key=lambda d: d.day_num)
    seen_workout_ids = []
    for pd in phase_days:
        if pd.workout_id and pd.workout_id not in seen_workout_ids:
            seen_workout_ids.append(pd.workout_id)

    workout_groups = []  # [(workout, [exercise, ...])]
    for wid in seen_workout_ids:
        w = db.session.get(Workout, wid)
        if w:
            exs = [we.exercise for we in w.exercises.order_by('order').all()]
            if exs:
                workout_groups.append((w, exs))

    # Load existing overrides for this phase
    overrides = ProgressionOverride.query.filter_by(phase_id=phase.id).all()
    override_map = {(o.exercise_id, o.week_num): o for o in overrides}
    weeks = list(range(1, phase.weeks + 1))
    return render_template('phase_progression.html', prog=prog, phase=phase,
                           workout_groups=workout_groups, weeks=weeks, override_map=override_map)


@app.route('/programs/<int:program_id>/phase/<int:phase_id>/progression', methods=['POST'])
@staff_required
def phase_progression_save(program_id, phase_id):
    prog = Program.query.get_or_404(program_id)
    if current_user.role != 'admin' and prog.trainer_id != current_user.id:
        abort(403)
    phase = ProgramPhase.query.get_or_404(phase_id)
    if phase.program_id != prog.id:
        abort(404)
    # Parse form: fields named ex_{ex_id}_w{week_num}_{field}
    # Group keys by (ex_id, week_num) pair first to allow partial updates
    combos = {}
    for key, val in request.form.items():
        if not key.startswith('ex_'):
            continue
        # key format: ex_{ex_id}_w{week_num}_{field}
        try:
            rest = key[3:]  # strip 'ex_'
            second_underscore = rest.index('_')
            ex_id = int(rest[:second_underscore])
            rest2 = rest[second_underscore + 1:]  # 'w{week_num}_{field}'
            third_underscore = rest2.index('_')
            week_str = rest2[:third_underscore]
            field = rest2[third_underscore + 1:]
            if not week_str.startswith('w'):
                continue
            week_num = int(week_str[1:])
        except (ValueError, IndexError):
            continue
        combos.setdefault((ex_id, week_num), {})[field] = val.strip()
    for (ex_id, week_num), fields in combos.items():
        override = ProgressionOverride.query.filter_by(
            phase_id=phase.id, exercise_id=ex_id, week_num=week_num
        ).first()
        if override is None:
            override = ProgressionOverride(phase_id=phase.id, exercise_id=ex_id, week_num=week_num)
            db.session.add(override)
        if 'sets' in fields:
            try:
                override.sets = int(fields['sets']) if fields['sets'] else None
            except ValueError:
                override.sets = None
        if 'reps' in fields:
            override.reps = fields['reps'] or None
        if 'weight_note' in fields:
            override.weight_note = fields['weight_note'] or None
    db.session.commit()
    flash('Progression chart saved.', 'success')
    return redirect(url_for('phase_progression', program_id=program_id, phase_id=phase_id))


@app.route('/programs/<int:program_id>/assign', methods=['POST'])
@staff_required
def program_assign(program_id):
    prog = Program.query.get_or_404(program_id)
    from datetime import date as date_type
    client_ids = request.form.getlist('client_ids')
    start_raw = request.form.get('start_date', '').strip()
    if not client_ids or not start_raw:
        flash('Select at least one client and a start date.', 'danger')
        return redirect(url_for('program_detail', program_id=program_id))
    start_date = date_type.fromisoformat(start_raw)
    # Trainers can only assign to their own clients
    if current_user.role != 'admin':
        allowed_ids = {str(c.id) for c in User.query.filter_by(role='client', trainer_id=current_user.id).all()}
        client_ids = [cid for cid in client_ids if cid in allowed_ids]
    if not client_ids:
        flash('No valid clients selected.', 'danger')
        return redirect(url_for('program_detail', program_id=program_id))
    assigned = 0
    for cid in client_ids:
        # Skip if this client already has this program assigned on this start date
        exists = ProgramAssignment.query.filter_by(
            program_id=prog.id, client_id=int(cid), start_date=start_date
        ).first()
        if exists:
            continue
        db.session.add(ProgramAssignment(
            program_id=prog.id,
            client_id=int(cid),
            start_date=start_date,
            assigned_by_id=current_user.id,
        ))
        assigned += 1
    db.session.commit()
    flash(f'Program assigned to {assigned} client(s).', 'success')
    return redirect(url_for('program_detail', program_id=program_id))


@app.route('/programs/<int:program_id>/delete', methods=['POST'])
@staff_required
def program_delete(program_id):
    prog = Program.query.get_or_404(program_id)
    if current_user.role != 'admin' and prog.trainer_id != current_user.id:
        abort(403)
    name = prog.name
    db.session.delete(prog)
    db.session.commit()
    flash(f'Program "{name}" deleted.', 'success')
    return redirect(url_for('programs_list'))


@app.route('/my-workout/<int:workout_id>')
@login_required
def client_workout_view(workout_id):
    """Read-only workout view accessible to clients (for their assigned program workouts)."""
    workout = Workout.query.get_or_404(workout_id)
    return render_template('workout_detail.html', workout=workout)


@app.route('/my-program')
@login_required
def my_program():
    if current_user.role != 'client':
        return redirect(url_for('dashboard'))
    from datetime import date as date_type
    today = date_type.today()
    # Find the most recent assignment
    assignment = ProgramAssignment.query.filter_by(
        client_id=current_user.id
    ).order_by(ProgramAssignment.start_date.desc()).first()

    current_week = None
    current_day = None
    today_program_day = None
    checkin_done = False
    program_complete = False
    week_days = {}  # {day_num: PhaseDay or ProgramDay} for current week

    # Phase-based tracking variables
    current_phase = None
    week_in_phase = None
    day_in_week = None
    total_phases = 0
    today_overrides = {}  # {exercise_id: ProgressionOverride}

    if assignment:
        days_since_start = (today - assignment.start_date).days
        phases = assignment.program.phases.all()

        if phases:
            # Phase-based program
            total_phases = len(phases)
            running_days = 0
            for ph in phases:
                phase_days_total = ph.weeks * 7
                if days_since_start < running_days + phase_days_total:
                    days_in_phase = days_since_start - running_days
                    week_in_phase = (days_in_phase // 7) + 1
                    day_in_week = (days_in_phase % 7) + 1
                    current_phase = ph
                    break
                running_days += phase_days_total
            if current_phase is None:
                # Past all phases
                program_complete = True
                current_phase = phases[-1]
                week_in_phase = phases[-1].weeks
                day_in_week = 7
            # current_week = global week number for compatibility
            current_week = week_in_phase
            current_day = day_in_week
            # Load phase days for the current phase (same every week)
            phase_day_rows = current_phase.days.all()
            week_days = {phd.day_num: phd for phd in phase_day_rows}
            today_program_day = week_days.get(day_in_week)
            if today_program_day and today_program_day.workout_id and not program_complete:
                # Load progression overrides for today's exercises this week
                exercise_ids = [we.exercise_id for we in today_program_day.workout.exercises.all()]
                for eid in exercise_ids:
                    ov = ProgressionOverride.query.filter_by(
                        phase_id=current_phase.id,
                        exercise_id=eid,
                        week_num=week_in_phase,
                    ).first()
                    if ov:
                        today_overrides[eid] = ov
            if today_program_day:
                checkin_done = WorkoutCheckin.query.filter_by(
                    client_id=current_user.id,
                    assignment_id=assignment.id,
                    week=current_week,
                    day=current_day,
                ).first() is not None
        else:
            # Legacy flat-week program
            total_program_days = assignment.program.weeks * 7
            if days_since_start >= total_program_days:
                program_complete = True
                current_week = assignment.program.weeks
                current_day = 7
            else:
                current_week = (days_since_start // 7) + 1
                current_day = (days_since_start % 7) + 1
            week_day_rows = ProgramDay.query.filter_by(
                program_id=assignment.program_id, week=current_week
            ).all()
            week_days = {pd.day: pd for pd in week_day_rows}
            today_program_day = week_days.get(current_day)
            if today_program_day:
                checkin_done = WorkoutCheckin.query.filter_by(
                    client_id=current_user.id,
                    assignment_id=assignment.id,
                    week=current_week,
                    day=current_day,
                ).first() is not None

    return render_template('my_program.html',
        assignment=assignment,
        current_week=current_week,
        current_day=current_day,
        today_program_day=today_program_day,
        checkin_done=checkin_done,
        today=today,
        program_complete=program_complete,
        week_days=week_days,
        # Phase-based extras
        current_phase=current_phase,
        week_in_phase=week_in_phase,
        day_in_week=day_in_week,
        total_phases=total_phases,
        today_overrides=today_overrides,
    )


# MODULE 4: Progress Tracking

@app.route('/progress')
@login_required
def client_progress():
    if current_user.role not in ('client',):
        return redirect(url_for('dashboard'))
    from datetime import date as date_type
    entries = ProgressEntry.query.filter_by(client_id=current_user.id).order_by(ProgressEntry.log_date).all()
    ex_logs = ExerciseLog.query.filter_by(client_id=current_user.id).order_by(ExerciseLog.log_date.desc()).all()
    exercises = Exercise.query.order_by(Exercise.name).all()
    return render_template('progress_client.html',
        entries=entries, ex_logs=ex_logs, exercises=exercises,
        client=current_user, today_date=date_type.today().isoformat())


@app.route('/progress/log', methods=['POST'])
@login_required
def progress_log():
    if current_user.role != 'client':
        abort(403)
    from datetime import date as date_type
    def _float(key):
        val = request.form.get(key, '').strip()
        try:
            return float(val) if val else None
        except ValueError:
            return None
    raw_date = request.form.get('log_date', '').strip()
    log_date = date_type.fromisoformat(raw_date) if raw_date else date_type.today()
    entry = ProgressEntry(
        client_id=current_user.id,
        log_date=log_date,
        weight_lbs=_float('weight_lbs'),
        chest_in=_float('chest_in'),
        waist_in=_float('waist_in'),
        hips_in=_float('hips_in'),
        arms_in=_float('arms_in'),
        legs_in=_float('legs_in'),
        notes=request.form.get('notes', '').strip() or None,
    )
    db.session.add(entry)
    db.session.commit()
    flash('Progress logged!', 'success')
    return redirect(url_for('client_progress'))


@app.route('/progress/log-exercise', methods=['POST'])
@login_required
def progress_log_exercise():
    if current_user.role != 'client':
        abort(403)
    from datetime import date as date_type
    raw_date = request.form.get('log_date', '').strip()
    log_date = date_type.fromisoformat(raw_date) if raw_date else date_type.today()
    ex_id = request.form.get('exercise_id', '').strip()
    if not ex_id:
        flash('Select an exercise.', 'danger')
        return redirect(url_for('client_progress'))
    def _float(key):
        val = request.form.get(key, '').strip()
        try:
            return float(val) if val else None
        except ValueError:
            return None
    log = ExerciseLog(
        client_id=current_user.id,
        exercise_id=int(ex_id),
        log_date=log_date,
        sets_completed=int(request.form.get('sets_completed', 0) or 0) or None,
        reps_completed=request.form.get('reps_completed', '').strip() or None,
        weight_lbs=_float('weight_lbs'),
        notes=request.form.get('notes', '').strip() or None,
    )
    db.session.add(log)
    db.session.commit()
    flash('Exercise logged!', 'success')
    return redirect(url_for('client_progress'))


@app.route('/progress/<int:client_id>')
@staff_required
def staff_progress(client_id):
    client = User.query.get_or_404(client_id)
    if client.role != 'client':
        abort(404)
    if current_user.role == 'trainer' and client.trainer_id != current_user.id:
        abort(403)
    entries = ProgressEntry.query.filter_by(client_id=client_id).order_by(ProgressEntry.log_date).all()
    ex_logs = ExerciseLog.query.filter_by(client_id=client_id).order_by(ExerciseLog.log_date.desc()).all()
    return render_template('progress_staff.html', client=client, entries=entries, ex_logs=ex_logs)


# MODULE 5: Check-ins & Compliance

@app.route('/checkin', methods=['POST'])
@login_required
@csrf.exempt
def workout_checkin():
    if current_user.role != 'client':
        return jsonify({'ok': False, 'error': 'Clients only'}), 403
    data = request.get_json()
    assignment_id = data.get('assignment_id')
    week = data.get('week')
    day = data.get('day')
    if not all([assignment_id, week, day]):
        return jsonify({'ok': False, 'error': 'Missing fields'}), 400
    # Verify the assignment belongs to this client
    assignment = ProgramAssignment.query.filter_by(
        id=int(assignment_id), client_id=current_user.id
    ).first()
    if not assignment:
        return jsonify({'ok': False, 'error': 'Assignment not found'}), 404
    existing = WorkoutCheckin.query.filter_by(
        client_id=current_user.id,
        assignment_id=int(assignment_id),
        week=int(week),
        day=int(day)
    ).first()
    if existing:
        return jsonify({'ok': True, 'already_done': True})
    checkin = WorkoutCheckin(
        client_id=current_user.id,
        assignment_id=int(assignment_id),
        week=int(week),
        day=int(day),
    )
    db.session.add(checkin)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/compliance')
@staff_required
def compliance_dashboard():
    from datetime import date as date_type
    today = date_type.today()
    # Get all active clients with an assignment
    all_assignments = ProgramAssignment.query.all()
    # One row per client (most recent assignment)
    seen_clients = {}
    for pa in sorted(all_assignments, key=lambda x: x.start_date, reverse=True):
        if pa.client_id not in seen_clients:
            seen_clients[pa.client_id] = pa

    # Bulk-load all program days for relevant programs in one query
    program_ids = {pa.program_id for pa in seen_clients.values()}
    all_program_days = ProgramDay.query.filter(ProgramDay.program_id.in_(program_ids)).all()
    days_by_program = {}
    for pd in all_program_days:
        days_by_program.setdefault(pd.program_id, []).append(pd)

    rows = []
    for client_id, pa in seen_clients.items():
        client = db.session.get(User, client_id)
        if not client or not client.is_active:
            continue
        days_since_start = (today - pa.start_date).days
        if days_since_start < 0:
            continue
        prog = pa.program
        current_week = min((days_since_start // 7) + 1, prog.weeks)
        current_day_num = (days_since_start % 7) + 1
        all_days = days_by_program.get(prog.id, [])
        assigned_so_far = [
            pd for pd in all_days
            if pd.workout_id is not None and (
                pd.week < current_week or
                (pd.week == current_week and pd.day <= current_day_num)
            )
        ]
        total_assigned = len(assigned_so_far)
        completed = WorkoutCheckin.query.filter_by(
            client_id=client_id, assignment_id=pa.id
        ).count()
        if total_assigned > 0:
            rate = int(completed / total_assigned * 100)
        else:
            rate = 0
        rows.append({
            'client': client,
            'program': prog,
            'total_assigned': total_assigned,
            'completed': completed,
            'rate': rate,
        })
    rows.sort(key=lambda x: x['rate'])
    return render_template('compliance.html', rows=rows)


# ─── Error Handlers ──────────────────────────────────────────────────────────

@app.errorhandler(403)
def forbidden(e):
    return render_template('error.html', code=403, message="You don't have permission to view this page."), 403


@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', code=404, message="Page not found."), 404


@app.errorhandler(500)
def internal_error(e):
    app.logger.exception("Unhandled 500 error")
    db.session.rollback()
    return render_template('error.html', code=500, message="Something went wrong. Please try again."), 500


# ─── Init ────────────────────────────────────────────────────────────────────

def init_db():
    db.create_all()
    # ── Column migrations (db.create_all won't add columns to existing tables) ─
    with db.engine.connect() as conn:
        for col, ddl in [
            ('plan_id',  'ALTER TABLE session_packages ADD COLUMN plan_id INTEGER REFERENCES membership_plans(id)'),
            ('is_crew',  'ALTER TABLE session_packages ADD COLUMN is_crew BOOLEAN NOT NULL DEFAULT FALSE'),
        ]:
            try:
                conn.execute(db.text(ddl))
                conn.commit()
            except Exception:
                conn.rollback()
    if not Location.query.first():
        db.session.add_all([
            Location(name='West Mobile', color='#7c3aed'),
            Location(name='Midtown', color='#0891b2'),
        ])
        db.session.commit()
        print('Locations seeded.')
    admin = User.query.filter_by(email='nick@fitforlife.com').first()
    if not admin:
        admin = User(name='Nick Wells', email='nick@fitforlife.com', role='admin')
        admin.set_password('Wells2026')
        db.session.add(admin)
        db.session.commit()
        print('Admin created: nick@fitforlife.com / Wells2026')
    elif admin.role != 'admin':
        admin.role = 'admin'
        db.session.commit()
    for full_name, email, password in [
        ('Carrie Cox',   'carrie@fitforlife.com', 'Cox2026'),
        ('Marie Berry',  'marie@fitforlife.com',  'Berry2026'),
    ]:
        u = User.query.filter_by(email=email).first()
        if not u:
            u = User(name=full_name, email=email, role='admin')
            u.set_password(password)
            db.session.add(u)
            db.session.commit()
            print(f'Admin created: {email}')
        elif u.role != 'admin':
            u.role = 'admin'
            db.session.commit()
    # Client accounts are managed exclusively through the admin UI (/admin/users/new).
    # Seeding clients here caused duplicates on every deploy whenever a client was
    # added via the UI with a different email than the seeded one.

    if not MembershipPlan.query.first():
        plans = [
            # ── 1-on-1 Standard (Non-Member) ──────────────────────────────
            MembershipPlan(name='1-on-1 · 4x/mo · Month-to-Month',  plan_type='1on1', sessions_per_month=4,  commitment='month-to-month', is_crew=False, price_cents=30000),
            MembershipPlan(name='1-on-1 · 8x/mo · Month-to-Month',  plan_type='1on1', sessions_per_month=8,  commitment='month-to-month', is_crew=False, price_cents=57500),
            MembershipPlan(name='1-on-1 · 12x/mo · Month-to-Month', plan_type='1on1', sessions_per_month=12, commitment='month-to-month', is_crew=False, price_cents=82000),
            MembershipPlan(name='1-on-1 · 4x/mo · 6-Month Commit',  plan_type='1on1', sessions_per_month=4,  commitment='6-month', is_crew=False, price_cents=23500),
            MembershipPlan(name='1-on-1 · 8x/mo · 6-Month Commit',  plan_type='1on1', sessions_per_month=8,  commitment='6-month', is_crew=False, price_cents=43000),
            MembershipPlan(name='1-on-1 · 12x/mo · 6-Month Commit', plan_type='1on1', sessions_per_month=12, commitment='6-month', is_crew=False, price_cents=61500),
            # ── 1-on-1 Crew (Member Discount) ─────────────────────────────
            MembershipPlan(name='1-on-1 · 4x/mo · Month-to-Month',  plan_type='1on1', sessions_per_month=4,  commitment='month-to-month', is_crew=True, price_cents=24000),
            MembershipPlan(name='1-on-1 · 8x/mo · Month-to-Month',  plan_type='1on1', sessions_per_month=8,  commitment='month-to-month', is_crew=True, price_cents=43000),
            MembershipPlan(name='1-on-1 · 12x/mo · Month-to-Month', plan_type='1on1', sessions_per_month=12, commitment='month-to-month', is_crew=True, price_cents=61500),
            MembershipPlan(name='1-on-1 · 4x/mo · 6-Month Commit',  plan_type='1on1', sessions_per_month=4,  commitment='6-month', is_crew=True, price_cents=18000),
            MembershipPlan(name='1-on-1 · 8x/mo · 6-Month Commit',  plan_type='1on1', sessions_per_month=8,  commitment='6-month', is_crew=True, price_cents=34500),
            MembershipPlan(name='1-on-1 · 12x/mo · 6-Month Commit', plan_type='1on1', sessions_per_month=12, commitment='6-month', is_crew=True, price_cents=50000),
            # ── Small Group 2-Person · Month-to-Month ─────────────────────
            MembershipPlan(name='Group 2 · 4x/mo · Month-to-Month',  plan_type='group-2', sessions_per_month=4,  commitment='month-to-month', is_crew=False, price_cents=21500),
            MembershipPlan(name='Group 2 · 8x/mo · Month-to-Month',  plan_type='group-2', sessions_per_month=8,  commitment='month-to-month', is_crew=False, price_cents=40000),
            MembershipPlan(name='Group 2 · 12x/mo · Month-to-Month', plan_type='group-2', sessions_per_month=12, commitment='month-to-month', is_crew=False, price_cents=54000),
            MembershipPlan(name='Group 2 · 4x/mo · Month-to-Month',  plan_type='group-2', sessions_per_month=4,  commitment='month-to-month', is_crew=True, price_cents=17000),
            MembershipPlan(name='Group 2 · 8x/mo · Month-to-Month',  plan_type='group-2', sessions_per_month=8,  commitment='month-to-month', is_crew=True, price_cents=32000),
            MembershipPlan(name='Group 2 · 12x/mo · Month-to-Month', plan_type='group-2', sessions_per_month=12, commitment='month-to-month', is_crew=True, price_cents=43000),
            # ── Small Group 3-Person · 6-Month Contract ───────────────────
            MembershipPlan(name='Group 3 · 4x/mo · 6-Month Commit',  plan_type='group-3', sessions_per_month=4,  commitment='6-month', is_crew=False, price_cents=14500),
            MembershipPlan(name='Group 3 · 8x/mo · 6-Month Commit',  plan_type='group-3', sessions_per_month=8,  commitment='6-month', is_crew=False, price_cents=28000),
            MembershipPlan(name='Group 3 · 12x/mo · 6-Month Commit', plan_type='group-3', sessions_per_month=12, commitment='6-month', is_crew=False, price_cents=36000),
            MembershipPlan(name='Group 3 · 4x/mo · 6-Month Commit',  plan_type='group-3', sessions_per_month=4,  commitment='6-month', is_crew=True, price_cents=12500),
            MembershipPlan(name='Group 3 · 8x/mo · 6-Month Commit',  plan_type='group-3', sessions_per_month=8,  commitment='6-month', is_crew=True, price_cents=22500),
            MembershipPlan(name='Group 3 · 12x/mo · 6-Month Commit', plan_type='group-3', sessions_per_month=12, commitment='6-month', is_crew=True, price_cents=28500),
            # ── Small Group 4-Person · Month-to-Month ─────────────────────
            MembershipPlan(name='Group 4 · 4x/mo · Month-to-Month',  plan_type='group-4', sessions_per_month=4,  commitment='month-to-month', is_crew=False, price_cents=12500),
            MembershipPlan(name='Group 4 · 8x/mo · Month-to-Month',  plan_type='group-4', sessions_per_month=8,  commitment='month-to-month', is_crew=False, price_cents=23000),
            MembershipPlan(name='Group 4 · 12x/mo · Month-to-Month', plan_type='group-4', sessions_per_month=12, commitment='month-to-month', is_crew=False, price_cents=32000),
            MembershipPlan(name='Group 4 · 4x/mo · Month-to-Month',  plan_type='group-4', sessions_per_month=4,  commitment='month-to-month', is_crew=True, price_cents=10500),
            MembershipPlan(name='Group 4 · 8x/mo · Month-to-Month',  plan_type='group-4', sessions_per_month=8,  commitment='month-to-month', is_crew=True, price_cents=18500),
            MembershipPlan(name='Group 4 · 12x/mo · Month-to-Month', plan_type='group-4', sessions_per_month=12, commitment='month-to-month', is_crew=True, price_cents=25500),
            # ── Small Group 4-Person · 6-Month Contract ───────────────────
            MembershipPlan(name='Group 4 · 4x/mo · 6-Month Commit',  plan_type='group-4', sessions_per_month=4,  commitment='6-month', is_crew=False, price_cents=11000),
            MembershipPlan(name='Group 4 · 8x/mo · 6-Month Commit',  plan_type='group-4', sessions_per_month=8,  commitment='6-month', is_crew=False, price_cents=20000),
            MembershipPlan(name='Group 4 · 12x/mo · 6-Month Commit', plan_type='group-4', sessions_per_month=12, commitment='6-month', is_crew=False, price_cents=28000),
            MembershipPlan(name='Group 4 · 4x/mo · 6-Month Commit',  plan_type='group-4', sessions_per_month=4,  commitment='6-month', is_crew=True, price_cents=9500),
            MembershipPlan(name='Group 4 · 8x/mo · 6-Month Commit',  plan_type='group-4', sessions_per_month=8,  commitment='6-month', is_crew=True, price_cents=16500),
            MembershipPlan(name='Group 4 · 12x/mo · 6-Month Commit', plan_type='group-4', sessions_per_month=12, commitment='6-month', is_crew=True, price_cents=24500),
        ]
        db.session.add_all(plans)
        db.session.commit()
        print(f'Membership plans seeded ({len(plans)} plans).')


with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(debug=True, port=5001)
