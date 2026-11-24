import os, secrets, hashlib
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, abort, session
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy import func
from sqlalchemy.engine import make_url, URL

app = Flask(__name__)
DB_CONFIG_ERROR = None
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-before-deploying')
# Railway must use the shared PostgreSQL database. Never silently use a
# separate SQLite database in production.
def get_database_url():
    """Resolve Railway PostgreSQL using common Railway and PostgreSQL env names.

    Never print connection values. If no complete DB configuration exists,
    keep the web process alive only to show the setup/diagnostic page.
    """
    global DB_CONFIG_ERROR
    env_names = (
        'DATABASE_URL', 'DATABASE_PRIVATE_URL', 'DATABASE_PUBLIC_URL',
        'POSTGRES_URL', 'POSTGRESQL_URL', 'POSTGRES_URI',
    )
    raw = ''
    selected_name = None
    for key in env_names:
        value = (os.environ.get(key) or '').strip()
        if value:
            raw, selected_name = value, key
            break

    # Support Railway/Postgres split variables if no URL alias is present.
    if not raw:
        pg_host = os.environ.get('PGHOST') or os.environ.get('POSTGRES_HOST')
        pg_user = os.environ.get('PGUSER') or os.environ.get('POSTGRES_USER')
        pg_password = os.environ.get('PGPASSWORD') or os.environ.get('POSTGRES_PASSWORD')
        pg_database = os.environ.get('PGDATABASE') or os.environ.get('POSTGRES_DB')
        pg_port = os.environ.get('PGPORT') or os.environ.get('POSTGRES_PORT') or '5432'
        if pg_host and pg_user and pg_password is not None and pg_database:
            try:
                return URL.create(
                    drivername='postgresql+psycopg', username=pg_user,
                    password=pg_password, host=pg_host, port=int(pg_port),
                    database=pg_database,
                )
            except Exception:
                DB_CONFIG_ERROR = 'PostgreSQL split variables are present but invalid. Check PGHOST, PGUSER, PGPASSWORD, PGDATABASE and PGPORT.'
                return 'sqlite://'

        detected = [k for k in (*env_names, 'PGHOST', 'PGUSER', 'PGPASSWORD', 'PGDATABASE', 'POSTGRES_HOST', 'POSTGRES_USER', 'POSTGRES_PASSWORD', 'POSTGRES_DB') if os.environ.get(k)]
        DB_CONFIG_ERROR = (
            'No PostgreSQL connection variable is visible to this Web service process. '
            'Expected DATABASE_URL (recommended) or complete PGHOST/PGUSER/PGPASSWORD/PGDATABASE variables. '
            'Detected variable NAMES: ' + (', '.join(detected) if detected else 'none') + '.'
        )
        return 'sqlite://'

    if '${{' in raw or '}}' in raw or raw.lower() in {'postgres', 'postgresql', 'database_url'}:
        DB_CONFIG_ERROR = f'{selected_name} exists but is an unresolved placeholder, not a connection URL.'
        return 'sqlite://'

    if raw.startswith('postgres://'):
        raw = 'postgresql://' + raw[len('postgres://'):]
    try:
        parsed = make_url(raw)
    except Exception:
        DB_CONFIG_ERROR = f'{selected_name} is malformed. Use Railway Add Reference to the PostgreSQL service URL.'
        return 'sqlite://'

    if parsed.drivername in ('postgres', 'postgresql'):
        parsed = parsed.set(drivername='postgresql+psycopg')
    elif parsed.drivername == 'postgresql+psycopg2':
        parsed = parsed.set(drivername='postgresql+psycopg')
    if parsed.drivername not in ('postgresql+psycopg', 'sqlite'):
        DB_CONFIG_ERROR = f'{selected_name} must contain a PostgreSQL connection URL.'
        return 'sqlite://'
    return parsed

app.config['SQLALCHEMY_DATABASE_URI'] = get_database_url()
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('COOKIE_SECURE', '0') == '1'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)
db = SQLAlchemy(app)
csrf = CSRFProtect(app)


def utcnow():
    return datetime.now(timezone.utc)

class Village(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    president_name = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    people = db.relationship('Person', backref='village', lazy=True, cascade='all, delete-orphan')
    links = db.relationship('AccessLink', backref='village', lazy=True, cascade='all, delete-orphan')

class Person(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    village_id = db.Column(db.Integer, db.ForeignKey('village.id'), nullable=False, index=True)
    serial_no = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(160), nullable=False)
    father_name = db.Column(db.String(160), nullable=False)
    address = db.Column(db.String(300), nullable=False)
    gender = db.Column(db.String(20), nullable=False)
    community = db.Column(db.String(120), nullable=True)
    community_consent = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint('village_id', 'serial_no', name='uq_village_serial'),)

class AccessLink(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    village_id = db.Column(db.Integer, db.ForeignKey('village.id'), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    revoked = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    admin_username = db.Column(db.String(80), nullable=True)
    village_id = db.Column(db.Integer, nullable=True)
    action = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


def admin_required(fn):
    # NO-LOGIN MODE requested by the project owner.
    # Admin routes are directly accessible; do not use this deployment on a
    # public/untrusted network because anyone with the URL can manage records.
    @wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)
    return wrapper

def log_action(action, village_id=None):
    db.session.add(AuditLog(admin_username=session.get('admin_username'), village_id=village_id, action=action))
    db.session.commit()

def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()

@app.before_request
def require_database_configuration():
    # If Railway has no valid shared DB reference, never serve data routes from
    # the temporary in-memory SQLite placeholder. Show a helpful setup page.
    if DB_CONFIG_ERROR and request.endpoint not in {'health', 'database_health', 'database_config_health', 'database_setup', 'static'}:
        return render_template('database_setup.html', message=DB_CONFIG_ERROR), 503

@app.route('/setup/database')
def database_setup():
    if not DB_CONFIG_ERROR:
        return redirect(url_for('dashboard'))
    return render_template('database_setup.html', message=DB_CONFIG_ERROR), 503

@app.route('/health')
def health():
    # Liveness check only; does not expose credentials or personal information.
    return {'status': 'ok'}, 200

@app.route('/health/db')
def database_health():
    if DB_CONFIG_ERROR:
        return {'status': 'error', 'database': 'configuration_missing', 'message': DB_CONFIG_ERROR}, 503
    # Railway troubleshooting endpoint: confirms the app can reach its configured DB.
    try:
        db.session.execute(db.text('SELECT 1'))
        return {'status': 'ok', 'database': 'connected'}, 200
    except Exception:
        db.session.rollback()
        app.logger.exception('Database health check failed')
        return {'status': 'error', 'database': 'unavailable'}, 503

@app.route('/health/config')
def database_config_health():
    # Safe diagnostic: report variable NAMES only, never values or credentials.
    names = [
        'DATABASE_URL', 'DATABASE_PRIVATE_URL', 'DATABASE_PUBLIC_URL',
        'POSTGRES_URL', 'POSTGRESQL_URL', 'POSTGRES_URI',
        'PGHOST', 'PGUSER', 'PGPASSWORD', 'PGDATABASE', 'PGPORT',
        'POSTGRES_HOST', 'POSTGRES_USER', 'POSTGRES_PASSWORD', 'POSTGRES_DB',
    ]
    present = [name for name in names if os.environ.get(name)]
    return {
        'database_configured': DB_CONFIG_ERROR is None,
        'recognized_variable_names_present': present,
        'note': 'Only variable names are shown; secret values are never returned.'
    }, 200 if DB_CONFIG_ERROR is None else 503


@app.route('/')
def home():
    return redirect(url_for('dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    # Compatibility redirect: login is disabled in this requested build.
    return redirect(url_for('dashboard'))

@app.route('/logout', methods=['GET', 'POST'])
def logout():
    return redirect(url_for('dashboard'))

@app.route('/admin')
@admin_required
def dashboard():
    # Admin has full access to every village and every person record.
    villages = Village.query.order_by(Village.name).all()
    q = request.args.get('q', '').strip()
    village_id = request.args.get('village_id', type=int)
    gender = request.args.get('gender', '')
    query = Person.query.join(Village)
    if q:
        query = query.filter(db.or_(Person.name.ilike(f'%{q}%'),
                                    Person.father_name.ilike(f'%{q}%'),
                                    Person.address.ilike(f'%{q}%'),
                                    Village.name.ilike(f'%{q}%')))
    if village_id:
        query = query.filter(Person.village_id == village_id)
    if gender in ['Male', 'Female', 'Other']:
        query = query.filter(Person.gender == gender)
    people = query.order_by(Village.name, Person.serial_no).limit(5000).all()
    total = Person.query.count()
    male = Person.query.filter_by(gender='Male').count()
    female = Person.query.filter_by(gender='Female').count()
    other = Person.query.filter_by(gender='Other').count()
    active_links = AccessLink.query.filter(AccessLink.revoked.is_(False), AccessLink.expires_at > utcnow()).count()
    village_stats = db.session.query(Village.id, Village.name, Village.president_name,
                    func.count(Person.id)).outerjoin(Person).group_by(Village.id).order_by(Village.name).all()
    return render_template('dashboard.html', villages=villages, people=people, q=q,
                           village_id=village_id, gender=gender, total=total, male=male,
                           female=female, other=other, active_links=active_links,
                           village_stats=village_stats)

@app.route('/admin/people/add', methods=['POST'])
@admin_required
def admin_add_person():
    village = db.get_or_404(Village, request.form.get('village_id', type=int))
    name = request.form.get('name', '').strip()
    father = request.form.get('father_name', '').strip()
    address = request.form.get('address', '').strip()
    gender = request.form.get('gender', '')
    if not name or not father or not address or gender not in ['Male', 'Female', 'Other']:
        flash('Please complete all required fields.', 'error')
    else:
        last = db.session.query(func.max(Person.serial_no)).filter_by(village_id=village.id).scalar() or 0
        db.session.add(Person(village_id=village.id, serial_no=last + 1, name=name,
                              father_name=father, address=address, gender=gender))
        db.session.commit(); log_action('Admin added person', village.id)
        flash('Person record added.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/admin/people/<int:person_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_edit_person(person_id):
    person = db.get_or_404(Person, person_id)
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        father = request.form.get('father_name', '').strip()
        address = request.form.get('address', '').strip()
        gender = request.form.get('gender', '')
        if not name or not father or not address or gender not in ['Male', 'Female', 'Other']:
            flash('Please complete all required fields.', 'error')
        else:
            person.name, person.father_name, person.address, person.gender = name, father, address, gender
            db.session.commit(); log_action('Admin edited person', person.village_id)
            flash('Record updated.', 'success')
            return redirect(url_for('dashboard'))
    return render_template('edit_person.html', person=person)

@app.route('/admin/people/<int:person_id>/delete', methods=['POST'])
@admin_required
def admin_delete_person(person_id):
    person = db.get_or_404(Person, person_id)
    village_id = person.village_id
    db.session.delete(person); db.session.commit(); log_action('Admin deleted person', village_id)
    flash('Record deleted.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/admin/export.xlsx')
@admin_required
def admin_export_xlsx():
    from io import BytesIO
    from flask import send_file
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.title = 'All Village Records'
    ws.append(['Village', 'President', 'Sl No', 'Name', 'Father Name', 'Address', 'Gender'])
    rows = Person.query.join(Village).order_by(Village.name, Person.serial_no).all()
    for person in rows:
        ws.append([person.village.name, person.village.president_name, person.serial_no,
                   person.name, person.father_name, person.address, person.gender])
    stream = BytesIO(); wb.save(stream); stream.seek(0)
    return send_file(stream, as_attachment=True, download_name='all_village_people.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/admin/villages', methods=['POST'])
@admin_required
def add_village():
    name = request.form.get('name','').strip(); president = request.form.get('president','').strip()
    if not name or not president: flash('Village and president names are required.', 'error')
    elif Village.query.filter(func.lower(Village.name) == name.lower()).first(): flash('This village already exists.', 'error')
    else:
        db.session.add(Village(name=name, president_name=president)); db.session.commit(); log_action('Created village')
        flash('Village created.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/admin/links/<int:village_id>', methods=['POST'])
@admin_required
def create_link(village_id):
    village = db.get_or_404(Village, village_id)
    days = request.form.get('days', '7', type=int)
    days = max(1, min(days, 30))
    raw = secrets.token_urlsafe(32)
    link = AccessLink(village_id=village.id, token_hash=hash_token(raw), expires_at=utcnow()+timedelta(days=days))
    db.session.add(link); db.session.commit(); log_action('Created temporary president link', village.id)
    flash('New link: ' + url_for('president_entry', token=raw, _external=True), 'link')
    return redirect(url_for('dashboard'))

@app.route('/admin/links/<int:link_id>/revoke', methods=['POST'])
@admin_required
def revoke_link(link_id):
    link = db.get_or_404(AccessLink, link_id); link.revoked = True; db.session.commit(); log_action('Revoked president link', link.village_id)
    flash('Link revoked.', 'success'); return redirect(url_for('dashboard'))

@app.route('/admin/villages/<int:village_id>/links')
@admin_required
def village_links(village_id):
    village = db.get_or_404(Village, village_id)
    return render_template('links.html', village=village, now=utcnow())

def get_active_link(token):
    link = AccessLink.query.filter_by(token_hash=hash_token(token)).first()
    if not link or link.revoked or link.expires_at <= utcnow():
        abort(404)
    return link

@app.route('/p/<token>', methods=['GET', 'POST'])
def president_entry(token):
    link = get_active_link(token)
    village = link.village
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        father = request.form.get('father_name', '').strip()
        address = request.form.get('address', '').strip()
        gender = request.form.get('gender', '')
        if not name or not father or not address or gender not in ['Male', 'Female', 'Other']:
            flash('Please complete all required fields.', 'error')
        else:
            last = db.session.query(func.max(Person.serial_no)).filter_by(village_id=village.id).scalar() or 0
            person = Person(village_id=village.id, serial_no=last + 1, name=name,
                            father_name=father, address=address, gender=gender,
                            community=None, community_consent=False)
            db.session.add(person)
            try:
                db.session.commit()
                flash(f'Record saved. Serial No. {person.serial_no}', 'success')
            except Exception:
                db.session.rollback()
                flash('Could not save this record. Please try again.', 'error')

    q = request.args.get('q', '').strip()
    gender = request.args.get('gender', '')
    query = Person.query.filter_by(village_id=village.id)
    if q:
        query = query.filter(db.or_(Person.name.ilike(f'%{q}%'),
                                    Person.father_name.ilike(f'%{q}%'),
                                    Person.address.ilike(f'%{q}%')))
    if gender in ['Male', 'Female', 'Other']:
        query = query.filter(Person.gender == gender)
    people = query.order_by(Person.serial_no).limit(2000).all()
    total = Person.query.filter_by(village_id=village.id).count()
    male = Person.query.filter_by(village_id=village.id, gender='Male').count()
    female = Person.query.filter_by(village_id=village.id, gender='Female').count()
    other = Person.query.filter_by(village_id=village.id, gender='Other').count()
    return render_template('president.html', village=village, people=people, total=total,
                           male=male, female=female, other=other, q=q, gender=gender,
                           expires=link.expires_at, token=token)

@app.route('/p/<token>/export.xlsx')
def president_export_xlsx(token):
    link = get_active_link(token)
    from io import BytesIO
    from flask import send_file
    from openpyxl import Workbook
    people = Person.query.filter_by(village_id=link.village_id).order_by(Person.serial_no).all()
    wb = Workbook()
    ws = wb.active
    ws.title = 'Village Records'
    ws.append(['Sl No', 'Name', 'Father Name', 'Address', 'Gender'])
    for p in people:
        ws.append([p.serial_no, p.name, p.father_name, p.address, p.gender])
    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)
    safe_name = ''.join(c for c in link.village.name if c.isalnum() or c in (' ', '-', '_')).strip() or 'village'
    return send_file(stream, as_attachment=True, download_name=f'{safe_name}_people.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.cli.command('init-db')
def init_db():
    # Verify the configured database before creating tables.
    if DB_CONFIG_ERROR:
        raise RuntimeError(DB_CONFIG_ERROR)
    try:
        db.session.execute(db.text('SELECT 1'))
        db.create_all()  # Creates missing tables; does not drop existing tables or rows.
    except Exception as exc:
        db.session.rollback()
        app.logger.exception('Database initialization failed')
        raise RuntimeError(
            'Could not connect to DATABASE_URL. Check the web service variable reference '
            'to the PostgreSQL service and inspect Railway deploy logs.'
        ) from exc

    print('INIT_DB: database connected; missing tables created; existing tables and records preserved. Admin login is disabled in this build.')

@app.cli.command('reset-admin-password')
def reset_admin_password():
    """Explicitly reset the configured admin password without deleting records."""
    username = os.environ.get('ADMIN_USERNAME', 'admin').strip()
    password = os.environ.get('ADMIN_PASSWORD')
    if not password:
        raise RuntimeError('Set ADMIN_PASSWORD in Railway Variables before running this command.')
    admin = Admin.query.filter_by(username=username).first()
    if not admin:
        raise RuntimeError(f'Admin {username!r} does not exist. Run init-db first.')
    admin.password_hash = generate_password_hash(password)
    db.session.commit()
    print(f'ADMIN_PASSWORD_RESET: password updated for {username!r}.')

@app.after_request
def security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    return response

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
