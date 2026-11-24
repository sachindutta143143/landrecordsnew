import os, secrets, hashlib, csv, io
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template, request, redirect, url_for, flash, abort, Response, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, text
from sqlalchemy.exc import SQLAlchemyError

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 280}
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

def database_url():
    for key in ("DATABASE_URL", "RAILWAY_DATABASE_URL", "POSTGRES_URL", "POSTGRESQL_URL"):
        value = os.environ.get(key, "").strip()
        if value:
            if value.startswith("postgres://"):
                value = "postgresql://" + value[len("postgres://"):]
            if value.startswith("postgresql://") and "+psycopg" not in value:
                value = value.replace("postgresql://", "postgresql+psycopg://", 1)
            return value
    # Support Railway's split PG variables without falling back to ephemeral SQLite.
    host=os.environ.get("PGHOST"); user=os.environ.get("PGUSER")
    password=os.environ.get("PGPASSWORD"); dbname=os.environ.get("PGDATABASE")
    if all((host,user,password,dbname)):
        from urllib.parse import quote_plus
        port=os.environ.get("PGPORT","5432")
        return f"postgresql+psycopg://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{dbname}"
    return None

DB_URL = database_url()
if DB_URL:
    app.config["SQLALCHEMY_DATABASE_URI"] = DB_URL
else:
    # Keep the web process alive so /health/config explains the missing Railway variable.
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:////tmp/vpm-diagnostic-only.db"

db = SQLAlchemy(app)

def now(): return datetime.now(timezone.utc)
def token_hash(value): return hashlib.sha256(value.encode()).hexdigest()

class Village(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    name=db.Column(db.String(120), unique=True, nullable=False)
    president_name=db.Column(db.String(120), nullable=False)
    created_at=db.Column(db.DateTime(timezone=True), default=now, nullable=False)

class Person(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    village_id=db.Column(db.Integer, db.ForeignKey("village.id"), nullable=False, index=True)
    serial_no=db.Column(db.Integer, nullable=False)
    name=db.Column(db.String(160), nullable=False)
    father_name=db.Column(db.String(160), nullable=False)
    address=db.Column(db.String(300), nullable=False)
    gender=db.Column(db.String(20), nullable=False)
    created_at=db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    village=db.relationship("Village")
    __table_args__=(db.UniqueConstraint("village_id","serial_no",name="uq_village_serial"),)

class AccessLink(db.Model):
    id=db.Column(db.Integer, primary_key=True)
    village_id=db.Column(db.Integer, db.ForeignKey("village.id"), nullable=False, index=True)
    token_hash=db.Column(db.String(64), unique=True, nullable=False)
    expires_at=db.Column(db.DateTime(timezone=True), nullable=False)
    revoked=db.Column(db.Boolean, default=False, nullable=False)
    created_at=db.Column(db.DateTime(timezone=True), default=now, nullable=False)
    village=db.relationship("Village")

def db_ready():
    if not DB_URL: return False
    try:
        db.session.execute(text("SELECT 1"))
        return True
    except Exception:
        db.session.rollback()
        return False

@app.get("/health")
def health(): return jsonify(status="ok", database_configured=bool(DB_URL))
@app.get("/health/config")
def health_config():
    keys=("DATABASE_URL","RAILWAY_DATABASE_URL","POSTGRES_URL","POSTGRESQL_URL","PGHOST","PGUSER","PGPASSWORD","PGDATABASE")
    return jsonify(database_configured=bool(DB_URL), recognized_variable_names_present=[k for k in keys if os.environ.get(k)])
@app.get("/health/db")
def health_db():
    if not DB_URL: return jsonify(connected=False,error="database_configuration_missing"),503
    try:
        db.session.execute(text("SELECT 1"))
        return jsonify(connected=True,database="postgresql")
    except Exception as e:
        db.session.rollback()
        return jsonify(connected=False,error=type(e).__name__,detail=str(e)[:300]),503

@app.before_request
def ensure_tables():
    # Create missing tables only after successful PostgreSQL connection; never drop/alter existing tables.
    if request.endpoint in ("health","health_config","health_db"): return
    if DB_URL and not app.config.get("_tables_checked"):
        try:
            db.create_all()
            app.config["_tables_checked"]=True
        except Exception:
            db.session.rollback()

@app.get("/")
def home(): return redirect(url_for("admin"))

@app.get("/admin")
def admin():
    if not db_ready(): return render_template("setup_error.html"), 503
    villages=Village.query.order_by(Village.name).all()
    people_q=Person.query.join(Village)
    q=request.args.get("q","").strip()
    village_id=request.args.get("village",type=int)
    gender=request.args.get("gender","")
    if q: people_q=people_q.filter(db.or_(Person.name.ilike(f"%{q}%"),Person.father_name.ilike(f"%{q}%"),Person.address.ilike(f"%{q}%")))
    if village_id: people_q=people_q.filter(Person.village_id==village_id)
    if gender in ("Male","Female","Other"): people_q=people_q.filter(Person.gender==gender)
    people=people_q.order_by(Village.name,Person.serial_no).limit(2000).all()
    total=Person.query.count()
    stats={g:Person.query.filter_by(gender=g).count() for g in ("Male","Female","Other")}
    summaries=[]
    for v in villages:
        summaries.append({"village":v,"total":Person.query.filter_by(village_id=v.id).count(),
          "male":Person.query.filter_by(village_id=v.id,gender="Male").count(),
          "female":Person.query.filter_by(village_id=v.id,gender="Female").count(),
          "other":Person.query.filter_by(village_id=v.id,gender="Other").count()})
    links=AccessLink.query.order_by(AccessLink.created_at.desc()).all()
    return render_template("admin.html",villages=villages,people=people,total=total,stats=stats,summaries=summaries,links=links,q=q,selected=village_id,gender=gender)

@app.post("/admin/villages")
def add_village():
    if not db_ready(): abort(503)
    name=request.form.get("name","").strip(); president=request.form.get("president_name","").strip()
    if not name or not president: flash("Village aur President name required hai.","error")
    elif Village.query.filter(func.lower(Village.name)==name.lower()).first(): flash("Village already exists.","error")
    else:
        db.session.add(Village(name=name,president_name=president)); db.session.commit(); flash("Village added.","success")
    return redirect(url_for("admin")+"#villages")

@app.post("/admin/villages/<int:vid>/edit")
def edit_village(vid):
    v=db.get_or_404(Village,vid); v.name=request.form.get("name",v.name).strip(); v.president_name=request.form.get("president_name",v.president_name).strip()
    db.session.commit(); flash("Village updated.","success"); return redirect(url_for("admin")+"#villages")

@app.post("/admin/people")
def add_person_admin():
    return save_person(None)

@app.post("/p/<token>/people")
def add_person_president(token):
    link=get_link(token)
    if not link: abort(404)
    return save_person(link.village_id, token)

def save_person(locked_village_id=None, token=None):
    vid=locked_village_id or request.form.get("village_id",type=int)
    v=db.get_or_404(Village,vid)
    name=request.form.get("name","").strip(); father=request.form.get("father_name","").strip()
    address=request.form.get("address","").strip(); gender=request.form.get("gender","")
    if not all((name,father,address)) or gender not in ("Male","Female","Other"):
        flash("Please complete all required fields.","error")
        return redirect(url_for("president",token=token) if token else url_for("admin")+"#people")
    try:
        # Retry-safe serial assignment protected by unique constraint; row lock on village for PostgreSQL.
        if DB_URL: db.session.execute(text("SELECT id FROM village WHERE id=:id FOR UPDATE"),{"id":vid})
        last=db.session.query(func.max(Person.serial_no)).filter_by(village_id=vid).scalar() or 0
        p=Person(village_id=vid,serial_no=last+1,name=name,father_name=father,address=address,gender=gender)
        db.session.add(p); db.session.commit()
        flash(f"Record saved. Serial No. {p.serial_no}","success")
    except Exception:
        db.session.rollback(); flash("Record save nahi hua. Please retry.","error")
    return redirect(url_for("president",token=token) if token else url_for("admin")+"#people")

@app.post("/admin/people/<int:pid>/edit")
def edit_person(pid):
    p=db.get_or_404(Person,pid)
    p.name=request.form.get("name",p.name).strip(); p.father_name=request.form.get("father_name",p.father_name).strip()
    p.address=request.form.get("address",p.address).strip(); p.gender=request.form.get("gender",p.gender)
    db.session.commit(); flash("Record updated.","success"); return redirect(url_for("admin")+"#people")

@app.post("/admin/people/<int:pid>/delete")
def delete_person(pid):
    p=db.get_or_404(Person,pid); db.session.delete(p); db.session.commit(); flash("Record deleted.","success"); return redirect(url_for("admin")+"#people")

@app.post("/admin/links/<int:vid>")
def create_link(vid):
    v=db.get_or_404(Village,vid); raw=secrets.token_urlsafe(32)
    link=AccessLink(village_id=vid,token_hash=token_hash(raw),expires_at=now()+timedelta(days=max(1,min(request.form.get("days",7,type=int),30))))
    db.session.add(link); db.session.commit()
    flash("President link (copy now): "+url_for("president",token=raw,_external=True),"success")
    return redirect(url_for("admin")+"#links")

@app.post("/admin/links/<int:lid>/revoke")
def revoke_link(lid):
    link=db.get_or_404(AccessLink,lid); link.revoked=True; db.session.commit(); return redirect(url_for("admin")+"#links")

def get_link(token):
    link=AccessLink.query.filter_by(token_hash=token_hash(token)).first()
    if not link or link.revoked or link.expires_at<=now(): return None
    return link

@app.route("/p/<token>",methods=["GET"])
def president(token):
    if not db_ready(): return render_template("setup_error.html"),503
    link=get_link(token)
    if not link: abort(404)
    v=link.village
    q=request.args.get("q","").strip(); gender=request.args.get("gender","")
    query=Person.query.filter_by(village_id=v.id)
    if q: query=query.filter(db.or_(Person.name.ilike(f"%{q}%"),Person.father_name.ilike(f"%{q}%"),Person.address.ilike(f"%{q}%")))
    if gender in ("Male","Female","Other"): query=query.filter_by(gender=gender)
    people=query.order_by(Person.serial_no).all()
    stats={g:Person.query.filter_by(village_id=v.id,gender=g).count() for g in ("Male","Female","Other")}
    return render_template("president_connected.html",village=v,people=people,stats=stats,link=link,q=q,gender=gender)

@app.get("/admin/export.csv")
def export_admin():
    out=io.StringIO(); w=csv.writer(out); w.writerow(["Village","Serial No","Name","Father Name","Address","Gender"])
    for p in Person.query.join(Village).order_by(Village.name,Person.serial_no).all():
        w.writerow([p.village.name,p.serial_no,p.name,p.father_name,p.address,p.gender])
    return Response("\ufeff"+out.getvalue(),mimetype="text/csv",headers={"Content-Disposition":"attachment; filename=all_village_people.csv"})

@app.get("/p/<token>/export.csv")
def export_president(token):
    link=get_link(token)
    if not link: abort(404)
    out=io.StringIO(); w=csv.writer(out); w.writerow(["Serial No","Name","Father Name","Address","Gender"])
    for p in Person.query.filter_by(village_id=link.village_id).order_by(Person.serial_no).all():
        w.writerow([p.serial_no,p.name,p.father_name,p.address,p.gender])
    return Response("\ufeff"+out.getvalue(),mimetype="text/csv",headers={"Content-Disposition":"attachment; filename=village_people.csv"})

if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.environ.get("PORT",8080)))
