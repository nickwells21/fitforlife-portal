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
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    sessions_as_trainer = db.relationship('Session', foreign_keys='Session.trainer_id', backref='trainer', lazy='dynamic')
    sessions_as_client = db.relationship('Session', foreign_keys='Session.client_id', backref='client', lazy='dynamic')
    packages = db.relationship('SessionPackage', backref='client_user', lazy='dynamic')

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
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    @property
    def end_time(self):
        return self.scheduled_at + timedelta(minutes=self.duration)


class SessionPackage(db.Model):
    __tablename__ = 'session_packages'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    month = db.Column(db.Integer, nullable=False)
    year = db.Column(db.Integer, nullable=False)
    sessions_purchased = db.Column(db.Integer, nullable=False)
    notes = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


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


def get_month_balance(client_id, month, year):
    """Returns (purchased, completed, remaining) for a client in a given month."""
    package = SessionPackage.query.filter_by(
        client_id=client_id, month=month, year=year
    ).first()
    purchased = package.sessions_purchased if package else 0

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
            Session.scheduled_at >= datetime(week_start.year, week_start.month, week_start.day, tzinfo=timezone.utc),
            Session.scheduled_at < datetime(week_end.year, week_end.month, week_end.day, tzinfo=timezone.utc),
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
        upcoming = Session.query.filter(
            Session.trainer_id == current_user.id,
            Session.status == 'scheduled',
            Session.scheduled_at >= now
        ).order_by(Session.scheduled_at).limit(10).all()

        today_sessions = Session.query.filter(
            Session.trainer_id == current_user.id,
            Session.status != 'cancelled',
            db.func.date(Session.scheduled_at) == today
        ).order_by(Session.scheduled_at).all()

        all_clients = User.query.filter_by(role='client', is_active=True).order_by(User.name).all()

        return render_template('dashboard_trainer.html',
            upcoming=upcoming,
            today_sessions=today_sessions,
            all_clients=all_clients,
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

        today_sessions = Session.query.filter(
            db.func.date(Session.scheduled_at) == today,
            Session.status != 'cancelled'
        ).order_by(Session.scheduled_at).all()

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
    trainers = User.query.filter_by(role='trainer', is_active=True).all()
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
        query = query.filter(Session.client_id == current_user.id)

    sessions = query.all()
    events = []
    for s in sessions:
        color = STATUS_COLORS.get(s.status) or s.location.color
        events.append({
            'id': s.id,
            'title': f'{s.trainer.name} + {s.client.name}',
            'start': s.scheduled_at.isoformat(),
            'end': s.end_time.isoformat(),
            'color': color,
            'extendedProps': {
                'location': s.location.name,
                'location_id': s.location_id,
                'trainer': s.trainer.name,
                'trainer_id': s.trainer_id,
                'client': s.client.name,
                'status': s.status,
                'notes': s.notes or '',
                'session_id': s.id,
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
            'trainer': c.trainer.name,
            'client': c.client.name,
            'location': c.location.name,
            'start': c.scheduled_at.strftime('%I:%M %p'),
            'end': c.end_time.strftime('%I:%M %p'),
            'conflict_type': ' & '.join(reason),
        })
    return jsonify({'conflicts': result})


# ─── Sessions ────────────────────────────────────────────────────────────────

@app.route('/sessions/new', methods=['GET', 'POST'])
@staff_required
def new_session():
    trainers = User.query.filter_by(role='trainer', is_active=True).all()
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
        conflicts = check_conflict(trainer_id, location_id, scheduled_at, end_dt)

        if conflicts:
            msgs = []
            for c in conflicts:
                t = 'Trainer' if c.trainer_id == trainer_id else 'Location'
                msgs.append(f'{t} conflict: {c.trainer.name} + {c.client.name} at {c.location.name} ({c.scheduled_at.strftime("%I:%M %p")})')
            flash('Conflict detected — ' + '; '.join(msgs), 'danger')
        else:
            sess = Session(
                trainer_id=trainer_id,
                client_id=client_id,
                location_id=location_id,
                scheduled_at=scheduled_at,
                duration=duration,
                notes=notes,
                created_by_id=current_user.id
            )
            db.session.add(sess)
            db.session.commit()
            flash('Session booked!', 'success')
            return redirect(url_for('calendar_view'))

    prefill_date = request.args.get('date', '')
    prefill_trainer = request.args.get('trainer_id', str(current_user.id) if current_user.role == 'trainer' else '')

    return render_template('session_form.html',
        sess=None,
        trainers=trainers,
        clients=clients,
        locations=locations,
        prefill_date=prefill_date,
        prefill_trainer=prefill_trainer
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
    trainers = User.query.filter_by(role='trainer', is_active=True).all()
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

        if conflicts:
            msgs = [f'{c.trainer.name} + {c.client.name} at {c.location.name}' for c in conflicts]
            flash('Conflict: ' + ', '.join(msgs), 'danger')
        else:
            sess.trainer_id = trainer_id
            sess.client_id = client_id
            sess.location_id = location_id
            sess.scheduled_at = scheduled_at
            sess.duration = duration
            sess.notes = notes
            db.session.commit()
            flash('Session updated.', 'success')
            return redirect(url_for('session_detail', session_id=sess.id))

    return render_template('session_form.html',
        sess=sess,
        trainers=trainers,
        clients=clients,
        locations=locations,
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


# ─── Clients ─────────────────────────────────────────────────────────────────

@app.route('/clients')
@staff_required
def clients():
    now = datetime.now(timezone.utc)
    month, year = now.month, now.year
    all_clients = User.query.filter_by(role='client', is_active=True).order_by(User.name).all()

    client_data = []
    for c in all_clients:
        p, comp, rem = get_month_balance(c.id, month, year)
        next_sess = Session.query.filter(
            Session.client_id == c.id,
            Session.status == 'scheduled',
            Session.scheduled_at >= now
        ).order_by(Session.scheduled_at).first()
        client_data.append({'client': c, 'purchased': p, 'completed': comp, 'remaining': rem, 'next_session': next_sess})

    return render_template('clients.html', client_data=client_data, month_name=month_name[month])


@app.route('/clients/<int:client_id>')
@login_required
def client_detail(client_id):
    if current_user.role == 'client' and current_user.id != client_id:
        abort(403)
    client = User.query.get_or_404(client_id)
    if client.role != 'client':
        abort(404)

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

@app.route('/admin/users')
@admin_required
def admin_users():
    users = User.query.order_by(User.role, User.name).all()
    return render_template('admin_users.html', users=users)


@app.route('/admin/users/new', methods=['GET', 'POST'])
@admin_required
def admin_new_user():
    if request.method == 'POST':
        name = request.form['name'].strip()
        email = request.form['email'].strip().lower()
        role = request.form['role']
        password = request.form['password']
        trainerize_url = request.form.get('trainerize_url', '').strip() or None

        if User.query.filter_by(email=email).first():
            flash('Email already in use.', 'danger')
        else:
            user = User(name=name, email=email, role=role, trainerize_url=trainerize_url)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash(f'{role.capitalize()} account created for {name}.', 'success')
            return redirect(url_for('admin_users'))
    return render_template('admin_user_form.html', user=None)


@app.route('/admin/users/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_edit_user(user_id):
    user = User.query.get_or_404(user_id)
    if request.method == 'POST':
        user.name = request.form['name'].strip()
        user.email = request.form['email'].strip().lower()
        user.role = request.form['role']
        user.trainerize_url = request.form.get('trainerize_url', '').strip() or None
        user.is_active = 'is_active' in request.form
        if request.form.get('password'):
            user.set_password(request.form['password'])
        db.session.commit()
        flash('User updated.', 'success')
        return redirect(url_for('admin_users'))
    return render_template('admin_user_form.html', user=user)


# ─── Admin: Packages ─────────────────────────────────────────────────────────

@app.route('/admin/packages')
@admin_required
def admin_packages():
    now = datetime.now(timezone.utc)
    packages = SessionPackage.query.order_by(
        SessionPackage.year.desc(), SessionPackage.month.desc()
    ).all()
    clients = User.query.filter_by(role='client', is_active=True).order_by(User.name).all()
    return render_template('admin_packages.html',
        packages=packages, clients=clients,
        current_month=now.month, current_year=now.year,
        month_name=month_name
    )


@app.route('/admin/packages/new', methods=['POST'])
@admin_required
def admin_new_package():
    client_id = int(request.form['client_id'])
    month = int(request.form['month'])
    year = int(request.form['year'])
    sessions_purchased = int(request.form['sessions_purchased'])
    notes = request.form.get('notes', '').strip()

    existing = SessionPackage.query.filter_by(client_id=client_id, month=month, year=year).first()
    if existing:
        existing.sessions_purchased = sessions_purchased
        existing.notes = notes
        db.session.commit()
        flash('Package updated.', 'success')
    else:
        pkg = SessionPackage(
            client_id=client_id, month=month, year=year,
            sessions_purchased=sessions_purchased, notes=notes
        )
        db.session.add(pkg)
        db.session.commit()
        flash('Package created.', 'success')
    return redirect(url_for('admin_packages'))


@app.route('/admin/packages/<int:pkg_id>/delete', methods=['POST'])
@admin_required
def admin_delete_package(pkg_id):
    pkg = SessionPackage.query.get_or_404(pkg_id)
    db.session.delete(pkg)
    db.session.commit()
    flash('Package deleted.', 'success')
    return redirect(url_for('admin_packages'))


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
    return render_template('admin_events.html', events=events, locations=locations)


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
    if not Location.query.first():
        db.session.add_all([
            Location(name='West Mobile', color='#7c3aed'),
            Location(name='Midtown', color='#0891b2'),
        ])
        db.session.commit()
        print('Locations seeded.')
    if not User.query.filter_by(role='admin').first():
        admin = User(name='Nick Wells', email='nick@fitforlife.com', role='admin')
        admin.set_password('changeme')
        db.session.add(admin)
        db.session.commit()
        print('Admin created: nick@fitforlife.com / changeme — CHANGE THIS PASSWORD!')
    clients_seed = [
        ('Demo Client',          'client@fitforlife.com',        'Client123'),
        ('Akennya Barnes',       'ABarnes251@fitforlife.com',    'Barnes2026'),
        ('Alison Herlihy',       'AHerlihy251@fitforlife.com',   'Herlihy2026'),
        ('Andrew Sprinkle',      'ASprinkle251@fitforlife.com',  'Sprinkle2026'),
        ('Chad Hansen',          'CHansen251@fitforlife.com',    'Hansen2026'),
        ('Charlene Shaw',        'CShaw251@fitforlife.com',      'Shaw2026'),
        ('Clare McConnell',      'CMcConnell251@fitforlife.com', 'McConnell2026'),
        ('Debbie McGowin',       'DMcGowin251@fitforlife.com',   'McGowin2026'),
        ('Jennifer Morgan',      'JMorgan251@fitforlife.com',    'Morgan2026'),
        ('Kari Bradham',         'KBradham251@fitforlife.com',   'Bradham2026'),
        ('Mary Alice Mathison',  'MMathison251@fitforlife.com',  'Mathison2026'),
        ('Noah Pittman',         'NPittman251@fitforlife.com',   'Pittman2026'),
        ('Peyton Atkins',        'PAtkins251@fitforlife.com',    'Atkins2026'),
        ('Rebekka Dyess',        'RDyess251@fitforlife.com',     'Dyess2026'),
        ('Ruth Pappas',          'RPappas251@fitforlife.com',    'Pappas2026'),
        ('Sarah Williams',       'SWilliams251@fitforlife.com',  'Williams2026'),
        ('Shea Sadler',          'SSadler251@fitforlife.com',    'Sadler2026'),
    ]
    for name, email, password in clients_seed:
        if not User.query.filter_by(email=email).first():
            u = User(name=name, email=email, role='client')
            u.set_password(password)
            db.session.add(u)
    db.session.commit()
    print('Client accounts seeded.')


with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(debug=True, port=5001)
