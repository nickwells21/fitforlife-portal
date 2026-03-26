import os
import logging
from datetime import datetime, timedelta, timezone
from calendar import month_name
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import stripe as _stripe

load_dotenv()

# Stripe config — set STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET in environment
_stripe.api_key = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')

app = Flask(__name__)

# SECRET_KEY — use env var, fall back to a generated key with warning
_secret_key = os.environ.get('SECRET_KEY')
if not _secret_key:
    import secrets as _secrets
    _secret_key = _secrets.token_hex(32)
    logging.warning('SECRET_KEY not set — generated a random key. Sessions will reset on restart. Set SECRET_KEY in environment for persistence.')
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

# Secure session cookies — Secure=True in production, Strict SameSite always
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') != 'development'
app.config['SESSION_COOKIE_SAMESITE'] = 'Strict'
app.config['SESSION_COOKIE_HTTPONLY'] = True

csrf = CSRFProtect(app)
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Rate limiting
limiter = Limiter(app=app, key_func=get_remote_address, default_limits=["300 per hour"])

# Audit logger
audit_logger = logging.getLogger("ffl_portal_audit")
audit_logger.setLevel(logging.INFO)
_audit_handler = logging.StreamHandler()
_audit_handler.setFormatter(logging.Formatter("%(asctime)s AUDIT %(message)s"))
audit_logger.addHandler(_audit_handler)

# Account lockout tracking (in-memory — resets on restart, which is acceptable)
_failed_logins = {}  # {email: {'count': int, 'locked_until': datetime}}


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
    stripe_customer_id = db.Column(db.String(100), unique=True, nullable=True)
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
    stripe_price_id = db.Column(db.String(100), nullable=True)  # Stripe Price object ID
    stripe_product_id = db.Column(db.String(100), nullable=True)  # Stripe Product object ID


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


class ClientSubscription(db.Model):
    """Tracks a client's active Stripe recurring subscription."""
    __tablename__ = 'client_subscriptions'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    plan_id = db.Column(db.Integer, db.ForeignKey('membership_plans.id'), nullable=False)
    stripe_subscription_id = db.Column(db.String(100), unique=True, nullable=True)
    status = db.Column(db.String(30), default='active')  # active, past_due, canceled, paused
    billing_day = db.Column(db.Integer, default=1)  # Day of month to charge (1-28)
    current_period_start = db.Column(db.Date, nullable=True)
    current_period_end = db.Column(db.Date, nullable=True)
    canceled_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    client = db.relationship('User', foreign_keys=[client_id])
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
    title = db.Column(db.String(120), nullable=True)
    message = db.Column(db.String(400), nullable=False)
    notif_type = db.Column(db.String(50), default='session_completed')
    # notif_type: see CURSOR_AGENT_SPEC.md §3 for full list
    icon = db.Column(db.String(50), nullable=True)  # bootstrap icon class
    level = db.Column(db.String(20), nullable=True)  # bronze, silver, gold, platinum
    session_id = db.Column(db.Integer, db.ForeignKey('sessions.id'), nullable=True)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    user = db.relationship('User', foreign_keys=[user_id])
    __table_args__ = (db.Index('ix_notif_user_unread', 'user_id', 'is_read', 'created_at'),)


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


# ─── Security Headers ────────────────────────────────────────────────────────

@app.after_request
def set_security_headers(response):
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    if os.environ.get('FLASK_ENV') != 'development':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' data:; "
        "frame-src https://www.youtube.com https://www.youtube-nocookie.com; "
        "connect-src 'self'"
    )
    return response


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

# 2026 FFL Annual Calendar — matches the official FFL calendar
PROGRAM_QUARTERS = [
    {'name': 'Q1', 'title': 'Raise the Standard', 'subtitle': 'FFL Base',    'weeks': 12, 'color': '#1e9df1', 'label': '12 Weeks', 'start': datetime(2026, 2, 2),  'end': datetime(2026, 4, 24), 'assessment': datetime(2026, 4, 20)},
    {'name': 'Q2', 'title': 'Strength+',          'subtitle': 'FFL Build',   'weeks': 12, 'color': '#22c55e', 'label': '12 Weeks', 'start': datetime(2026, 5, 4),  'end': datetime(2026, 7, 24), 'assessment': datetime(2026, 7, 20)},
    {'name': 'Q3', 'title': 'Joint Integrity',    'subtitle': 'FFL Restore', 'weeks': 9,  'color': '#f59e0b', 'label': '9 Weeks',  'start': datetime(2026, 8, 3),  'end': datetime(2026, 9, 28), 'assessment': datetime(2026, 9, 28)},
    {'name': 'Q4', 'title': 'Performance & Sustainability', 'subtitle': 'FFL Peak', 'weeks': 9, 'color': '#ef4444', 'label': '9 Weeks', 'start': datetime(2026, 10, 12), 'end': datetime(2026, 12, 11), 'assessment': datetime(2026, 12, 7)},
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
            weeks_elapsed = min(q['weeks'], int(elapsed_secs / (7 * 86400)) + 1)
            return {
                'index': i,
                'name': q['name'],
                'title': q.get('title', ''),
                'subtitle': q.get('subtitle', ''),
                'color': q.get('color', '#1e9df1'),
                'weeks': q.get('weeks', 12),
                'weeks_elapsed': weeks_elapsed,
                'label': q['label'],
                'pct': pct,
                'start': q['start'],
                'end': q['end'],
                'assessment': q.get('assessment'),
                'quarters': PROGRAM_QUARTERS,
                'active': True,
            }
    # Between quarters or before/after program year
    next_q = next((q for q in PROGRAM_QUARTERS if q['start'] > now_dt), None)
    return {
        'index': None,
        'name': None,
        'title': '',
        'subtitle': '',
        'color': '#1e9df1',
        'weeks': 0,
        'weeks_elapsed': 0,
        'label': 'Program Break' if not next_q else f'Next: {next_q["title"]}',
        'pct': 0,
        'start': None,
        'end': next_q['start'] if next_q else None,
        'assessment': None,
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
    """Consecutive weeks (going back) with at least 1 completed session.
    If the current week has no completed sessions yet (still in progress),
    we skip it so that a maintained streak from previous weeks still shows."""
    now = datetime.now(timezone.utc)
    week_start = now.date() - timedelta(days=now.weekday())

    # Check current week — if no sessions yet, start counting from last week
    week_end = week_start + timedelta(days=7)
    current_week_count = Session.query.filter(
        Session.client_id == client_id,
        Session.status == 'completed',
        Session.scheduled_at >= datetime(week_start.year, week_start.month, week_start.day),
        Session.scheduled_at < datetime(week_end.year, week_end.month, week_end.day),
    ).count()
    if current_week_count == 0:
        week_start -= timedelta(days=7)

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
@limiter.limit("5 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        # Account lockout check
        lockout = _failed_logins.get(email, {})
        locked_until = lockout.get('locked_until')
        if locked_until and datetime.now(timezone.utc) < locked_until:
            remaining = int((locked_until - datetime.now(timezone.utc)).total_seconds() // 60) + 1
            flash(f'Account temporarily locked. Try again in {remaining} minutes.', 'danger')
            audit_logger.warning("LOGIN_LOCKED email=%s ip=%s", email, request.remote_addr)
            return render_template('login.html')

        user = User.query.filter_by(email=email).first()
        if user and user.is_active and user.check_password(password):
            # Reset failed attempts on success
            _failed_logins.pop(email, None)
            login_user(user)
            audit_logger.info("LOGIN_SUCCESS user_id=%s email=%s ip=%s", user.id, email, request.remote_addr)
            return redirect(url_for('dashboard'))

        # Track failed attempts
        if email not in _failed_logins:
            _failed_logins[email] = {'count': 0, 'locked_until': None}
        _failed_logins[email]['count'] += 1
        if _failed_logins[email]['count'] >= 5:
            _failed_logins[email]['locked_until'] = datetime.now(timezone.utc) + timedelta(minutes=15)
            flash('Too many failed attempts. Account locked for 15 minutes.', 'danger')
            audit_logger.warning("LOGIN_LOCKOUT email=%s ip=%s attempts=%s", email, request.remote_addr, _failed_logins[email]['count'])
        else:
            flash('Invalid email or password.', 'danger')
            audit_logger.warning("LOGIN_FAILED email=%s ip=%s attempt=%s", email, request.remote_addr, _failed_logins[email]['count'])
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

        # ── Next program workout ───────────────────────────────────────────────
        next_program_workout = None   # Workout object
        next_program_exercises = []   # [Exercise, ...]  names only
        next_workout_phase_id = None
        next_workout_week = None

        assignment = ProgramAssignment.query.filter_by(
            client_id=current_user.id
        ).order_by(ProgramAssignment.start_date.desc()).first()

        if assignment:
            days_since_start = (today - assignment.start_date).days
            if days_since_start >= 0:
                phases = assignment.program.phases.order_by(ProgramPhase.phase_num).all()
                running = 0
                for ph in phases:
                    phase_total = ph.weeks * 7
                    if days_since_start < running + phase_total:
                        days_in_phase = days_since_start - running
                        current_day_num = (days_in_phase % 7) + 1   # 1=Mon … 7=Sun
                        week_in_phase   = (days_in_phase // 7) + 1
                        # Search from today's weekday slot forward in the phase
                        for d in range(current_day_num, 8):
                            phd = PhaseDay.query.filter(
                                PhaseDay.phase_id == ph.id,
                                PhaseDay.day_num == d,
                                PhaseDay.workout_id.isnot(None)
                            ).first()
                            if phd and phd.workout:
                                next_program_workout = phd.workout
                                next_program_exercises = [
                                    we.exercise for we in phd.workout.exercises.order_by(WorkoutExercise.order).all()
                                ]
                                next_workout_phase_id = ph.id
                                next_workout_week = week_in_phase
                                break
                        break
                    running += phase_total

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
            next_program_workout=next_program_workout,
            next_program_exercises=next_program_exercises,
            next_workout_phase_id=next_workout_phase_id,
            next_workout_week=next_workout_week,
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

    if current_user.role == 'trainer':
        query = query.filter(Session.trainer_id == current_user.id)
    elif current_user.role == 'client':
        query = query.filter(Session.client_id == current_user.id)

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
            # Only expose notes to staff, not other clients
            _notes = (s.notes or '') if (current_user.role != 'client' or is_mine) else ''
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
                    'notes': _notes,
                    'session_id': s.id,
                    'group_id': s.group_id,
                    'group_name': group_name,
                    'is_mine': is_mine,
                }
            })
        else:
            _trainer = s.trainer.name if s.trainer else 'Trainer'
            _client = s.client.name if s.client else 'Client'
            # Only expose notes to staff, not other clients
            _notes = (s.notes or '') if (current_user.role != 'client' or is_mine) else ''
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
                    'notes': _notes,
                    'session_id': s.id,
                    'group_id': s.group_id,
                    'group_name': None,
                    'is_mine': is_mine,
                }
            })
    return jsonify(events)


@app.route('/api/check-conflict', methods=['POST'])
@staff_required
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

        # For group sessions, update ALL rows for this group slot so every
        # member's progress bar and streak reflect the change
        if sess.group_id:
            siblings = Session.query.filter_by(
                group_id=sess.group_id,
                scheduled_at=sess.scheduled_at,
            ).all()
        else:
            siblings = [sess]

        milestone_msgs = {
            1:  '🔥 Your streak starts now — keep it going!',
            3:  '🔥🔥🔥 3-week streak — you\'re building momentum!',
            5:  '🔥 5-week streak — incredible consistency!',
            10: '⚡ 10-week streak — you are unstoppable!',
            15: '🏅 15-week streak — elite level dedication!',
            20: '🏆 20-week streak — absolute legend!',
        }

        for s in siblings:
            old_status = s.status
            s.status = new_status

            if new_status == 'completed' and old_status != 'completed' and s.client_id:
                date_str = s.scheduled_at.strftime('%b %-d')
                trainer_name = s.trainer.name if s.trainer else 'your trainer'
                create_notification(s.client_id, 'session_completed',
                    f'Session completed {date_str} with {trainer_name}. Great work! 💪',
                    session_id=s.id)
                streak = get_session_streak(s.client_id)
                if streak in milestone_msgs:
                    create_notification(s.client_id, 'streak_milestone',
                        milestone_msgs[streak], session_id=s.id)
                # V2: streak + badge hooks
                update_streak(s.client_id, 'workout')
                check_badges(s.client_id, {'action': 'session_completed', 'hour': datetime.now(timezone.utc).hour})

        db.session.commit()
        flash(f'Session marked as {new_status}.', 'success')
    return redirect(request.referrer or url_for('calendar_view'))


@app.route('/sessions/complete-week', methods=['POST'])
@staff_required
def complete_week():
    """Mark all scheduled sessions in a given Mon-Sun week as completed."""
    data = request.get_json() or {}
    week_start_str = data.get('week_start')  # ISO date string e.g. "2026-03-18"
    trainer_id = data.get('trainer_id')       # optional — filter by trainer

    if not week_start_str:
        return jsonify({'ok': False, 'error': 'week_start required'}), 400

    try:
        week_start = datetime.fromisoformat(week_start_str).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=None
        )
    except ValueError:
        return jsonify({'ok': False, 'error': 'invalid date'}), 400

    week_end = week_start + timedelta(days=7)

    query = Session.query.filter(
        Session.status == 'scheduled',
        Session.scheduled_at >= week_start,
        Session.scheduled_at < week_end,
    )
    if trainer_id:
        query = query.filter(Session.trainer_id == int(trainer_id))

    sessions = query.all()
    count = 0
    affected_clients = set()
    for s in sessions:
        s.status = 'completed'
        if s.client_id:
            affected_clients.add(s.client_id)
            try:
                date_str = s.scheduled_at.strftime('%b %-d')
                create_notification(s.client_id, 'session_completed',
                    f'Session completed {date_str} with {s.trainer.name}. Great work! 💪',
                    session_id=s.id)
            except Exception:
                pass
        count += 1

    # V2: streak + badge hooks for all affected clients
    for cid in affected_clients:
        update_streak(cid, 'workout')
        check_badges(cid, {'action': 'session_completed', 'hour': datetime.now(timezone.utc).hour})

    db.session.commit()
    return jsonify({'ok': True, 'count': count})


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
        ids = [s.id for s in to_delete]
        Notification.query.filter(Notification.session_id.in_(ids)).delete(synchronize_session=False)
        for s in to_delete:
            db.session.delete(s)
        db.session.commit()
        flash(f'{count} session{"s" if count != 1 else ""} deleted.', 'success')
    else:
        Notification.query.filter_by(session_id=sess.id).delete()
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
@limiter.limit("10 per minute")
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
    # Active subscriptions keyed by client_id
    subs = ClientSubscription.query.filter(ClientSubscription.status.in_(['active', 'past_due'])).all()
    sub_by_client = {s.client_id: s for s in subs}
    return render_template('admin_packages.html',
        packages=packages, clients=clients, plans=plans,
        current_month=now.month, current_year=now.year,
        month_name=month_name, sub_by_client=sub_by_client,
        stripe_enabled=stripe_enabled(),
    )


@app.route('/admin/packages/new', methods=['POST'])
@admin_required
def admin_new_package():
    client_ids = [int(x) for x in request.form.getlist('client_ids') if x]
    if not client_ids:
        flash('Select at least one client.', 'danger')
        return redirect(url_for('admin_packages'))

    try:
        month = int(request.form['month'])
        year = int(request.form['year'])
        sessions_purchased = int(request.form['sessions_purchased'])
    except (ValueError, KeyError):
        flash('Invalid form data.', 'danger')
        return redirect(url_for('admin_packages'))
    if not (1 <= month <= 12) or not (2020 <= year <= 2100):
        flash('Invalid month or year.', 'danger')
        return redirect(url_for('admin_packages'))
    if not (1 <= sessions_purchased <= 100):
        flash('Sessions purchased must be between 1 and 100.', 'danger')
        return redirect(url_for('admin_packages'))
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
        audit_logger.error(f'Package delete failed: {e}')
        flash('Could not delete package. Please try again.', 'danger')
    return redirect(url_for('admin_packages'))


# ─── Admin: Finances ──────────────────────────────────────────────────────────

@app.route('/admin/finances')
@admin_required
def admin_finances():
    now = datetime.now(timezone.utc)

    # ── Payout rates ──────────────────────────────────────────────────────────
    # Nick (gym owner): $45 group / $30 individual
    # Other trainers:   $30 group / $20 individual
    nick_user = User.query.filter_by(email='nick@fitforlife.com').first()
    nick_id = nick_user.id if nick_user else None

    NICK_GROUP = 45;  NICK_INDIV = 30
    OTHER_GROUP = 30; OTHER_INDIV = 20

    def calc_payout(sessions):
        """Sum trainer payouts for a list of Session objects.
        Group sessions are de-duplicated per trainer+group+timeslot."""
        seen_group = set()
        total = 0
        for s in sessions:
            if s.group_id:
                key = (s.trainer_id, s.group_id, s.scheduled_at)
                if key in seen_group:
                    continue
                seen_group.add(key)
                total += NICK_GROUP if s.trainer_id == nick_id else OTHER_GROUP
            else:
                total += NICK_INDIV if s.trainer_id == nick_id else OTHER_INDIV
        return total

    # ── Find locations ────────────────────────────────────────────────────────
    west_loc    = Location.query.filter(Location.name.ilike('%west%')).first()
    midtown_loc = Location.query.filter(Location.name.ilike('%midtown%')).first()
    west_id    = west_loc.id    if west_loc    else None
    midtown_id = midtown_loc.id if midtown_loc else None

    # ── Build last 6 months ───────────────────────────────────────────────────
    months_data = []
    for i in range(5, -1, -1):
        m = now.month - i
        y = now.year
        while m <= 0:
            m += 12
            y -= 1

        pkgs = SessionPackage.query.filter_by(month=m, year=y).all()
        revenue_cents = sum(p.plan.price_cents for p in pkgs if p.plan and p.plan.price_cents)
        revenue = revenue_cents / 100

        month_start = datetime(y, m, 1, tzinfo=timezone.utc)
        month_end   = datetime(y + 1, 1, 1, tzinfo=timezone.utc) if m == 12 \
                      else datetime(y, m + 1, 1, tzinfo=timezone.utc)

        all_sess = Session.query.filter(
            Session.scheduled_at >= month_start,
            Session.scheduled_at < month_end,
        ).all()
        completed_sess = [s for s in all_sess if s.status == 'completed']
        missed_count   = sum(1 for s in all_sess if s.status == 'missed')

        total_sessions = len(all_sess)
        completed      = len(completed_sess)

        payout     = calc_payout(completed_sess)
        net_profit = revenue - payout

        # Location split
        west_sess    = [s for s in completed_sess if s.location_id == west_id]
        midtown_sess = [s for s in completed_sess if s.location_id == midtown_id]

        # Allocate revenue proportionally by completed sessions
        frac_west    = len(west_sess)    / completed if completed else 0
        frac_midtown = len(midtown_sess) / completed if completed else 0
        west_revenue    = round(revenue * frac_west,    2)
        midtown_revenue = round(revenue * frac_midtown, 2)
        west_payout    = calc_payout(west_sess)
        midtown_payout = calc_payout(midtown_sess)
        west_net    = round(west_revenue    - west_payout,    2)
        midtown_net = round(midtown_revenue - midtown_payout, 2)

        avg_cost = revenue / total_sessions if total_sessions else 0

        months_data.append({
            'month': m, 'year': y, 'month_name': month_name[m],
            'revenue':        revenue,
            'revenue_cents':  revenue_cents,
            'payout':         payout,
            'net_profit':     net_profit,
            'total_sessions': total_sessions,
            'completed':      completed,
            'missed':         missed_count,
            'avg_cost':       avg_cost,
            'west_revenue':   west_revenue,
            'west_payout':    west_payout,
            'west_net':       west_net,
            'west_sessions':  len(west_sess),
            'midtown_revenue':   midtown_revenue,
            'midtown_payout':    midtown_payout,
            'midtown_net':       midtown_net,
            'midtown_sessions':  len(midtown_sess),
        })

    # ── 6-month totals ────────────────────────────────────────────────────────
    totals = {
        'revenue':    sum(d['revenue']    for d in months_data),
        'payout':     sum(d['payout']     for d in months_data),
        'net_profit': sum(d['net_profit'] for d in months_data),
        'sessions':   sum(d['total_sessions'] for d in months_data),
        'completed':  sum(d['completed']  for d in months_data),
    }

    # ── This week ─────────────────────────────────────────────────────────────
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    week_sessions = Session.query.filter(
        Session.scheduled_at >= week_start,
        Session.scheduled_at < week_start + timedelta(days=7),
    ).count()

    # ── Session log ───────────────────────────────────────────────────────────
    session_log = Session.query.filter(
        Session.status.in_(['completed', 'missed']),
    ).order_by(Session.scheduled_at.desc()).limit(100).all()

    current_month = months_data[-1]
    return render_template(
        'admin_finances.html',
        months_data=months_data,
        totals=totals,
        week_sessions=week_sessions,
        session_log=session_log,
        current_month=current_month,
        month_name=month_name,
        west_name=west_loc.name   if west_loc    else 'West Mobile',
        midtown_name=midtown_loc.name if midtown_loc else 'Midtown',
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
@limiter.limit("30 per minute")
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


MOVEMENT_PATTERNS = ['Push', 'Pull', 'Squat', 'Hinge', 'Lunge', 'Carry', 'Rotation', 'Isolation', 'Cardio', 'Mobility']
EQUIPMENT_OPTIONS = ['Barbell', 'Dumbbell', 'Kettlebell', 'Cable', 'Machine', 'Smith Machine', 'Bodyweight', 'Resistance Band', 'Landmine', 'Rings', 'TRX', 'Cardio Machine']
MUSCLE_GROUPS    = ['Legs', 'Back', 'Chest', 'Shoulders', 'Arms', 'Core', 'Hamstrings', 'Hips', 'Full Body']

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
    is_timed = db.Column(db.Boolean, default=False)    # True for runs, rows, bike sprints (lower time = better)
    is_hold = db.Column(db.Boolean, default=False)     # True for planks, wall sits, dead hangs (longer = better)
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
    group_id = db.Column(db.Integer, nullable=True)
    group_type = db.Column(db.String(20), nullable=True)
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
                              order_by='ProgramPhase.phase_num',
                              cascade='all, delete-orphan'))


class PhaseDay(db.Model):
    """One row per day slot in a phase. The same workout repeats every week of the phase."""
    __tablename__ = 'phase_days'
    id = db.Column(db.Integer, primary_key=True)
    phase_id = db.Column(db.Integer, db.ForeignKey('program_phases.id'), nullable=False)
    day_num = db.Column(db.Integer, nullable=False)      # 1=Mon, 2=Tue, ... 7=Sun
    workout_id = db.Column(db.Integer, db.ForeignKey('workouts.id'), nullable=True)
    label = db.Column(db.String(100))
    __table_args__ = (db.UniqueConstraint('phase_id', 'day_num', name='uq_phase_day'),)
    phase = db.relationship('ProgramPhase', backref=db.backref('days', lazy='dynamic',
                            cascade='all, delete-orphan'))
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
    phase = db.relationship('ProgramPhase', backref=db.backref('overrides', lazy='dynamic',
                            cascade='all, delete-orphan'))
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
    duration_seconds = db.Column(db.Float)        # For holds: plank, wall sit, dead hang
    time_seconds = db.Column(db.Float)            # For timed: mile run, 400m, rowing
    distance = db.Column(db.Float)                # For distance-based activities
    distance_unit = db.Column(db.String(10))      # 'meters', 'miles', 'yards'
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


# ─── Gamification Models ─────────────────────────────────────────────────────

class PersonalRecord(db.Model):
    """Tracks every PR a client hits across all categories."""
    __tablename__ = 'personal_records'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    exercise_id = db.Column(db.Integer, db.ForeignKey('exercises.id'), nullable=True)
    pr_type = db.Column(db.String(30), nullable=False)
    # pr_type values:
    #   Rep maxes: '1rm','3rm','5rm','8rm','10rm'
    #   Volume:    'volume_session' (best single-session), 'volume_exercise_milestone' (cumulative), 'volume_workout' (total workout)
    #   Time:      'longest_hold' (higher=better), 'fastest_time' (lower=better)
    #   Other:     'max_reps' (most unbroken reps)
    value = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(20), nullable=False)  # lbs, seconds, minutes, meters, miles, reps, lbs_volume
    previous_value = db.Column(db.Float, nullable=True)
    muscle_group = db.Column(db.String(80), nullable=True)  # for volume_muscle PRs
    achieved_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    user = db.relationship('User', foreign_keys='PersonalRecord.user_id')
    exercise = db.relationship('Exercise')


class BadgeDefinition(db.Model):
    """Static badge definitions — seeded on init."""
    __tablename__ = 'badge_definitions'
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(60), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(300), nullable=False)
    icon = db.Column(db.String(50), nullable=False)  # bootstrap icon class
    category = db.Column(db.String(40), nullable=False)  # workout, pr, streak, consistency, special
    level = db.Column(db.String(20), default='bronze')  # bronze, silver, gold, platinum
    threshold = db.Column(db.Integer, nullable=True)  # numeric threshold if applicable
    repeatable = db.Column(db.Boolean, default=False)


class UserBadge(db.Model):
    """Badges earned by users."""
    __tablename__ = 'user_badges'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    badge_id = db.Column(db.Integer, db.ForeignKey('badge_definitions.id'), nullable=False)
    earned_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    user = db.relationship('User', foreign_keys='UserBadge.user_id')
    badge = db.relationship('BadgeDefinition')


class Streak(db.Model):
    """Tracks streaks per user per type."""
    __tablename__ = 'streaks'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    streak_type = db.Column(db.String(30), nullable=False)  # workout, logging, weekly_target, checkin
    current_count = db.Column(db.Integer, default=0)
    longest_count = db.Column(db.Integer, default=0)
    last_activity_date = db.Column(db.Date, nullable=True)
    freeze_used_this_week = db.Column(db.Boolean, default=False)
    user = db.relationship('User', foreign_keys='Streak.user_id')
    __table_args__ = (db.UniqueConstraint('user_id', 'streak_type', name='uq_user_streak'),)


class RepMilestone(db.Model):
    """Tracks cumulative rep milestones hit per exercise."""
    __tablename__ = 'rep_milestones'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    exercise_id = db.Column(db.Integer, db.ForeignKey('exercises.id'), nullable=False)
    milestone = db.Column(db.Integer, nullable=False)  # 100, 200, 300, 400, 500, 600
    reached_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    user = db.relationship('User', foreign_keys='RepMilestone.user_id')
    exercise = db.relationship('Exercise')
    __table_args__ = (db.UniqueConstraint('user_id', 'exercise_id', 'milestone', name='uq_rep_milestone'),)


class DailyLog(db.Model):
    """Daily quick-logs: water, sleep, meal, weigh-in."""
    __tablename__ = 'daily_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    log_date = db.Column(db.Date, nullable=False)
    log_type = db.Column(db.String(30), nullable=False)  # water, sleep, meal, weighin
    value = db.Column(db.Float)          # water=oz, sleep=hours, weighin=lbs, meal=null
    quality = db.Column(db.Integer)      # 1-5 for sleep quality
    notes = db.Column(db.String(300))
    photo_url = db.Column(db.String(500))  # for meal photos
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    user = db.relationship('User', foreign_keys=[user_id])
    __table_args__ = (db.UniqueConstraint('user_id', 'log_date', 'log_type', name='uq_daily_log'),)


class ExerciseVolumeTotal(db.Model):
    """Running cumulative volume per user per exercise. Updated incrementally on every log."""
    __tablename__ = 'exercise_volume_totals'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    exercise_id = db.Column(db.Integer, db.ForeignKey('exercises.id'), nullable=False)
    total_volume_lbs = db.Column(db.Float, default=0.0)
    total_reps = db.Column(db.Integer, default=0)
    session_count = db.Column(db.Integer, default=0)
    best_session_volume = db.Column(db.Float, default=0.0)
    last_updated = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    user = db.relationship('User', foreign_keys=[user_id])
    exercise = db.relationship('Exercise')
    __table_args__ = (db.UniqueConstraint('user_id', 'exercise_id', name='uq_user_exercise_volume'),)


class WorkoutVolumeRecord(db.Model):
    """Tracks best total volume for a named workout (all exercises combined)."""
    __tablename__ = 'workout_volume_records'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    workout_id = db.Column(db.Integer, db.ForeignKey('workouts.id'), nullable=False)
    best_volume_lbs = db.Column(db.Float, default=0.0)
    achieved_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    user = db.relationship('User', foreign_keys=[user_id])
    workout = db.relationship('Workout')
    __table_args__ = (db.UniqueConstraint('user_id', 'workout_id', name='uq_user_workout_volume'),)


# ─── Badge Definitions Seed Data ─────────────────────────────────────────────

BADGE_SEED = [
    # ── Workout Count ────────────────────────────────────────────────────────
    {'key': 'first_timer',      'name': 'First Timer',      'description': 'Log your first workout',              'icon': 'bi-lightning-charge-fill', 'category': 'workout',     'level': 'bronze',   'threshold': 1},
    {'key': 'ten_club',         'name': '10 Club',           'description': 'Complete 10 workouts',                'icon': 'bi-fire',                 'category': 'workout',     'level': 'bronze',   'threshold': 10},
    {'key': 'quarter_century',  'name': 'Quarter Century',   'description': 'Complete 25 workouts',                'icon': 'bi-fire',                 'category': 'workout',     'level': 'silver',   'threshold': 25},
    {'key': 'half_century',     'name': 'Half Century',      'description': 'Complete 50 workouts',                'icon': 'bi-fire',                 'category': 'workout',     'level': 'gold',     'threshold': 50},
    {'key': 'century_club',     'name': 'Century Club',      'description': 'Complete 100 workouts',               'icon': 'bi-trophy-fill',          'category': 'workout',     'level': 'platinum', 'threshold': 100},
    # ── Weekly Frequency ─────────────────────────────────────────────────────
    {'key': 'hat_trick',        'name': 'Hat Trick',         'description': '3 workouts in one week',              'icon': 'bi-3-circle-fill',        'category': 'consistency', 'level': 'bronze',   'threshold': 3,  'repeatable': True},
    {'key': 'iron_week',        'name': 'Iron Week',         'description': '5 workouts in one week',              'icon': 'bi-5-circle-fill',        'category': 'consistency', 'level': 'silver',   'threshold': 5,  'repeatable': True},
    # ── PR Collection ────────────────────────────────────────────────────────
    {'key': 'pr_collector_10',  'name': 'PR Collector',      'description': 'Hit 10 lifetime PRs',                 'icon': 'bi-award-fill',           'category': 'pr',          'level': 'bronze',   'threshold': 10},
    {'key': 'pr_collector_25',  'name': 'PR Hunter',         'description': 'Hit 25 lifetime PRs',                 'icon': 'bi-award-fill',           'category': 'pr',          'level': 'silver',   'threshold': 25},
    {'key': 'pr_collector_50',  'name': 'PR Machine',        'description': 'Hit 50 lifetime PRs',                 'icon': 'bi-award-fill',           'category': 'pr',          'level': 'gold',     'threshold': 50},
    {'key': 'pr_collector_100', 'name': 'PR Legend',          'description': 'Hit 100 lifetime PRs',               'icon': 'bi-award-fill',           'category': 'pr',          'level': 'platinum', 'threshold': 100},
    {'key': 'double_up',        'name': 'Double Up',         'description': 'Beat 2 PRs in the same week',         'icon': 'bi-chevron-double-up',    'category': 'pr',          'level': 'silver',   'threshold': 2,  'repeatable': True},
    {'key': 'triple_up',        'name': 'Triple Up',         'description': 'Beat 3 PRs in the same week',         'icon': 'bi-chevron-double-up',    'category': 'pr',          'level': 'gold',     'threshold': 3,  'repeatable': True},
    # ── Time of Day ──────────────────────────────────────────────────────────
    {'key': 'early_bird',       'name': 'Early Bird',        'description': 'Log a workout before 7 AM',           'icon': 'bi-sunrise-fill',         'category': 'special',     'level': 'bronze',   'repeatable': True},
    {'key': 'night_owl',        'name': 'Night Owl',         'description': 'Log a workout after 8 PM',            'icon': 'bi-moon-stars-fill',      'category': 'special',     'level': 'bronze',   'repeatable': True},
    # ── Variety ──────────────────────────────────────────────────────────────
    {'key': 'muscle_map',       'name': 'Muscle Map',        'description': 'Hit every muscle group in one week',  'icon': 'bi-body-text',            'category': 'consistency', 'level': 'gold',     'repeatable': True},
    {'key': 'variety_pack',     'name': 'Variety Pack',      'description': 'Log 8 different exercises in a month','icon': 'bi-grid-3x3-gap-fill',    'category': 'consistency', 'level': 'silver',   'threshold': 8},
    # ── Global Volume Milestones ─────────────────────────────────────────────
    {'key': 'volume_10k',       'name': 'Volume Rising',     'description': 'Lift 10,000 lbs total volume',        'icon': 'bi-bar-chart-fill',       'category': 'volume',      'level': 'bronze',   'threshold': 10000},
    {'key': 'volume_25k',       'name': 'Volume Builder',    'description': 'Lift 25,000 lbs total volume',        'icon': 'bi-bar-chart-fill',       'category': 'volume',      'level': 'silver',   'threshold': 25000},
    {'key': 'volume_50k',       'name': 'Volume King',       'description': 'Lift 50,000 lbs total volume',        'icon': 'bi-bar-chart-fill',       'category': 'volume',      'level': 'gold',     'threshold': 50000},
    {'key': 'volume_100k',      'name': 'Volume Legend',     'description': 'Lift 100,000 lbs total volume',       'icon': 'bi-bar-chart-fill',       'category': 'volume',      'level': 'platinum', 'threshold': 100000},
    # ── Per-Exercise Volume Milestones ───────────────────────────────────────
    {'key': 'volume_ex_1k',     'name': 'Mover',             'description': '1,000 lbs on a single exercise',      'icon': 'bi-graph-up-arrow',       'category': 'volume',      'level': 'bronze',   'threshold': 1000,   'repeatable': True},
    {'key': 'volume_ex_5k',     'name': 'Grinder',           'description': '5,000 lbs on a single exercise',      'icon': 'bi-graph-up-arrow',       'category': 'volume',      'level': 'bronze',   'threshold': 5000,   'repeatable': True},
    {'key': 'volume_ex_10k',    'name': 'Workhorse',         'description': '10,000 lbs on a single exercise',     'icon': 'bi-graph-up-arrow',       'category': 'volume',      'level': 'silver',   'threshold': 10000,  'repeatable': True},
    {'key': 'volume_ex_25k',    'name': 'Volume Addict',     'description': '25,000 lbs on a single exercise',     'icon': 'bi-graph-up-arrow',       'category': 'volume',      'level': 'silver',   'threshold': 25000,  'repeatable': True},
    {'key': 'volume_ex_50k',    'name': 'Iron Mover',        'description': '50,000 lbs on a single exercise',     'icon': 'bi-graph-up-arrow',       'category': 'volume',      'level': 'gold',     'threshold': 50000,  'repeatable': True},
    {'key': 'volume_ex_100k',   'name': 'Volume Monster',    'description': '100,000 lbs on a single exercise',    'icon': 'bi-graph-up-arrow',       'category': 'volume',      'level': 'platinum', 'threshold': 100000, 'repeatable': True},
    # ── Time Domain — Holds ──────────────────────────────────────────────────
    {'key': 'plank_1min',       'name': 'Core Starter',      'description': 'Hold a 1-minute plank',               'icon': 'bi-stopwatch-fill',       'category': 'special',     'level': 'bronze',   'threshold': 60},
    {'key': 'plank_2min',       'name': 'Core Warrior',      'description': 'Hold a 2-minute plank',               'icon': 'bi-stopwatch-fill',       'category': 'special',     'level': 'silver',   'threshold': 120},
    {'key': 'the_wall',         'name': 'The Wall',          'description': 'Hold a 3-minute plank',               'icon': 'bi-bricks',               'category': 'special',     'level': 'gold',     'threshold': 180},
    {'key': 'plank_5min',       'name': 'Plank Legend',      'description': 'Hold a 5-minute plank',               'icon': 'bi-bricks',               'category': 'special',     'level': 'platinum', 'threshold': 300},
    {'key': 'dead_hang_30s',    'name': 'Hanging On',        'description': '30-second dead hang',                 'icon': 'bi-grip-horizontal',      'category': 'special',     'level': 'bronze',   'threshold': 30},
    {'key': 'dead_hang_1min',   'name': 'Iron Grip',         'description': '1-minute dead hang',                  'icon': 'bi-grip-horizontal',      'category': 'special',     'level': 'silver',   'threshold': 60},
    {'key': 'dead_hang_2min',   'name': 'Gorilla Grip',      'description': '2-minute dead hang',                  'icon': 'bi-grip-horizontal',      'category': 'special',     'level': 'gold',     'threshold': 120},
    # ── Time Domain — Speed ──────────────────────────────────────────────────
    {'key': 'mile_sub_10',      'name': 'Runner',            'description': 'Sub-10 minute mile',                  'icon': 'bi-speedometer2',         'category': 'special',     'level': 'bronze',   'threshold': 600},
    {'key': 'mile_sub_8',       'name': 'Speed Demon',       'description': 'Sub-8 minute mile',                   'icon': 'bi-speedometer2',         'category': 'special',     'level': 'silver',   'threshold': 480},
    {'key': 'mile_sub_7',       'name': 'Road Warrior',      'description': 'Sub-7 minute mile',                   'icon': 'bi-speedometer2',         'category': 'special',     'level': 'gold',     'threshold': 420},
    {'key': 'mile_sub_6',       'name': 'Lightning',         'description': 'Sub-6 minute mile',                   'icon': 'bi-speedometer2',         'category': 'special',     'level': 'platinum', 'threshold': 360},
    # ── Exercise Loyalty ─────────────────────────────────────────────────────
    {'key': 'exercise_10x',     'name': 'Getting Started',   'description': 'Do an exercise 10 times',             'icon': 'bi-repeat',               'category': 'consistency', 'level': 'bronze',   'threshold': 10,  'repeatable': True},
    {'key': 'exercise_25x',     'name': 'Regular',           'description': 'Do an exercise 25 times',             'icon': 'bi-repeat',               'category': 'consistency', 'level': 'bronze',   'threshold': 25,  'repeatable': True},
    {'key': 'exercise_50x',     'name': 'Dedicated',         'description': 'Do an exercise 50 times',             'icon': 'bi-repeat',               'category': 'consistency', 'level': 'silver',   'threshold': 50,  'repeatable': True},
    {'key': 'exercise_100x',    'name': 'Specialist',        'description': 'Do an exercise 100 times',            'icon': 'bi-repeat',               'category': 'consistency', 'level': 'gold',     'threshold': 100, 'repeatable': True},
    {'key': 'exercise_200x',    'name': 'Master',            'description': 'Do an exercise 200 times',            'icon': 'bi-repeat',               'category': 'consistency', 'level': 'platinum', 'threshold': 200, 'repeatable': True},
    # ── Rep Milestones (per exercise) ────────────────────────────────────────
    {'key': 'reps_100',         'name': 'Rep Starter',       'description': '100 total reps of an exercise',       'icon': 'bi-123',                  'category': 'volume',      'level': 'bronze',   'threshold': 100,   'repeatable': True},
    {'key': 'reps_500',         'name': '500 Club',          'description': '500 total reps of an exercise',       'icon': 'bi-123',                  'category': 'volume',      'level': 'silver',   'threshold': 500,   'repeatable': True},
    {'key': 'reps_1000',        'name': 'Thousand Repper',   'description': '1,000 total reps of an exercise',     'icon': 'bi-123',                  'category': 'volume',      'level': 'gold',     'threshold': 1000,  'repeatable': True},
    {'key': 'reps_5000',        'name': '5K Repper',         'description': '5,000 total reps of an exercise',     'icon': 'bi-123',                  'category': 'volume',      'level': 'platinum', 'threshold': 5000,  'repeatable': True},
    {'key': 'reps_10000',       'name': 'Ten Thousand',      'description': '10,000 total reps of an exercise',    'icon': 'bi-123',                  'category': 'volume',      'level': 'platinum', 'threshold': 10000, 'repeatable': True},
    # ── Daily Logging ────────────────────────────────────────────────────────
    {'key': 'hydro_7',          'name': 'Hydro Homie',       'description': 'Log water 7 days in a row',           'icon': 'bi-droplet-fill',         'category': 'streak',      'level': 'bronze',   'threshold': 7},
    {'key': 'hydro_30',         'name': 'Water Warrior',     'description': 'Log water 30 days in a row',          'icon': 'bi-droplet-fill',         'category': 'streak',      'level': 'silver',   'threshold': 30},
    {'key': 'sleep_7',          'name': 'Rest Up',           'description': 'Log sleep 7 days in a row',           'icon': 'bi-moon-fill',            'category': 'streak',      'level': 'bronze',   'threshold': 7},
    {'key': 'sleep_30',         'name': 'Sleep Scholar',     'description': 'Log sleep 30 days in a row',          'icon': 'bi-moon-fill',            'category': 'streak',      'level': 'silver',   'threshold': 30},
    {'key': 'meal_7',           'name': 'Fuel Up',           'description': 'Log meals 7 days in a row',           'icon': 'bi-egg-fried',            'category': 'streak',      'level': 'bronze',   'threshold': 7},
    {'key': 'meal_30',          'name': 'Nutrition Pro',     'description': 'Log meals 30 days in a row',          'icon': 'bi-egg-fried',            'category': 'streak',      'level': 'silver',   'threshold': 30},
    {'key': 'weighin_7',        'name': 'Scale Starter',     'description': 'Weigh in 7 days in a row',            'icon': 'bi-speedometer',          'category': 'streak',      'level': 'bronze',   'threshold': 7},
    {'key': 'weighin_30',       'name': 'Weight Watcher',    'description': 'Weigh in 30 days in a row',           'icon': 'bi-speedometer',          'category': 'streak',      'level': 'silver',   'threshold': 30},
    {'key': 'daily_all',        'name': 'Full Logger',       'description': 'Log water + sleep + meal + weigh-in in one day', 'icon': 'bi-check2-all', 'category': 'consistency', 'level': 'gold', 'repeatable': True},
    {'key': 'daily_all_7',      'name': 'Data Machine',      'description': 'Full log every category 7 days straight', 'icon': 'bi-database-fill-check', 'category': 'streak', 'level': 'platinum', 'threshold': 7},
    # ── Streak Badges ────────────────────────────────────────────────────────
    {'key': 'streak_3',         'name': 'Getting Going',     'description': '3-day streak',                        'icon': 'bi-fire',                 'category': 'streak',      'level': 'bronze',   'threshold': 3},
    {'key': 'streak_7',         'name': 'Week Warrior',      'description': '7-day streak',                        'icon': 'bi-fire',                 'category': 'streak',      'level': 'bronze',   'threshold': 7},
    {'key': 'streak_14',        'name': 'Two Weeks Strong',  'description': '14-day streak',                       'icon': 'bi-fire',                 'category': 'streak',      'level': 'silver',   'threshold': 14},
    {'key': 'streak_30',        'name': 'Month of Iron',     'description': '30-day streak',                       'icon': 'bi-fire',                 'category': 'streak',      'level': 'silver',   'threshold': 30},
    {'key': 'streak_60',        'name': 'Two Month Titan',   'description': '60-day streak',                       'icon': 'bi-fire',                 'category': 'streak',      'level': 'gold',     'threshold': 60},
    {'key': 'streak_90',        'name': 'Quarter Beast',     'description': '90-day streak',                       'icon': 'bi-fire',                 'category': 'streak',      'level': 'gold',     'threshold': 90},
    {'key': 'streak_180',       'name': 'Half Year Hero',    'description': '180-day streak',                      'icon': 'bi-fire',                 'category': 'streak',      'level': 'platinum', 'threshold': 180},
    {'key': 'streak_365',       'name': 'Year of Iron',      'description': '365-day streak',                      'icon': 'bi-fire',                 'category': 'streak',      'level': 'platinum', 'threshold': 365},
    {'key': 'consistent',       'name': 'Consistent',        'description': 'Hit weekly goal 8 weeks in a row',    'icon': 'bi-check2-all',           'category': 'streak',      'level': 'gold',     'threshold': 8},
    {'key': 'unbreakable',      'name': 'Unbreakable',       'description': '30-day logging streak',               'icon': 'bi-shield-fill-check',    'category': 'streak',      'level': 'platinum', 'threshold': 30},
    # ── Special ──────────────────────────────────────────────────────────────
    {'key': 'comeback_kid',     'name': 'Comeback Kid',      'description': 'Return after 7+ days off',            'icon': 'bi-arrow-counterclockwise','category': 'special',   'level': 'silver'},
]


# ─── V2 Engagement Engine ────────────────────────────────────────────────────

# Notification emoji map
NOTIF_EMOJI = {
    'session_completed': '💪', 'streak_milestone': '🔥', 'pr_rep_max': '🏆',
    'pr_volume_exercise': '📊', 'pr_volume_workout': '💥', 'pr_volume_milestone': '📈',
    'pr_longest_hold': '⏱️', 'pr_fastest_time': '⚡', 'pr_max_reps': '💯',
    'badge_earned': '🏅', 'streak_started': '🔥', 'streak_broken': '💔',
    'comeback_nudge': '👋', 'workout_completed': '✅', 'exercise_first': '🆕',
    'exercise_count_milestone': '🎯', 'exercise_rep_milestone': '🔢',
    'weekly_target_hit': '🎉', 'consistency_badge': '⭐',
    'daily_log_water': '💧', 'daily_log_sleep': '😴', 'daily_log_meal': '🍽️',
    'daily_log_weighin': '⚖️', 'early_bird': '🌅', 'night_owl': '🌙',
    'week_started': '📅', 'month_summary': '📋',
}

RARITY_MAP = {'bronze': 'common', 'silver': 'rare', 'gold': 'epic', 'platinum': 'legendary'}

# Volume milestones (per exercise, cumulative)
VOLUME_MILESTONES = [1_000, 5_000, 10_000, 25_000, 50_000, 100_000, 250_000, 500_000, 1_000_000]
REP_MILESTONES = [100, 500, 1_000, 5_000, 10_000]
EXERCISE_COUNT_MILESTONES = [1, 10, 25, 50, 100, 150, 200]
STREAK_MILESTONES = [3, 7, 14, 21, 30, 45, 60, 90, 120, 150, 180, 270, 365]


def get_notif_emoji(notif_type):
    return NOTIF_EMOJI.get(notif_type, 'ℹ️')


def create_notification(user_id, notif_type, message, title=None, icon=None, level=None, session_id=None):
    """Central notification creation — ALL notifications flow through here."""
    notif = Notification(
        user_id=user_id, title=title, message=message,
        notif_type=notif_type, icon=icon, level=level, session_id=session_id,
    )
    db.session.add(notif)
    return notif


def calculate_set_volume(sets, reps_str, weight):
    """Calculate volume from sets × reps × weight. Handles comma-separated reps ('8,8,6')."""
    if not weight or not reps_str:
        return 0.0, 0
    reps_list = [int(r.strip()) for r in str(reps_str).split(',') if r.strip().isdigit()]
    if not reps_list:
        return 0.0, 0
    if len(reps_list) == 1 and sets:
        total_reps = reps_list[0] * sets
    else:
        total_reps = sum(reps_list)
    return weight * total_reps, total_reps


def format_duration(seconds):
    """Format seconds into readable time (e.g., '2:45')."""
    if seconds is None:
        return '0:00'
    m, s = divmod(int(seconds), 60)
    return f'{m}:{s:02d}'


def format_number(n):
    """Format large numbers: 10000 → '10,000'."""
    return f'{n:,.0f}'


def get_best_active_streak(user_id):
    """Return the longest active streak across all types for a user."""
    streaks = Streak.query.filter_by(user_id=user_id).all()
    if not streaks:
        return 0
    return max((s.current_count for s in streaks), default=0)


def get_today_log_status(user_id):
    """Return dict of which daily log types were done today."""
    from datetime import date as _date
    today = _date.today()
    logs = DailyLog.query.filter_by(user_id=user_id, log_date=today).all()
    done = {log.log_type for log in logs}
    return {'water': 'water' in done, 'sleep': 'sleep' in done,
            'meal': 'meal' in done, 'weighin': 'weighin' in done}


def update_streak(user_id, streak_type):
    """Update a streak, handling freeze logic and milestones."""
    from datetime import date as _date
    today = _date.today()
    streak = Streak.query.filter_by(user_id=user_id, streak_type=streak_type).first()
    if not streak:
        streak = Streak(user_id=user_id, streak_type=streak_type, current_count=0, longest_count=0)
        db.session.add(streak)
        db.session.flush()

    if streak.last_activity_date == today:
        return streak  # Already logged today

    # Reset freeze on Monday
    if today.weekday() == 0 and streak.last_activity_date and streak.last_activity_date < today:
        streak.freeze_used_this_week = False

    if streak.last_activity_date == today - timedelta(days=1):
        # Consecutive day
        streak.current_count += 1
    elif (streak.last_activity_date == today - timedelta(days=2)
          and not streak.freeze_used_this_week
          and streak.current_count > 0):
        # Missed 1 day — use freeze
        streak.current_count += 1
        streak.freeze_used_this_week = True
    else:
        # Streak broken or brand new
        if streak.current_count > 0 and streak.last_activity_date:
            days_off = (today - streak.last_activity_date).days
            if days_off >= 2:
                create_notification(user_id, 'streak_broken',
                    f'💔 Your {streak.current_count}-day {streak_type} streak ended. But you can start a new one today!')
        streak.current_count = 1

    streak.last_activity_date = today
    if streak.current_count > streak.longest_count:
        streak.longest_count = streak.current_count

    # Check milestones
    if streak.current_count in STREAK_MILESTONES:
        tier = ('just getting started!' if streak.current_count <= 7
                else "on fire!" if streak.current_count <= 30
                else 'absolutely unstoppable!' if streak.current_count <= 90
                else 'LEGENDARY.')
        create_notification(user_id, 'streak_milestone',
            f"🔥 {streak.current_count}-day {streak_type} streak! You're {tier}")

    return streak


def check_badges(user_id, context):
    """Check badge thresholds after an action. Returns list of newly awarded badge names."""
    awarded = []
    action = context.get('action', '')

    # ── Workout count badges ──
    if action in ('workout_logged', 'session_completed'):
        total_workouts = WorkoutCheckin.query.filter_by(client_id=user_id).count()
        for badge_key, threshold in [('first_timer', 1), ('ten_club', 10), ('quarter_century', 25),
                                      ('half_century', 50), ('century_club', 100)]:
            if total_workouts >= threshold:
                badge_def = BadgeDefinition.query.filter_by(key=badge_key).first()
                if badge_def and not UserBadge.query.filter_by(user_id=user_id, badge_id=badge_def.id).first():
                    db.session.add(UserBadge(user_id=user_id, badge_id=badge_def.id))
                    create_notification(user_id, 'badge_earned',
                        f'🏅 Badge unlocked: {badge_def.name} — {badge_def.description}')
                    awarded.append(badge_def.name)

    # ── PR collection badges ──
    if context.get('new_pr_count', 0) > 0:
        total_prs = PersonalRecord.query.filter_by(user_id=user_id).count()
        for badge_key, threshold in [('pr_collector_10', 10), ('pr_collector_25', 25),
                                      ('pr_collector_50', 50), ('pr_collector_100', 100)]:
            if total_prs >= threshold:
                badge_def = BadgeDefinition.query.filter_by(key=badge_key).first()
                if badge_def and not UserBadge.query.filter_by(user_id=user_id, badge_id=badge_def.id).first():
                    db.session.add(UserBadge(user_id=user_id, badge_id=badge_def.id))
                    create_notification(user_id, 'badge_earned',
                        f'🏅 Badge unlocked: {badge_def.name} — {badge_def.description}')
                    awarded.append(badge_def.name)

        # Weekly PR badges (double_up, triple_up)
        week_start = datetime.now(timezone.utc).date() - timedelta(days=datetime.now(timezone.utc).weekday())
        week_prs = PersonalRecord.query.filter(
            PersonalRecord.user_id == user_id,
            PersonalRecord.achieved_at >= datetime(week_start.year, week_start.month, week_start.day, tzinfo=timezone.utc),
        ).count()
        for badge_key, threshold in [('double_up', 2), ('triple_up', 3)]:
            if week_prs >= threshold:
                badge_def = BadgeDefinition.query.filter_by(key=badge_key).first()
                if badge_def:
                    db.session.add(UserBadge(user_id=user_id, badge_id=badge_def.id))
                    create_notification(user_id, 'badge_earned',
                        f'🏅 Badge unlocked: {badge_def.name} — {badge_def.description}')
                    awarded.append(badge_def.name)

    # ── Volume badges (global) ──
    if action in ('workout_logged',):
        total_volume = db.session.query(db.func.coalesce(db.func.sum(ExerciseVolumeTotal.total_volume_lbs), 0)).filter_by(user_id=user_id).scalar()
        for badge_key, threshold in [('volume_10k', 10000), ('volume_25k', 25000),
                                      ('volume_50k', 50000), ('volume_100k', 100000)]:
            if total_volume >= threshold:
                badge_def = BadgeDefinition.query.filter_by(key=badge_key).first()
                if badge_def and not UserBadge.query.filter_by(user_id=user_id, badge_id=badge_def.id).first():
                    db.session.add(UserBadge(user_id=user_id, badge_id=badge_def.id))
                    create_notification(user_id, 'badge_earned',
                        f'🏅 Badge unlocked: {badge_def.name} — {badge_def.description}')
                    awarded.append(badge_def.name)

    # ── Streak badges ──
    if action in ('workout_logged', 'daily_log', 'session_completed'):
        for stype in ['workout', 'logging']:
            s = Streak.query.filter_by(user_id=user_id, streak_type=stype).first()
            if s:
                for badge_key, threshold in [('streak_3', 3), ('streak_7', 7), ('streak_14', 14),
                                              ('streak_30', 30), ('streak_60', 60), ('streak_90', 90),
                                              ('streak_180', 180), ('streak_365', 365)]:
                    if s.current_count >= threshold:
                        badge_def = BadgeDefinition.query.filter_by(key=badge_key).first()
                        if badge_def and not UserBadge.query.filter_by(user_id=user_id, badge_id=badge_def.id).first():
                            db.session.add(UserBadge(user_id=user_id, badge_id=badge_def.id))
                            create_notification(user_id, 'badge_earned',
                                f'🏅 Badge unlocked: {badge_def.name} — {badge_def.description}')
                            awarded.append(badge_def.name)

    # ── Time-of-day badges ──
    if context.get('hour') is not None:
        hour = context['hour']
        if hour < 7:
            badge_def = BadgeDefinition.query.filter_by(key='early_bird').first()
            if badge_def:
                db.session.add(UserBadge(user_id=user_id, badge_id=badge_def.id))
                create_notification(user_id, 'early_bird', '🌅 Workout before 7 AM — Early Bird energy!')
                awarded.append('Early Bird')
        elif hour >= 20:
            badge_def = BadgeDefinition.query.filter_by(key='night_owl').first()
            if badge_def:
                db.session.add(UserBadge(user_id=user_id, badge_id=badge_def.id))
                create_notification(user_id, 'night_owl', '🌙 Late night grind — Night Owl mode activated!')
                awarded.append('Night Owl')

    return awarded


def process_daily_log(user_id, log_type, value=None):
    """Handle notifications and streaks after a daily log entry."""
    msg_map = {
        'water':  f'💧 {value:.0f} oz logged. Hydration on point.' if value else '💧 Water logged.',
        'sleep':  f'😴 {value:.1f} hours logged. Rest is where gains happen.' if value else '😴 Sleep logged.',
        'meal':   '🍽️ Meal logged. Fueling the machine.',
        'weighin': f'⚖️ Weigh-in recorded. Consistency builds the picture.',
    }
    create_notification(user_id, f'daily_log_{log_type}', msg_map.get(log_type, 'Logged!'))
    update_streak(user_id, 'logging')

    # Check if all 4 logged today
    status = get_today_log_status(user_id)
    if all(status.values()):
        badge_def = BadgeDefinition.query.filter_by(key='daily_all').first()
        if badge_def:
            db.session.add(UserBadge(user_id=user_id, badge_id=badge_def.id))
            create_notification(user_id, 'badge_earned',
                f'🏅 Badge unlocked: {badge_def.name} — {badge_def.description}')


def check_comeback(user_id):
    """Check if user is returning after days away and award comeback_kid if 7+."""
    from datetime import date as _date
    today = _date.today()
    # Find most recent activity across all streaks
    last = db.session.query(db.func.max(Streak.last_activity_date)).filter_by(user_id=user_id).scalar()
    if not last:
        # Check last workout checkin
        last_checkin = WorkoutCheckin.query.filter_by(client_id=user_id).order_by(WorkoutCheckin.completed_at.desc()).first()
        if last_checkin:
            last = last_checkin.completed_at.date()
    if last:
        days_off = (today - last).days
        if days_off >= 7:
            badge_def = BadgeDefinition.query.filter_by(key='comeback_kid').first()
            if badge_def and not UserBadge.query.filter_by(user_id=user_id, badge_id=badge_def.id).first():
                db.session.add(UserBadge(user_id=user_id, badge_id=badge_def.id))
                create_notification(user_id, 'badge_earned',
                    f'🏅 Badge unlocked: {badge_def.name} — {badge_def.description}')
            create_notification(user_id, 'comeback_nudge',
                '👋 Welcome back! Let\'s restart together.')
        elif days_off >= 2:
            create_notification(user_id, 'comeback_nudge',
                f'👋 {days_off} days off — session tomorrow?')


def sort_achievements(badge_progress_list):
    """Sort badges: closest to completion first, then unlocked (newest first), then not started."""
    def _key(item):
        if item['unlocked']:
            ts = item.get('earned_at')
            return (0, -(ts.timestamp() if ts else 0))
        elif item['progress'] > 0:
            pct = item['progress'] / item['total'] if item['total'] else 0
            return (1, -pct)
        else:
            return (2, 0)
    return sorted(badge_progress_list, key=_key)


def process_workout_log(user_id, workout_id, exercise_data_list, log_date):
    """
    THE BRAIN — called after a workout form is submitted.
    exercise_data_list = [
        {'exercise_id': int, 'sets': int, 'reps': str, 'weight_lbs': float,
         'duration_seconds': float|None, 'time_seconds': float|None},
        ...
    ]
    Runs ALL PR detection, volume tracking, badge checks, streak updates, and notifications.
    """
    now = datetime.now(timezone.utc)
    new_pr_count = 0
    total_workout_volume = 0.0

    for entry in exercise_data_list:
        ex_id = entry['exercise_id']
        exercise = Exercise.query.get(ex_id)
        if not exercise:
            continue
        ex_name = exercise.name
        sets = entry.get('sets') or 0
        reps_str = entry.get('reps') or ''
        weight = entry.get('weight_lbs') or 0.0
        dur = entry.get('duration_seconds')
        timed = entry.get('time_seconds')

        # ── 1. Rep Max PR Detection (1RM, 3RM, 5RM, 8RM, 10RM) ──
        if weight > 0 and reps_str:
            reps_list = [int(r.strip()) for r in str(reps_str).split(',') if r.strip().isdigit()]
            for rep_count in reps_list:
                for pr_label, max_reps in [('1rm', 1), ('3rm', 3), ('5rm', 5), ('8rm', 8), ('10rm', 10)]:
                    if rep_count <= max_reps:
                        prev = PersonalRecord.query.filter_by(
                            user_id=user_id, exercise_id=ex_id, pr_type=pr_label
                        ).order_by(PersonalRecord.value.desc()).first()
                        if not prev or weight > prev.value:
                            pr = PersonalRecord(
                                user_id=user_id, exercise_id=ex_id, pr_type=pr_label,
                                value=weight, unit='lbs',
                                previous_value=prev.value if prev else None,
                            )
                            db.session.add(pr)
                            new_pr_count += 1
                            diff_msg = f' Beat your old record by {weight - prev.value:.0f} lbs!' if prev else ' First recorded!'
                            create_notification(user_id, 'pr_rep_max',
                                f'🏆 NEW {pr_label.upper()} on {ex_name} — {weight:.0f} lbs!{diff_msg}')

        # ── 2–4. Volume tracking per exercise ──
        session_vol, session_reps = calculate_set_volume(sets, reps_str, weight)
        total_workout_volume += session_vol

        if session_vol > 0 or session_reps > 0:
            vol_total = ExerciseVolumeTotal.query.filter_by(user_id=user_id, exercise_id=ex_id).first()
            if not vol_total:
                vol_total = ExerciseVolumeTotal(user_id=user_id, exercise_id=ex_id)
                db.session.add(vol_total)
                db.session.flush()

            old_volume = vol_total.total_volume_lbs
            old_reps = vol_total.total_reps
            vol_total.total_volume_lbs += session_vol
            vol_total.total_reps += session_reps
            vol_total.session_count += 1
            vol_total.last_updated = now

            # Best session volume per exercise
            if session_vol > vol_total.best_session_volume:
                vol_total.best_session_volume = session_vol
                pr = PersonalRecord(
                    user_id=user_id, exercise_id=ex_id, pr_type='volume_session',
                    value=session_vol, unit='lbs_volume',
                )
                db.session.add(pr)
                new_pr_count += 1
                create_notification(user_id, 'pr_volume_exercise',
                    f'📊 New volume record on {ex_name} — {format_number(session_vol)} lbs in one session!')

            # Cumulative volume milestones
            for milestone in VOLUME_MILESTONES:
                if old_volume < milestone <= vol_total.total_volume_lbs:
                    pr = PersonalRecord(
                        user_id=user_id, exercise_id=ex_id, pr_type='volume_exercise_milestone',
                        value=milestone, unit='lbs_volume',
                    )
                    db.session.add(pr)
                    new_pr_count += 1
                    qualifier = 'Beast mode.' if milestone >= 50000 else 'Keep grinding!'
                    create_notification(user_id, 'pr_volume_milestone',
                        f'📈 {format_number(milestone)} lbs moved on {ex_name}. {qualifier}')

            # Rep milestones
            for milestone in REP_MILESTONES:
                if old_reps < milestone <= vol_total.total_reps:
                    existing = RepMilestone.query.filter_by(
                        user_id=user_id, exercise_id=ex_id, milestone=milestone).first()
                    if not existing:
                        db.session.add(RepMilestone(user_id=user_id, exercise_id=ex_id, milestone=milestone))
                        qualifier = ('Half a thousand!' if milestone == 500
                                     else 'Incredible.' if milestone >= 5000 else 'Keep stacking!')
                        create_notification(user_id, 'exercise_rep_milestone',
                            f'🔢 {format_number(milestone)} total reps of {ex_name}. {qualifier}')

            # Exercise count milestones
            count = vol_total.session_count
            if count in EXERCISE_COUNT_MILESTONES:
                if count == 1:
                    create_notification(user_id, 'exercise_first',
                        f'🆕 First time doing {ex_name}. New movement unlocked!')
                else:
                    create_notification(user_id, 'exercise_count_milestone',
                        f"🎯 {count}{'th' if count > 3 else ['st','nd','rd'][count-1]} time doing {ex_name}. "
                        + ('That\'s commitment.' if count >= 50 else 'Building the habit!'))

        # ── 6. Longest hold PR ──
        if exercise.is_hold and dur and dur > 0:
            prev = PersonalRecord.query.filter_by(
                user_id=user_id, exercise_id=ex_id, pr_type='longest_hold'
            ).order_by(PersonalRecord.value.desc()).first()
            if not prev or dur > prev.value:
                db.session.add(PersonalRecord(
                    user_id=user_id, exercise_id=ex_id, pr_type='longest_hold',
                    value=dur, unit='seconds', previous_value=prev.value if prev else None,
                ))
                new_pr_count += 1
                diff_msg = f" That's {dur - prev.value:.0f} seconds longer than your best!" if prev else ''
                create_notification(user_id, 'pr_longest_hold',
                    f'⏱️ New longest {ex_name} — {format_duration(dur)}!{diff_msg}')

        # ── 7. Fastest time PR ──
        if exercise.is_timed and timed and timed > 0:
            prev = PersonalRecord.query.filter_by(
                user_id=user_id, exercise_id=ex_id, pr_type='fastest_time'
            ).order_by(PersonalRecord.value.asc()).first()
            if not prev or timed < prev.value:
                db.session.add(PersonalRecord(
                    user_id=user_id, exercise_id=ex_id, pr_type='fastest_time',
                    value=timed, unit='seconds', previous_value=prev.value if prev else None,
                ))
                new_pr_count += 1
                diff_msg = f' You shaved {prev.value - timed:.0f} seconds off!' if prev else ''
                create_notification(user_id, 'pr_fastest_time',
                    f'⚡ Fastest {ex_name} — {format_duration(timed)}!{diff_msg}')

        # ── 8. Max unbroken reps ──
        if reps_str and (not weight or weight == 0):
            reps_list = [int(r.strip()) for r in str(reps_str).split(',') if r.strip().isdigit()]
            if reps_list:
                max_single = max(reps_list)
                prev = PersonalRecord.query.filter_by(
                    user_id=user_id, exercise_id=ex_id, pr_type='max_reps'
                ).order_by(PersonalRecord.value.desc()).first()
                if not prev or max_single > prev.value:
                    db.session.add(PersonalRecord(
                        user_id=user_id, exercise_id=ex_id, pr_type='max_reps',
                        value=max_single, unit='reps', previous_value=prev.value if prev else None,
                    ))
                    new_pr_count += 1
                    create_notification(user_id, 'pr_max_reps',
                        f'💯 New max {ex_name} — {max_single} unbroken reps!')

    # ── 5. Total workout volume PR ──
    if workout_id and total_workout_volume > 0:
        workout = Workout.query.get(workout_id)
        workout_name = workout.name if workout else 'Workout'
        record = WorkoutVolumeRecord.query.filter_by(user_id=user_id, workout_id=workout_id).first()
        if not record:
            record = WorkoutVolumeRecord(user_id=user_id, workout_id=workout_id, best_volume_lbs=0)
            db.session.add(record)
            db.session.flush()
        if total_workout_volume > record.best_volume_lbs:
            record.best_volume_lbs = total_workout_volume
            record.achieved_at = now
            create_notification(user_id, 'pr_volume_workout',
                f'💥 Biggest {workout_name} EVER — {format_number(total_workout_volume)} lbs total volume!')

    # ── 11. Badge checks ──
    check_badges(user_id, {
        'action': 'workout_logged',
        'new_pr_count': new_pr_count,
        'hour': now.hour,
    })

    # ── 12. Streak updates ──
    update_streak(user_id, 'workout')
    update_streak(user_id, 'logging')

    # ── 13. Time-of-day ── (handled in check_badges via 'hour')

    # ── 14. Week started / weekly target ──
    from datetime import date as _date
    week_start = _date.today() - timedelta(days=_date.today().weekday())
    week_start_dt = datetime(week_start.year, week_start.month, week_start.day, tzinfo=timezone.utc)
    week_checkins = WorkoutCheckin.query.filter(
        WorkoutCheckin.client_id == user_id,
        WorkoutCheckin.completed_at >= week_start_dt,
    ).count()
    if week_checkins == 1:
        create_notification(user_id, 'week_started',
            '📅 First workout of the week — keep the momentum going!')
    # Weekly target check (default 3x/week)
    weekly_target = 3
    if week_checkins == weekly_target:
        create_notification(user_id, 'weekly_target_hit',
            f'🎉 {week_checkins} workouts this week — weekly target crushed!')

    # ── 15. Workout completed summary ──
    if total_workout_volume > 0:
        workout = Workout.query.get(workout_id) if workout_id else None
        wname = workout.name if workout else 'Workout'
        create_notification(user_id, 'workout_completed',
            f'✅ {wname} done! {format_number(total_workout_volume)} lbs total volume. 💪')

    db.session.flush()


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
                           movement_patterns=MOVEMENT_PATTERNS, equipment_options=EQUIPMENT_OPTIONS, muscle_groups=MUSCLE_GROUPS)


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
                           movement_patterns=MOVEMENT_PATTERNS, equipment_options=EQUIPMENT_OPTIONS, muscle_groups=MUSCLE_GROUPS)


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
                               movement_patterns=MOVEMENT_PATTERNS, equipment_options=EQUIPMENT_OPTIONS, muscle_groups=MUSCLE_GROUPS)
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
        group_ids = request.form.getlist('group_id[]')
        group_types = request.form.getlist('group_type[]')
        for i, eid in enumerate(ex_ids):
            if not eid:
                continue
            gid = group_ids[i] if i < len(group_ids) and group_ids[i] else None
            gtype = group_types[i] if i < len(group_types) and group_types[i] else None
            we = WorkoutExercise(
                workout_id=workout.id,
                exercise_id=int(eid),
                order=i,
                sets=int(sets_list[i]) if i < len(sets_list) and sets_list[i] else 3,
                reps=reps_list[i] if i < len(reps_list) and reps_list[i] else '10',
                rest_seconds=int(rest_list[i]) if i < len(rest_list) and rest_list[i] else 60,
                notes=notes_list[i] if i < len(notes_list) and notes_list[i] else None,
                group_id=int(gid) if gid else None,
                group_type=gtype if gtype else None,
            )
            db.session.add(we)
        db.session.commit()
        flash(f'Workout "{workout.name}" created.', 'success')
        return redirect(url_for('workout_detail', workout_id=workout.id))
    return render_template('workout_form.html', workout=None, exercises=exercises, movement_patterns=MOVEMENT_PATTERNS, equipment_options=EQUIPMENT_OPTIONS, muscle_groups=MUSCLE_GROUPS)


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
            return render_template('workout_form.html', workout=workout, exercises=exercises, movement_patterns=MOVEMENT_PATTERNS, equipment_options=EQUIPMENT_OPTIONS, muscle_groups=MUSCLE_GROUPS)
        workout.name = name
        workout.description = request.form.get('description', '').strip() or None
        # Clear existing exercises
        WorkoutExercise.query.filter_by(workout_id=workout.id).delete()
        ex_ids = request.form.getlist('exercise_id[]')
        sets_list = request.form.getlist('sets[]')
        reps_list = request.form.getlist('reps[]')
        rest_list = request.form.getlist('rest[]')
        notes_list = request.form.getlist('notes_ex[]')
        group_ids = request.form.getlist('group_id[]')
        group_types = request.form.getlist('group_type[]')
        for i, eid in enumerate(ex_ids):
            if not eid:
                continue
            gid = group_ids[i] if i < len(group_ids) and group_ids[i] else None
            gtype = group_types[i] if i < len(group_types) and group_types[i] else None
            we = WorkoutExercise(
                workout_id=workout.id,
                exercise_id=int(eid),
                order=i,
                sets=int(sets_list[i]) if i < len(sets_list) and sets_list[i] else 3,
                reps=reps_list[i] if i < len(reps_list) and reps_list[i] else '10',
                rest_seconds=int(rest_list[i]) if i < len(rest_list) and rest_list[i] else 60,
                notes=notes_list[i] if i < len(notes_list) and notes_list[i] else None,
                group_id=int(gid) if gid else None,
                group_type=gtype if gtype else None,
            )
            db.session.add(we)
        db.session.commit()
        flash(f'Workout "{workout.name}" updated.', 'success')
        return redirect(url_for('workout_detail', workout_id=workout.id))
    return render_template('workout_form.html', workout=workout, exercises=exercises, movement_patterns=MOVEMENT_PATTERNS, equipment_options=EQUIPMENT_OPTIONS, muscle_groups=MUSCLE_GROUPS)


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


@app.route('/programs/manage-assignments', methods=['GET', 'POST'])
@staff_required
def program_manage_assignments():
    from datetime import date as date_type
    if current_user.role == 'admin':
        all_clients = User.query.filter_by(role='client', is_active=True).order_by(User.name).all()
        all_programs = Program.query.order_by(Program.name).all()
    else:
        all_clients = User.query.filter_by(role='client', is_active=True, trainer_id=current_user.id).order_by(User.name).all()
        all_programs = Program.query.filter_by(trainer_id=current_user.id).order_by(Program.name).all()

    if request.method == 'POST':
        program_id = request.form.get('program_id', '').strip()
        start_raw = request.form.get('start_date', '').strip()
        client_ids = request.form.getlist('client_ids')
        if not program_id or not start_raw or not client_ids:
            flash('Select a program, start date, and at least one client.', 'danger')
        else:
            prog = Program.query.get_or_404(int(program_id))
            start_date = date_type.fromisoformat(start_raw)
            if current_user.role != 'admin':
                allowed = {str(c.id) for c in all_clients}
                client_ids = [cid for cid in client_ids if cid in allowed]
            assigned = 0
            for cid in client_ids:
                exists = ProgramAssignment.query.filter_by(
                    program_id=prog.id, client_id=int(cid), start_date=start_date
                ).first()
                if not exists:
                    db.session.add(ProgramAssignment(
                        program_id=prog.id,
                        client_id=int(cid),
                        start_date=start_date,
                        assigned_by_id=current_user.id,
                    ))
                    assigned += 1
            db.session.commit()
            flash(f'Assigned {prog.name} to {assigned} client(s) starting {start_date.strftime("%b %d, %Y")}.', 'success')
        return redirect(url_for('program_manage_assignments'))

    # Build per-client current assignment info
    client_data = []
    for c in all_clients:
        active = ProgramAssignment.query.filter_by(client_id=c.id).order_by(
            ProgramAssignment.start_date.desc()
        ).first()
        client_data.append({'client': c, 'assignment': active})

    from datetime import date as date_type
    return render_template('program_assignments.html',
        client_data=client_data,
        all_programs=all_programs,
        today=date_type.today().isoformat(),
    )


@app.route('/programs/assignments/<int:pa_id>/remove', methods=['POST'])
@staff_required
def program_assignment_remove(pa_id):
    pa = ProgramAssignment.query.get_or_404(pa_id)
    if current_user.role != 'admin' and pa.assigned_by_id != current_user.id:
        abort(403)
    db.session.delete(pa)
    db.session.commit()
    flash('Assignment removed.', 'success')
    return redirect(url_for('program_manage_assignments'))


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
    """Read-only workout view — same layout as logging but no input fields."""
    workout = Workout.query.get_or_404(workout_id)
    workout_exercises = workout.exercises.order_by(WorkoutExercise.order).all()
    phase_id = request.args.get('phase_id', type=int)
    week_num = request.args.get('week', type=int)
    override_map = {}
    if phase_id and week_num:
        overrides = ProgressionOverride.query.filter_by(phase_id=phase_id, week_num=week_num).all()
        override_map = {o.exercise_id: o for o in overrides}
    return render_template('workout_log.html',
        workout=workout,
        workout_exercises=workout_exercises,
        override_map=override_map,
        phase_id=phase_id,
        week_num=week_num,
        extract_youtube_id=extract_youtube_id,
        view_only=True,  # Flag: no inputs, no submit
    )


@app.route('/workout-log/<int:workout_id>', methods=['GET', 'POST'])
@login_required
def client_workout_log(workout_id):
    """Client workout logging page — shows prescribed sets/reps/RPE + weight input."""
    if current_user.role not in ('client',):
        return redirect(url_for('dashboard'))
    workout = Workout.query.get_or_404(workout_id)
    phase_id = request.args.get('phase_id', type=int)
    week_num  = request.args.get('week', type=int)

    # Load exercises in order
    workout_exercises = workout.exercises.order_by(WorkoutExercise.order).all()

    # Load progression overrides for this phase+week (if available)
    override_map = {}
    if phase_id and week_num:
        overrides = ProgressionOverride.query.filter_by(phase_id=phase_id, week_num=week_num).all()
        override_map = {o.exercise_id: o for o in overrides}

    if request.method == 'POST':
        from datetime import date as date_type
        log_date = date_type.today()

        # ── Duplicate prevention: check if this workout was already logged today ──
        already_logged = ExerciseLog.query.filter(
            ExerciseLog.client_id == current_user.id,
            ExerciseLog.log_date == log_date,
            ExerciseLog.exercise_id.in_([we.exercise_id for we in workout_exercises]),
        ).first()
        if already_logged:
            flash('This workout was already logged today.', 'info')
            return redirect(url_for('achievements'))

        saved = 0
        exercise_data_list = []
        for we in workout_exercises:
            weight_val  = request.form.get(f'weight_{we.exercise_id}', '').strip()
            sets_val    = request.form.get(f'sets_{we.exercise_id}', '').strip()
            reps_val    = request.form.get(f'reps_{we.exercise_id}', '').strip()
            notes_val   = request.form.get(f'notes_{we.exercise_id}', '').strip()
            dur_min     = request.form.get(f'dur_min_{we.exercise_id}', '').strip()
            dur_sec     = request.form.get(f'dur_sec_{we.exercise_id}', '').strip()
            time_min    = request.form.get(f'time_min_{we.exercise_id}', '').strip()
            time_sec    = request.form.get(f'time_sec_{we.exercise_id}', '').strip()

            # Calculate duration/time in seconds
            dur_seconds = None
            if dur_min or dur_sec:
                dur_seconds = (float(dur_min or 0) * 60) + float(dur_sec or 0)
            time_seconds = None
            if time_min or time_sec:
                time_seconds = (float(time_min or 0) * 60) + float(time_sec or 0)

            if not any([weight_val, sets_val, reps_val, dur_seconds, time_seconds]):
                continue
            try:
                weight_f = float(weight_val) if weight_val else None
            except ValueError:
                weight_f = None
            db.session.add(ExerciseLog(
                client_id=current_user.id,
                exercise_id=we.exercise_id,
                log_date=log_date,
                sets_completed=int(sets_val) if sets_val.isdigit() else None,
                reps_completed=reps_val or None,
                weight_lbs=weight_f,
                duration_seconds=dur_seconds,
                time_seconds=time_seconds,
                notes=notes_val or None,
            ))
            exercise_data_list.append({
                'exercise_id': we.exercise_id,
                'sets': int(sets_val) if sets_val.isdigit() else 0,
                'reps': reps_val or '',
                'weight_lbs': weight_f or 0.0,
                'duration_seconds': dur_seconds,
                'time_seconds': time_seconds,
            })
            saved += 1

        # Record checkin + fire the engagement engine
        if saved > 0:
            # Create a WorkoutCheckin if we have a phase/week context
            if phase_id and week_num:
                assignment = ProgramAssignment.query.filter_by(client_id=current_user.id).order_by(
                    ProgramAssignment.start_date.desc()).first()
                if assignment:
                    phase = ProgramPhase.query.get(phase_id)
                    if phase:
                        day_num = log_date.isoweekday()  # 1=Mon ... 7=Sun
                        existing_checkin = WorkoutCheckin.query.filter_by(
                            client_id=current_user.id, assignment_id=assignment.id,
                            week=week_num, day=day_num,
                        ).first()
                        if not existing_checkin:
                            db.session.add(WorkoutCheckin(
                                client_id=current_user.id, assignment_id=assignment.id,
                                week=week_num, day=day_num,
                            ))
            process_workout_log(current_user.id, workout_id, exercise_data_list, log_date)
        db.session.commit()
        flash(f'Workout logged! {saved} exercise{"s" if saved != 1 else ""} recorded.', 'success')
        return redirect(url_for('achievements'))

    return render_template('workout_log.html',
        workout=workout,
        workout_exercises=workout_exercises,
        override_map=override_map,
        phase_id=phase_id,
        week_num=week_num,
        extract_youtube_id=extract_youtube_id,
        view_only=False,
    )


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

    # Week navigation — clients can browse past weeks and up to 2 weeks ahead
    view_week = request.args.get('week', type=int)  # ?week=N to browse
    total_weeks = 0
    actual_week = None  # The "real" current week based on today's date
    is_viewing_current_week = True
    day_checkins = {}  # {day_num: bool} — checkin status for each day of viewed week
    max_viewable_week = 0  # Furthest week the client can see (actual + 2)

    if assignment:
        days_since_start = (today - assignment.start_date).days
        phases = assignment.program.phases.all()

        if phases:
            # Phase-based program
            total_phases = len(phases)
            total_weeks = sum(ph.weeks for ph in phases)
            running_days = 0
            auto_phase = None
            auto_week_in_phase = None
            auto_day_in_week = None
            for ph in phases:
                phase_days_total = ph.weeks * 7
                if days_since_start < running_days + phase_days_total:
                    days_in_phase = days_since_start - running_days
                    auto_week_in_phase = (days_in_phase // 7) + 1
                    auto_day_in_week = (days_in_phase % 7) + 1
                    auto_phase = ph
                    break
                running_days += phase_days_total
            if auto_phase is None:
                program_complete = True
                auto_phase = phases[-1]
                auto_week_in_phase = phases[-1].weeks
                auto_day_in_week = 7

            # Calculate actual global week
            actual_global = 0
            for ph in phases:
                if ph.id == auto_phase.id:
                    actual_global += auto_week_in_phase
                    break
                actual_global += ph.weeks
            actual_week = actual_global

            # Determine which week to VIEW (user-requested or auto)
            # Clients can only see up to 2 weeks ahead of current
            max_viewable_week = min(actual_week + 2, total_weeks)
            if view_week is not None:
                view_week = max(1, min(view_week, max_viewable_week))
            else:
                view_week = actual_week
            is_viewing_current_week = (view_week == actual_week)

            # Resolve view_week → phase + week_in_phase
            running_weeks = 0
            for ph in phases:
                if view_week <= running_weeks + ph.weeks:
                    current_phase = ph
                    week_in_phase = view_week - running_weeks
                    break
                running_weeks += ph.weeks
            if current_phase is None:
                current_phase = phases[-1]
                week_in_phase = phases[-1].weeks

            current_week = view_week
            current_day = auto_day_in_week if is_viewing_current_week else None
            day_in_week = current_day

            # Load phase days for the viewed phase
            phase_day_rows = current_phase.days.all()
            week_days = {phd.day_num: phd for phd in phase_day_rows}

            # Today's workout (only if viewing current week)
            if is_viewing_current_week and not program_complete:
                today_program_day = week_days.get(auto_day_in_week)
                if today_program_day and today_program_day.workout_id:
                    exercise_ids = [we.exercise_id for we in today_program_day.workout.exercises.all()]
                    for eid in exercise_ids:
                        ov = ProgressionOverride.query.filter_by(
                            phase_id=current_phase.id,
                            exercise_id=eid,
                            week_num=week_in_phase,
                        ).first()
                        if ov:
                            today_overrides[eid] = ov

            # Checkin status for each day of the viewed week
            checkins = WorkoutCheckin.query.filter_by(
                client_id=current_user.id, assignment_id=assignment.id,
                week=view_week,
            ).all()
            day_checkins = {c.day: True for c in checkins}
            if is_viewing_current_week and today_program_day:
                checkin_done = day_checkins.get(auto_day_in_week, False)

        else:
            # Legacy flat-week program
            total_weeks = assignment.program.weeks
            total_program_days = total_weeks * 7
            if days_since_start >= total_program_days:
                program_complete = True
                actual_week = total_weeks
                auto_day = 7
            else:
                actual_week = (days_since_start // 7) + 1
                auto_day = (days_since_start % 7) + 1

            max_viewable_week = min(actual_week + 2, total_weeks)
            if view_week is not None:
                view_week = max(1, min(view_week, max_viewable_week))
            else:
                view_week = actual_week
            is_viewing_current_week = (view_week == actual_week)

            current_week = view_week
            current_day = auto_day if is_viewing_current_week else None

            week_day_rows = ProgramDay.query.filter_by(
                program_id=assignment.program_id, week=view_week
            ).all()
            week_days = {pd.day: pd for pd in week_day_rows}

            if is_viewing_current_week:
                today_program_day = week_days.get(auto_day)

            checkins = WorkoutCheckin.query.filter_by(
                client_id=current_user.id, assignment_id=assignment.id,
                week=view_week,
            ).all()
            day_checkins = {c.day: True for c in checkins}
            if is_viewing_current_week and today_program_day:
                checkin_done = day_checkins.get(auto_day, False)

    return render_template('my_program.html',
        assignment=assignment,
        current_week=current_week,
        current_day=current_day,
        today_program_day=today_program_day,
        checkin_done=checkin_done,
        today=today,
        program_complete=program_complete,
        week_days=week_days,
        day_checkins=day_checkins,
        # Phase-based extras
        current_phase=current_phase,
        week_in_phase=week_in_phase,
        day_in_week=day_in_week,
        total_phases=total_phases,
        today_overrides=today_overrides,
        # Week navigation
        view_week=view_week,
        total_weeks=total_weeks,
        actual_week=actual_week,
        max_viewable_week=max_viewable_week,
        is_viewing_current_week=is_viewing_current_week,
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


# ─── Stripe Billing ──────────────────────────────────────────────────────────

def stripe_enabled():
    return bool(_stripe.api_key)


def get_or_create_stripe_customer(user):
    """Get or create a Stripe Customer for a portal user."""
    if user.stripe_customer_id:
        return user.stripe_customer_id
    if not stripe_enabled():
        return None
    customer = _stripe.Customer.create(
        name=user.name,
        email=user.email,
        metadata={'portal_user_id': str(user.id), 'role': user.role},
    )
    user.stripe_customer_id = customer.id
    db.session.commit()
    return customer.id


def get_or_create_stripe_price(plan):
    """Ensure a MembershipPlan has a Stripe Product + Price. Returns stripe_price_id."""
    if plan.stripe_price_id:
        return plan.stripe_price_id
    if not stripe_enabled() or not plan.price_cents:
        return None
    # Create Product
    if not plan.stripe_product_id:
        product = _stripe.Product.create(
            name=plan.name,
            metadata={'portal_plan_id': str(plan.id), 'plan_type': plan.plan_type},
        )
        plan.stripe_product_id = product.id
    # Create Price (recurring monthly)
    price = _stripe.Price.create(
        product=plan.stripe_product_id,
        unit_amount=plan.price_cents,
        currency='usd',
        recurring={'interval': 'month'},
        metadata={'portal_plan_id': str(plan.id)},
    )
    plan.stripe_price_id = price.id
    db.session.commit()
    return price.id


@app.route('/admin/billing/subscribe', methods=['POST'])
@admin_required
@limiter.limit("10 per minute")
def admin_start_subscription():
    """Create a Stripe subscription for a client on a given plan."""
    client_id = int(request.form['client_id'])
    plan_id = int(request.form['plan_id'])
    billing_day = int(request.form.get('billing_day', 1))

    client = User.query.get_or_404(client_id)
    plan = MembershipPlan.query.get_or_404(plan_id)

    if not stripe_enabled():
        flash('Stripe is not configured. Set STRIPE_SECRET_KEY in environment.', 'danger')
        return redirect(url_for('admin_packages'))

    # Ensure Stripe customer + price exist
    customer_id = get_or_create_stripe_customer(client)
    price_id = get_or_create_stripe_price(plan)
    if not customer_id or not price_id:
        flash('Could not create Stripe customer or price.', 'danger')
        return redirect(url_for('admin_packages'))

    # Check for existing active subscription
    existing = ClientSubscription.query.filter_by(client_id=client_id, status='active').first()
    if existing:
        flash(f'{client.name} already has an active subscription. Change their plan instead.', 'danger')
        return redirect(url_for('admin_packages'))

    try:
        sub = _stripe.Subscription.create(
            customer=customer_id,
            items=[{'price': price_id}],
            billing_cycle_anchor_config={'day_of_month': billing_day},
            proration_behavior='create_prorations',
            payment_behavior='default_incomplete',
            expand=['latest_invoice.payment_intent'],
            metadata={'portal_client_id': str(client_id), 'portal_plan_id': str(plan_id)},
        )
        # Save subscription record
        local_sub = ClientSubscription(
            client_id=client_id, plan_id=plan_id,
            stripe_subscription_id=sub.id,
            status=sub.status,
            billing_day=billing_day,
        )
        if sub.current_period_start:
            from datetime import date as _date
            local_sub.current_period_start = datetime.fromtimestamp(sub.current_period_start).date()
            local_sub.current_period_end = datetime.fromtimestamp(sub.current_period_end).date()
        db.session.add(local_sub)
        db.session.commit()

        # If subscription needs payment method, send Stripe-hosted invoice link
        if sub.status == 'incomplete' and sub.latest_invoice:
            invoice = sub.latest_invoice
            if hasattr(invoice, 'hosted_invoice_url') and invoice.hosted_invoice_url:
                flash(f'Subscription created for {client.name}. Invoice sent — they need to add a payment method.', 'success')
            else:
                flash(f'Subscription created for {client.name} (status: {sub.status}).', 'success')
        else:
            flash(f'Subscription started for {client.name} — ${plan.price_cents/100:.2f}/mo on the {billing_day}{"st" if billing_day==1 else "th"}.', 'success')

    except _stripe.error.StripeError as e:
        flash(f'Stripe error: {e.user_message or str(e)}', 'danger')

    return redirect(url_for('admin_packages'))


@app.route('/admin/billing/change-plan', methods=['POST'])
@admin_required
def admin_change_plan():
    """Swap a client to a different plan — Stripe prorates automatically."""
    sub_id = int(request.form['subscription_id'])
    new_plan_id = int(request.form['new_plan_id'])

    local_sub = ClientSubscription.query.get_or_404(sub_id)
    new_plan = MembershipPlan.query.get_or_404(new_plan_id)
    client = User.query.get(local_sub.client_id)

    if not stripe_enabled():
        flash('Stripe not configured.', 'danger')
        return redirect(url_for('admin_packages'))

    new_price_id = get_or_create_stripe_price(new_plan)
    if not new_price_id:
        flash('Could not create price for new plan.', 'danger')
        return redirect(url_for('admin_packages'))

    try:
        stripe_sub = _stripe.Subscription.retrieve(local_sub.stripe_subscription_id)
        _stripe.Subscription.modify(
            local_sub.stripe_subscription_id,
            items=[{
                'id': stripe_sub['items']['data'][0]['id'],
                'price': new_price_id,
            }],
            proration_behavior='create_prorations',
            metadata={'portal_plan_id': str(new_plan_id)},
        )
        old_plan_name = local_sub.plan.name if local_sub.plan else 'Unknown'
        local_sub.plan_id = new_plan_id
        local_sub.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        flash(f'{client.name} moved from {old_plan_name} → {new_plan.name}. Stripe will prorate.', 'success')

    except _stripe.error.StripeError as e:
        flash(f'Stripe error: {e.user_message or str(e)}', 'danger')

    return redirect(url_for('admin_packages'))


@app.route('/admin/billing/cancel', methods=['POST'])
@admin_required
def admin_cancel_subscription():
    """Cancel a subscription at end of current period."""
    sub_id = int(request.form['subscription_id'])
    local_sub = ClientSubscription.query.get_or_404(sub_id)
    client = User.query.get(local_sub.client_id)

    if not stripe_enabled():
        flash('Stripe not configured.', 'danger')
        return redirect(url_for('admin_packages'))

    try:
        _stripe.Subscription.modify(
            local_sub.stripe_subscription_id,
            cancel_at_period_end=True,
        )
        local_sub.status = 'canceled'
        local_sub.canceled_at = datetime.now(timezone.utc)
        db.session.commit()
        flash(f'{client.name}\'s subscription will cancel at end of billing period.', 'success')

    except _stripe.error.StripeError as e:
        flash(f'Stripe error: {e.user_message or str(e)}', 'danger')

    return redirect(url_for('admin_packages'))


@app.route('/admin/billing/reactivate', methods=['POST'])
@admin_required
def admin_reactivate_subscription():
    """Un-cancel a subscription that was set to cancel at period end."""
    sub_id = int(request.form['subscription_id'])
    local_sub = ClientSubscription.query.get_or_404(sub_id)
    client = User.query.get(local_sub.client_id)

    if not stripe_enabled():
        flash('Stripe not configured.', 'danger')
        return redirect(url_for('admin_packages'))

    try:
        _stripe.Subscription.modify(
            local_sub.stripe_subscription_id,
            cancel_at_period_end=False,
        )
        local_sub.status = 'active'
        local_sub.canceled_at = None
        db.session.commit()
        flash(f'{client.name}\'s subscription reactivated.', 'success')

    except _stripe.error.StripeError as e:
        flash(f'Stripe error: {e.user_message or str(e)}', 'danger')

    return redirect(url_for('admin_packages'))


@app.route('/webhook/stripe', methods=['POST'])
@csrf.exempt
def stripe_webhook():
    """Handle Stripe webhook events — payment success, failure, subscription changes."""
    payload = request.get_data(as_text=True)
    sig = request.headers.get('Stripe-Signature', '')

    if not STRIPE_WEBHOOK_SECRET:
        audit_logger.warning('Stripe webhook received but STRIPE_WEBHOOK_SECRET not configured — rejecting')
        return 'Webhook secret not configured', 503
    try:
        event = _stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except (ValueError, _stripe.error.SignatureVerificationError):
        return 'Invalid signature', 400

    etype = event['type']
    obj = event['data']['object']

    if etype == 'invoice.paid':
        # Successful recurring payment — auto-create SessionPackage for the month
        sub_id = obj.get('subscription')
        if sub_id:
            local_sub = ClientSubscription.query.filter_by(stripe_subscription_id=sub_id).first()
            if local_sub and local_sub.plan:
                from datetime import date as _date
                now = datetime.now(timezone.utc)
                existing_pkg = SessionPackage.query.filter_by(
                    client_id=local_sub.client_id, month=now.month, year=now.year
                ).first()
                if not existing_pkg:
                    db.session.add(SessionPackage(
                        client_id=local_sub.client_id,
                        month=now.month, year=now.year,
                        sessions_purchased=local_sub.plan.sessions_per_month,
                        plan_id=local_sub.plan_id,
                        is_crew=local_sub.plan.is_crew,
                        notes=f'Auto-created from Stripe payment ({obj.get("id", "")})',
                    ))
                    db.session.commit()
                    audit_logger.info(f'Auto-created package for client {local_sub.client_id} '
                                     f'({now.month}/{now.year}) from Stripe invoice {obj.get("id")}')

    elif etype == 'invoice.payment_failed':
        sub_id = obj.get('subscription')
        if sub_id:
            local_sub = ClientSubscription.query.filter_by(stripe_subscription_id=sub_id).first()
            if local_sub:
                local_sub.status = 'past_due'
                db.session.commit()
                client = User.query.get(local_sub.client_id)
                audit_logger.warning(f'Payment failed for {client.name if client else local_sub.client_id} '
                                     f'— subscription {sub_id}')

    elif etype == 'customer.subscription.updated':
        sub_id = obj.get('id')
        local_sub = ClientSubscription.query.filter_by(stripe_subscription_id=sub_id).first()
        if local_sub:
            local_sub.status = obj.get('status', local_sub.status)
            if obj.get('current_period_start'):
                local_sub.current_period_start = datetime.fromtimestamp(obj['current_period_start']).date()
                local_sub.current_period_end = datetime.fromtimestamp(obj['current_period_end']).date()
            db.session.commit()

    elif etype == 'customer.subscription.deleted':
        sub_id = obj.get('id')
        local_sub = ClientSubscription.query.filter_by(stripe_subscription_id=sub_id).first()
        if local_sub:
            local_sub.status = 'canceled'
            local_sub.canceled_at = datetime.now(timezone.utc)
            db.session.commit()

    return '', 200


# ─── V2 Engagement Routes ────────────────────────────────────────────────────

@app.route('/daily-log')
@login_required
def daily_log_page():
    if current_user.role != 'client':
        return redirect(url_for('dashboard'))
    from datetime import date as _date
    today = _date.today()
    status = get_today_log_status(current_user.id)
    # Get today's actual values
    today_logs = DailyLog.query.filter_by(user_id=current_user.id, log_date=today).all()
    log_values = {log.log_type: log for log in today_logs}
    return render_template('daily_log.html', status=status, log_values=log_values, today=today)


@app.route('/daily-log/water', methods=['POST'])
@login_required
def daily_log_water():
    from datetime import date as _date
    value = float(request.form.get('value', 0))
    existing = DailyLog.query.filter_by(user_id=current_user.id, log_date=_date.today(), log_type='water').first()
    if existing:
        existing.value = (existing.value or 0) + value
    else:
        db.session.add(DailyLog(user_id=current_user.id, log_date=_date.today(), log_type='water', value=value))
    process_daily_log(current_user.id, 'water', value=(existing.value if existing else value))
    db.session.commit()
    flash('Water logged!', 'success')
    return redirect(url_for('daily_log_page'))


@app.route('/daily-log/sleep', methods=['POST'])
@login_required
def daily_log_sleep():
    from datetime import date as _date
    hours = float(request.form.get('hours', 0))
    quality = int(request.form.get('quality', 3))
    existing = DailyLog.query.filter_by(user_id=current_user.id, log_date=_date.today(), log_type='sleep').first()
    if existing:
        existing.value = hours
        existing.quality = quality
    else:
        db.session.add(DailyLog(user_id=current_user.id, log_date=_date.today(), log_type='sleep', value=hours, quality=quality))
    process_daily_log(current_user.id, 'sleep', value=hours)
    db.session.commit()
    flash('Sleep logged!', 'success')
    return redirect(url_for('daily_log_page'))


@app.route('/daily-log/meal', methods=['POST'])
@login_required
def daily_log_meal():
    from datetime import date as _date
    notes = request.form.get('notes', '').strip()
    db.session.add(DailyLog(user_id=current_user.id, log_date=_date.today(), log_type='meal', notes=notes))
    process_daily_log(current_user.id, 'meal')
    db.session.commit()
    flash('Meal logged!', 'success')
    return redirect(url_for('daily_log_page'))


@app.route('/daily-log/weighin', methods=['POST'])
@login_required
def daily_log_weighin():
    from datetime import date as _date
    weight = float(request.form.get('weight', 0))
    existing = DailyLog.query.filter_by(user_id=current_user.id, log_date=_date.today(), log_type='weighin').first()
    if existing:
        existing.value = weight
    else:
        db.session.add(DailyLog(user_id=current_user.id, log_date=_date.today(), log_type='weighin', value=weight))
    process_daily_log(current_user.id, 'weighin', value=weight)
    db.session.commit()
    flash('Weigh-in recorded!', 'success')
    return redirect(url_for('daily_log_page'))


@app.route('/api/daily-log/today')
@login_required
def api_daily_log_today():
    return jsonify(get_today_log_status(current_user.id))


@app.route('/achievements')
@login_required
def achievements():
    if current_user.role != 'client':
        return redirect(url_for('dashboard'))
    # Build badge progress list
    all_badges = BadgeDefinition.query.all()
    earned = {ub.badge_id: ub.earned_at for ub in UserBadge.query.filter_by(user_id=current_user.id).all()}
    total_workouts = WorkoutCheckin.query.filter_by(client_id=current_user.id).count()
    total_prs = PersonalRecord.query.filter_by(user_id=current_user.id).count()
    total_volume = db.session.query(
        db.func.coalesce(db.func.sum(ExerciseVolumeTotal.total_volume_lbs), 0)
    ).filter_by(user_id=current_user.id).scalar()

    badge_progress = []
    for b in all_badges:
        unlocked = b.id in earned
        progress = 0
        total = b.threshold or 1

        # Calculate progress based on category
        if b.category == 'workout' and b.threshold:
            progress = min(total_workouts, b.threshold)
        elif b.category == 'pr' and b.threshold and 'collector' in b.key:
            progress = min(total_prs, b.threshold)
        elif b.category == 'volume' and b.threshold and b.key.startswith('volume_') and not b.key.startswith('volume_ex'):
            progress = min(int(total_volume), b.threshold)
        elif b.category == 'streak' and b.threshold:
            best = get_best_active_streak(current_user.id)
            progress = min(best, b.threshold)
        elif unlocked:
            progress = total

        badge_progress.append({
            'badge': b,
            'progress': progress,
            'total': total,
            'unlocked': unlocked,
            'earned_at': earned.get(b.id),
            'rarity': RARITY_MAP.get(b.level, 'common'),
        })

    badge_progress = sort_achievements(badge_progress)

    # Stats
    stats = {
        'unlocked': len(earned),
        'streak': get_best_active_streak(current_user.id),
        'total_workouts': total_workouts,
        'completion': int(len(earned) / len(all_badges) * 100) if all_badges else 0,
    }

    # Notifications (most recent 30)
    notifications = Notification.query.filter_by(user_id=current_user.id).order_by(
        Notification.is_read.asc(), Notification.created_at.desc()
    ).limit(30).all()
    unread_count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()

    # Category list for tabs
    categories = sorted(set(b.category for b in all_badges))

    return render_template('achievements.html',
        badge_progress=badge_progress,
        stats=stats,
        notifications=notifications,
        unread_count=unread_count,
        categories=categories,
        get_notif_emoji=get_notif_emoji,
        RARITY_MAP=RARITY_MAP,
    )


@app.route('/notifications/mark-all-read', methods=['POST'])
@login_required
def mark_all_read():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({'is_read': True})
    db.session.commit()
    return '', 204


@app.route('/api/notifications/recent')
@login_required
def api_notifications_recent():
    limit = request.args.get('limit', 20, type=int)
    notifs = Notification.query.filter_by(user_id=current_user.id).order_by(
        Notification.created_at.desc()
    ).limit(limit).all()
    return jsonify([{
        'id': n.id, 'type': n.notif_type, 'message': n.message,
        'emoji': get_notif_emoji(n.notif_type), 'is_read': n.is_read,
        'created_at': n.created_at.isoformat(),
    } for n in notifs])


@app.route('/leaderboard')
@login_required
def leaderboard():
    if current_user.role != 'client':
        return redirect(url_for('dashboard'))
    # Longest active streaks
    all_streaks = db.session.query(
        Streak.user_id, db.func.max(Streak.current_count).label('best')
    ).group_by(Streak.user_id).order_by(db.text('best DESC')).limit(10).all()

    streak_rows = []
    for s in all_streaks:
        u = User.query.get(s.user_id)
        if u and u.role == 'client':
            name_parts = u.name.split()
            display = f'{name_parts[0]} {name_parts[-1][0]}.' if len(name_parts) > 1 else name_parts[0]
            streak_rows.append({
                'name': display, 'value': s.best,
                'is_you': u.id == current_user.id,
            })

    # Most badges earned
    badge_counts = db.session.query(
        UserBadge.user_id, db.func.count(UserBadge.id).label('cnt')
    ).group_by(UserBadge.user_id).order_by(db.text('cnt DESC')).limit(10).all()

    badge_rows = []
    for b in badge_counts:
        u = User.query.get(b.user_id)
        if u and u.role == 'client':
            name_parts = u.name.split()
            display = f'{name_parts[0]} {name_parts[-1][0]}.' if len(name_parts) > 1 else name_parts[0]
            badge_rows.append({
                'name': display, 'value': b.cnt,
                'is_you': u.id == current_user.id,
            })

    return render_template('leaderboard.html',
        streak_rows=streak_rows, badge_rows=badge_rows)


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
            ('group_id', 'ALTER TABLE workout_exercises ADD COLUMN group_id INTEGER'),
            ('group_type', 'ALTER TABLE workout_exercises ADD COLUMN group_type VARCHAR(20)'),
            ('title',    'ALTER TABLE notifications ADD COLUMN title VARCHAR(120)'),
            ('icon',     'ALTER TABLE notifications ADD COLUMN icon VARCHAR(50)'),
            ('level',    'ALTER TABLE notifications ADD COLUMN level VARCHAR(20)'),
            # V2 Engagement: Exercise time-domain fields
            ('is_timed',         'ALTER TABLE exercises ADD COLUMN is_timed BOOLEAN DEFAULT FALSE'),
            ('is_hold',          'ALTER TABLE exercises ADD COLUMN is_hold BOOLEAN DEFAULT FALSE'),
            # Stripe billing fields
            ('stripe_customer_id', 'ALTER TABLE users ADD COLUMN stripe_customer_id VARCHAR(100) UNIQUE'),
            ('stripe_price_id',    'ALTER TABLE membership_plans ADD COLUMN stripe_price_id VARCHAR(100)'),
            ('stripe_product_id',  'ALTER TABLE membership_plans ADD COLUMN stripe_product_id VARCHAR(100)'),
            # V2 Engagement: ExerciseLog time-domain fields
            ('duration_seconds', 'ALTER TABLE exercise_logs ADD COLUMN duration_seconds FLOAT'),
            ('time_seconds',     'ALTER TABLE exercise_logs ADD COLUMN time_seconds FLOAT'),
            ('distance',         'ALTER TABLE exercise_logs ADD COLUMN distance FLOAT'),
            ('distance_unit',    'ALTER TABLE exercise_logs ADD COLUMN distance_unit VARCHAR(10)'),
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
    # Admin seeding — uses INITIAL_ADMIN_PASSWORD env var (no hardcoded passwords)
    initial_pw = os.environ.get('INITIAL_ADMIN_PASSWORD')
    admin = User.query.filter_by(email='nick@fitforlife.com').first()
    if not admin:
        if not initial_pw:
            print('WARNING: Set INITIAL_ADMIN_PASSWORD env var to create admin accounts on first run.')
        else:
            admin = User(name='Nick Wells', email='nick@fitforlife.com', role='admin')
            admin.set_password(initial_pw)
            db.session.add(admin)
            db.session.commit()
            print('Admin created: nick@fitforlife.com (password from INITIAL_ADMIN_PASSWORD env var)')
    elif admin.role != 'admin':
        admin.role = 'admin'
        db.session.commit()
    for full_name, email in [
        ('Carrie Cox',   'carrie@fitforlife.com'),
        ('Marie Berry',  'marie@fitforlife.com'),
    ]:
        u = User.query.filter_by(email=email).first()
        if not u and initial_pw:
            u = User(name=full_name, email=email, role='admin')
            u.set_password(initial_pw)
            db.session.add(u)
            db.session.commit()
            print(f'Admin created: {email} (change password after first login)')
        elif u and u.role != 'admin':
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

    # Seed / upsert badge definitions (adds new badges, updates existing ones)
    _seeded = 0
    for b in BADGE_SEED:
        existing = BadgeDefinition.query.filter_by(key=b['key']).first()
        if not existing:
            db.session.add(BadgeDefinition(
                key=b['key'], name=b['name'], description=b['description'],
                icon=b['icon'], category=b['category'], level=b.get('level', 'bronze'),
                threshold=b.get('threshold'), repeatable=b.get('repeatable', False),
            ))
            _seeded += 1
        else:
            existing.name = b['name']
            existing.description = b['description']
            existing.icon = b['icon']
            existing.category = b['category']
            existing.level = b.get('level', 'bronze')
            existing.threshold = b.get('threshold')
            existing.repeatable = b.get('repeatable', False)
    db.session.commit()
    if _seeded:
        print(f'Badge definitions: {_seeded} new badges added ({len(BADGE_SEED)} total).')


with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_ENV') == 'development', port=5001)
