import os, json, uuid
from datetime import datetime, timedelta
from pathlib import Path

import requests
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from passlib.hash import bcrypt
from sqlalchemy import create_engine, Column, String, Integer, DateTime, Text, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker

try:
    from moviepy.editor import ImageClip, concatenate_videoclips
    MOVIEPY_AVAILABLE = True
except Exception:
    MOVIEPY_AVAILABLE = False

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
UPLOADS_DIR = APP_DIR / "uploads"
VIDEOS_DIR = APP_DIR / "videos"
for d in (DATA_DIR, UPLOADS_DIR, VIDEOS_DIR):
    d.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "app.db"
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Trip(Base):
    __tablename__ = "trips"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    name = Column(String, default="My Trip")
    start = Column(String, nullable=False)
    dest = Column(String, nullable=False)
    start_lat = Column(String, nullable=True)
    start_lon = Column(String, nullable=True)
    dest_lat = Column(String, nullable=True)
    dest_lon = Column(String, nullable=True)
    preferences_json = Column(Text, default="{}")
    food_json = Column(Text, default="{}")
    route_json = Column(Text, default="{}")
    itinerary_json = Column(Text, default="[]")
    published = Column(Integer, default=0)
    visibility = Column(String, default="private")
    created_at = Column(DateTime, default=datetime.utcnow)

class Group(Base):
    __tablename__ = "groups"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    visibility = Column(String, default="public")
    created_at = Column(DateTime, default=datetime.utcnow)

class GroupMember(Base):
    __tablename__ = "group_members"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    group_id = Column(String, ForeignKey("groups.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    role = Column(String, default="member")
    created_at = Column(DateTime, default=datetime.utcnow)

class Post(Base):
    __tablename__ = "posts"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    group_id = Column(String, ForeignKey("groups.id"), nullable=True)
    trip_id = Column(String, ForeignKey("trips.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    message = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(engine)

def db():
    return SessionLocal()

def osrm_route(start_lat, start_lon, dest_lat, dest_lon):
    url = f"https://router.project-osrm.org/route/v1/driving/{start_lon},{start_lat};{dest_lon},{dest_lat}"
    params = {"overview":"full","geometries":"geojson","steps":"false"}
    r = requests.get(url, params=params, timeout=25)
    r.raise_for_status()
    data = r.json()
    if not data.get("routes"):
        raise ValueError("No route")
    rt = data["routes"][0]
    return {"distance_m": rt["distance"], "duration_s": rt["duration"], "geometry": rt["geometry"]}

def bbox_from_coords(coords, pad=0.04):
    lats = [c[1] for c in coords]; lons = [c[0] for c in coords]
    return (min(lats)-pad, min(lons)-pad, max(lats)+pad, max(lons)+pad)

def overpass_pois(min_lat, min_lon, max_lat, max_lon):
    query = f"""[out:json][timeout:25];
(
 node[\"amenity\"=\"fuel\"]({min_lat},{min_lon},{max_lat},{max_lon});
 node[\"amenity\"=\"charging_station\"]({min_lat},{min_lon},{max_lat},{max_lon});
 node[\"amenity\"=\"cafe\"]({min_lat},{min_lon},{max_lat},{max_lon});
 node[\"amenity\"=\"restaurant\"]({min_lat},{min_lon},{max_lat},{max_lon});
 node[\"tourism\"]({min_lat},{min_lon},{max_lat},{max_lon});
);
out center 200;"""
    r = requests.post("https://overpass-api.de/api/interpreter", data=query.encode("utf-8"), timeout=40)
    r.raise_for_status()
    data = r.json()
    pois = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        t = None
        if tags.get("amenity") == "fuel": t = "fuel"
        elif tags.get("amenity") == "charging_station": t = "ev"
        elif tags.get("amenity") in ("cafe","restaurant"): t = "food"
        elif tags.get("tourism"): t = "attractions"
        if not t: 
            continue
        name = (tags.get("name") or tags.get("brand") or f"{t.title()} Spot").strip()[:140]
        pois.append({
            "id": f"osm:{el.get('type')}:{el.get('id')}",
            "type": t,
            "name": name,
            "lat": el.get("lat") or (el.get("center") or {}).get("lat"),
            "lon": el.get("lon") or (el.get("center") or {}).get("lon"),
            "tags": tags,
        })
    return pois

def estimate_cost(distance_m, itinerary, vehicle_type, passengers, buffer_pct):
    km = (distance_m or 0)/1000.0
    passengers = max(1, int(passengers or 1))
    buffer_pct = max(0, min(30, int(buffer_pct or 10)))
    if vehicle_type == "ev":
        kwh = km*18/100.0
        fuel = round(kwh*0.45, 2)
    else:
        liters = km*8.5/100.0
        fuel = round(liters*2.05, 2)
    food = round(len([s for s in itinerary if s.get("type")=="food"])*passengers*22.0, 2)
    tickets = round(len([s for s in itinerary if s.get("type")=="attractions"])*passengers*12.0, 2)
    tolls = round(km*(0.06 if vehicle_type!="ev" else 0.05), 2)
    base = fuel + food + tickets + tolls
    buffer = round(base*buffer_pct/100.0, 2)
    return {"distance_km": round(km,1), "fuel_or_charging": fuel, "food": food, "tickets": tickets, "tolls": tolls, "buffer": buffer, "total": round(base+buffer,2), "currency":"AUD"}

def vlog_prompts(template):
    prompts = [
        {"phase":"Start of day","prompt":"Quick selfie before hitting the road"},
        {"phase":"On the road","prompt":"Wide road shot / dashboard view (3s clip)"},
        {"phase":"Scenic stop","prompt":"Landscape photo or slow pan video"},
        {"phase":"Food stop","prompt":"Plate shot + ambience (3s)"},
        {"phase":"Fuel/EV","prompt":"Quick fuel/charging moment"},
        {"phase":"Arrival","prompt":"Arrival clip / smile shot"},
        {"phase":"Night","prompt":"Sunset / hotel view"},
    ]
    if template == "Foodie Trail": prompts[3]["prompt"] = "Plate shot + menu board + ambience (3s)"
    if template == "Family Memories": prompts[0]["prompt"] = "Family selfie + a fun moment (3s)"
    if template == "EV Road Trip": prompts[4]["prompt"] = "Charging shot + connector close-up (3s)"
    return prompts

def create_video_from_images(image_paths, out_path, seconds_per_image=3):
    if not MOVIEPY_AVAILABLE:
        raise RuntimeError("MoviePy not available. Install moviepy and ffmpeg.")
    clips = [ImageClip(str(p)).set_duration(seconds_per_image) for p in image_paths]
    if not clips:
        raise ValueError("No images")
    vid = concatenate_videoclips(clips, method="compose")
    vid.write_videofile(str(out_path), fps=24, codec="libx264", audio=False, verbose=False, logger=None)
    return out_path

app = Flask(__name__)
CORS(app)
app.config["JWT_SECRET_KEY"] = os.environ.get("JWT_SECRET_KEY","dev-secret")
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(days=7)
jwt = JWTManager(app)

@app.get("/")
def home():
    return render_template("index.html")

@app.get("/api/health")
def health():
    return jsonify({"ok": True, "time": datetime.utcnow().isoformat()})

@app.post("/api/auth/register")
def register():
    p = request.get_json(force=True)
    email = (p.get("email") or "").strip().lower()
    name = (p.get("name") or "").strip() or "User"
    password = p.get("password") or ""
    if not email or not password:
        return jsonify({"error":"email and password required"}), 400
    s = db()
    if s.query(User).filter(User.email==email).first():
        return jsonify({"error":"email already exists"}), 409
    u = User(email=email, name=name, password_hash=bcrypt.hash(password))
    s.add(u); s.commit()
    return jsonify({"token": create_access_token(identity=u.id)})

@app.post("/api/auth/login")
def login():
    p = request.get_json(force=True)
    email = (p.get("email") or "").strip().lower()
    password = p.get("password") or ""
    s = db()
    u = s.query(User).filter(User.email==email).first()
    if not u or not bcrypt.verify(password, u.password_hash):
        return jsonify({"error":"invalid credentials"}), 401
    return jsonify({"token": create_access_token(identity=u.id)})

@app.post("/api/trips")
@jwt_required()
def create_trip():
    uid = get_jwt_identity()
    p = request.get_json(force=True)
    if not (p.get("start") and p.get("dest")):
        return jsonify({"error":"start and dest required"}), 400
    s = db()
    t = Trip(
        user_id=uid,
        name=p.get("name") or "My Trip",
        start=p.get("start"),
        dest=p.get("dest"),
        start_lat=str(p.get("start_lat") or ""),
        start_lon=str(p.get("start_lon") or ""),
        dest_lat=str(p.get("dest_lat") or ""),
        dest_lon=str(p.get("dest_lon") or ""),
        preferences_json=json.dumps(p.get("preferences") or {}),
        food_json=json.dumps(p.get("food") or {}),
    )
    s.add(t); s.commit()
    return jsonify({"trip_id": t.id})

@app.get("/api/trips")
@jwt_required()
def list_trips():
    uid = get_jwt_identity()
    s = db()
    trips = s.query(Trip).filter(Trip.user_id==uid).order_by(Trip.created_at.desc()).all()
    return jsonify([{"id":t.id,"name":t.name,"start":t.start,"dest":t.dest} for t in trips])

@app.get("/api/trips/<trip_id>")
@jwt_required()
def get_trip(trip_id):
    uid = get_jwt_identity()
    s = db()
    t = s.query(Trip).filter(Trip.id==trip_id, Trip.user_id==uid).first()
    if not t:
        return jsonify({"error":"not found"}), 404
    return jsonify({
        "id": t.id, "name": t.name, "start": t.start, "dest": t.dest,
        "preferences": json.loads(t.preferences_json or "{}"),
        "food": json.loads(t.food_json or "{}"),
        "route": json.loads(t.route_json or "{}"),
        "itinerary": json.loads(t.itinerary_json or "[]"),
    })

@app.post("/api/trips/<trip_id>/route")
@jwt_required()
def trip_route(trip_id):
    uid = get_jwt_identity()
    p = request.get_json(force=True)
    s = db()
    t = s.query(Trip).filter(Trip.id==trip_id, Trip.user_id==uid).first()
    if not t:
        return jsonify({"error":"not found"}), 404
    slat = float(p.get("start_lat") or t.start_lat or 0)
    slon = float(p.get("start_lon") or t.start_lon or 0)
    dlat = float(p.get("dest_lat") or t.dest_lat or 0)
    dlon = float(p.get("dest_lon") or t.dest_lon or 0)
    if not all([slat,slon,dlat,dlon]):
        return jsonify({"error":"lat/lon required"}), 400
    route = osrm_route(slat,slon,dlat,dlon)
    t.route_json = json.dumps(route)
    s.commit()
    return jsonify(route)

@app.post("/api/trips/<trip_id>/pois")
@jwt_required()
def trip_pois(trip_id):
    uid = get_jwt_identity()
    p = request.get_json(force=True)
    pad = float(p.get("pad") or 0.04)
    s = db()
    t = s.query(Trip).filter(Trip.id==trip_id, Trip.user_id==uid).first()
    if not t:
        return jsonify({"error":"not found"}), 404
    route = json.loads(t.route_json or "{}")
    coords = (route.get("geometry") or {}).get("coordinates") or []
    if not coords:
        return jsonify({"error":"generate route first"}), 400
    min_lat, min_lon, max_lat, max_lon = bbox_from_coords(coords, pad=pad)
    pois = overpass_pois(min_lat, min_lon, max_lat, max_lon)
    return jsonify({"pois": pois})

@app.post("/api/trips/<trip_id>/itinerary")
@jwt_required()
def save_itinerary(trip_id):
    uid = get_jwt_identity()
    p = request.get_json(force=True)
    items = p.get("itinerary") or []
    s = db()
    t = s.query(Trip).filter(Trip.id==trip_id, Trip.user_id==uid).first()
    if not t:
        return jsonify({"error":"not found"}), 404
    t.itinerary_json = json.dumps(items)
    s.commit()
    return jsonify({"ok": True})

@app.post("/api/trips/<trip_id>/estimate")
@jwt_required()
def estimate(trip_id):
    uid = get_jwt_identity()
    p = request.get_json(force=True)
    s = db()
    t = s.query(Trip).filter(Trip.id==trip_id, Trip.user_id==uid).first()
    if not t:
        return jsonify({"error":"not found"}), 404
    route = json.loads(t.route_json or "{}")
    itinerary = json.loads(t.itinerary_json or "[]")
    prefs = json.loads(t.preferences_json or "{}")
    vehicle = p.get("vehicle_type") or prefs.get("vehicle_type") or "petrol"
    passengers = int(p.get("passengers") or prefs.get("passengers") or 1)
    buffer_pct = int(p.get("buffer_pct") or prefs.get("buffer_pct") or 10)
    return jsonify(estimate_cost(route.get("distance_m") or 0, itinerary, vehicle, passengers, buffer_pct))

@app.post("/api/trips/<trip_id>/publish")
@jwt_required()
def publish(trip_id):
    uid = get_jwt_identity()
    s = db()
    t = s.query(Trip).filter(Trip.id==trip_id, Trip.user_id==uid).first()
    if not t:
        return jsonify({"error":"not found"}), 404
    t.published = 1
    t.visibility = "public"
    s.commit()
    return jsonify({"ok": True})

@app.get("/api/feed")
@jwt_required(optional=True)
def feed():
    s = db()
    trips = s.query(Trip).filter(Trip.published==1, Trip.visibility=="public").order_by(Trip.created_at.desc()).limit(50).all()
    return jsonify([{"id":t.id,"name":t.name,"start":t.start,"dest":t.dest} for t in trips])

@app.post("/api/groups")
@jwt_required()
def create_group():
    uid = get_jwt_identity()
    p = request.get_json(force=True)
    name = (p.get("name") or "").strip()
    visibility = p.get("visibility") or "public"
    if not name:
        return jsonify({"error":"name required"}), 400
    s = db()
    g = Group(name=name, visibility=visibility)
    s.add(g); s.commit()
    gm = GroupMember(group_id=g.id, user_id=uid, role="owner")
    s.add(gm); s.commit()
    return jsonify({"id": g.id, "name": g.name, "visibility": g.visibility})

@app.get("/api/groups")
@jwt_required(optional=True)
def list_groups():
    s = db()
    gs = s.query(Group).order_by(Group.created_at.desc()).limit(100).all()
    return jsonify([{"id":g.id,"name":g.name,"visibility":g.visibility} for g in gs])

@app.post("/api/groups/<group_id>/post")
@jwt_required()
def post_to_group(group_id):
    uid = get_jwt_identity()
    p = request.get_json(force=True)
    # Demo: no membership enforcement to keep flow simple
    return jsonify({"ok": True})

@app.get("/api/vlog/prompts")
@jwt_required(optional=True)
def prompts():
    template = request.args.get("template") or "Scenic Explorer"
    return jsonify({"template": template, "prompts": vlog_prompts(template)})

@app.post("/api/vlog/<trip_id>/upload")
@jwt_required()
def vlog_upload(trip_id):
    uid = get_jwt_identity()
    day = int(request.form.get("day") or 1)
    f = request.files.get("file")
    if not f:
        return jsonify({"error":"file required"}), 400
    day_dir = UPLOADS_DIR / trip_id / f"day_{day}"
    day_dir.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(f.filename)[1].lower() or ".jpg"
    fname = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex}{ext}"
    path = day_dir / fname
    f.save(path)
    return jsonify({"ok": True})

@app.post("/api/vlog/<trip_id>/daily")
@jwt_required()
def vlog_daily(trip_id):
    uid = get_jwt_identity()
    p = request.get_json(force=True)
    day = int(p.get("day") or 1)
    sec = float(p.get("seconds_per_image") or 3)
    day_dir = UPLOADS_DIR / trip_id / f"day_{day}"
    images = sorted([x for x in day_dir.iterdir() if x.suffix.lower() in (".jpg",".jpeg",".png",".webp")]) if day_dir.exists() else []
    if not images:
        return jsonify({"error":"no images for this day"}), 400
    out_dir = VIDEOS_DIR / trip_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"day_{day}_recap.mp4"
    create_video_from_images(images, out_path, seconds_per_image=sec)
    return jsonify({"ok": True, "video": f"/videos/{trip_id}/{out_path.name}"})

@app.post("/api/vlog/<trip_id>/final")
@jwt_required()
def vlog_final(trip_id):
    uid = get_jwt_identity()
    p = request.get_json(force=True)
    sec = float(p.get("seconds_per_image") or 2)
    trip_dir = UPLOADS_DIR / trip_id
    if not trip_dir.exists():
        return jsonify({"error":"no uploads"}), 400
    images = []
    for day_dir in sorted(trip_dir.glob("day_*")):
        images.extend(sorted([x for x in day_dir.iterdir() if x.suffix.lower() in (".jpg",".jpeg",".png",".webp")]))
    if not images:
        return jsonify({"error":"no images"}), 400
    out_dir = VIDEOS_DIR / trip_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "final_trip.mp4"
    create_video_from_images(images, out_path, seconds_per_image=sec)
    return jsonify({"ok": True, "video": f"/videos/{trip_id}/{out_path.name}"})

@app.get("/videos/<trip_id>/<filename>")
def serve_video(trip_id, filename):
    return send_from_directory(VIDEOS_DIR / trip_id, filename, as_attachment=False)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
