from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import json
import math
import os
from datetime import datetime, timedelta
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

app = Flask(__name__)
app.secret_key = "super_secret_key"
LOGIN_FILE = "users.json"

JSON_FILE = "tracked_drivers.json"
USERS_FILE = "user_assignments.json"
GEOCODE_CACHE_FILE = "geo_cache.json"

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_ROUTE_URL = "https://router.project-osrm.org/route/v1/driving"
OSRM_TABLE_URL = "https://router.project-osrm.org/table/v1/driving"
HTTP_HEADERS = {
    "User-Agent": "dispatch-system/1.0 (+dispatch-dashboard)"
}


def load_data():
    if not os.path.exists(JSON_FILE):
        return {}

    with open(JSON_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        normalized = {}
        for index, driver in enumerate(data):
            if not isinstance(driver, dict):
                continue

            driver_key = (
                str(driver.get("driver_key", "")).strip()
                or str(driver.get("driver_name", "")).strip()
                or str(driver.get("name", "")).strip()
                or f"driver_{index}"
            )
            normalized[driver_key] = driver
        data = normalized
    elif not isinstance(data, dict):
        return {}

    for _, driver in data.items():
        if not isinstance(driver, dict):
            continue
        driver.setdefault("delivery_address", "")
        driver.setdefault("appt_time", "")
        driver.setdefault("eta", "")
        driver.setdefault("eta_status", "")
        driver.setdefault("eta_delay_minutes", 0)
        driver.setdefault("eta_delay_text", "")
        driver.setdefault("distance_miles", "")
        driver.setdefault("notes", "")
        driver.setdefault("alerted", False)
        driver.setdefault("minutes", 0)
        driver.setdefault("location", "")
        driver.setdefault("status", "")
        driver.setdefault("name", "")
        driver.setdefault("vehicle", "")

    return data


def save_data(data):
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_users():
    if not os.path.exists(USERS_FILE):
        default_users = {
            "Bobur": [],
            "dispatcher1": [],
            "dispatcher2": []
        }
        save_users(default_users)
        return default_users

    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)


def load_login_users():
    if not os.path.exists(LOGIN_FILE):
        return {}

    with open(LOGIN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_geo_cache():
    if not os.path.exists(GEOCODE_CACHE_FILE):
        return {}

    with open(GEOCODE_CACHE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_geo_cache(cache):
    with open(GEOCODE_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def clean_location(location):
    location = str(location).strip()
    if " from " in location:
        return location.split(" from ", 1)[1].strip()
    return location


def pretty_city_case(text):
    if not text:
        return ""
    return ", ".join(part.strip().title() for part in str(text).split(","))


def get_risk(status, minutes):
    status = str(status).upper()
    minutes = int(minutes or 0)

    if "DRIVING" in status:
        return "good"
    if "SLEEPER" in status:
        return "rest"
    if "ON DUTY" in status:
        return "warning"
    if "OFF" in status:
        if minutes >= 60:
            return "danger"
        if minutes >= 30:
            return "warning"
        return "rest"
    return "unknown"


def parse_appt_time(appt_time_raw):
    if not appt_time_raw:
        return None

    appt_time_raw = appt_time_raw.strip()
    formats = [
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%m/%d/%Y %H:%M",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(appt_time_raw, fmt)
        except ValueError:
            pass

    return None


def format_eta(dt_obj):
    if not dt_obj:
        return ""
    return dt_obj.strftime("%m/%d %I:%M %p")


def format_delay_text(diff_minutes):
    diff_minutes = int(round(diff_minutes or 0))
    if diff_minutes <= 0:
        return ""

    hours, minutes = divmod(diff_minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return "Late by " + " ".join(parts)


def get_eta_status(eta_dt, appt_dt):
    if not eta_dt or not appt_dt:
        return "", "", 0

    diff_minutes = int(round((eta_dt - appt_dt).total_seconds() / 60))

    if diff_minutes > 15:
        return "LATE", format_delay_text(diff_minutes), diff_minutes
    if diff_minutes >= -30:
        return "CLOSE", "", diff_minutes
    return "ON TIME", "", diff_minutes


def simple_eta(distance_miles):
    try:
        distance = float(distance_miles)
    except Exception:
        return None, None

    speed = 60
    hours = distance / speed
    eta_dt = datetime.now() + timedelta(hours=hours)
    return eta_dt, format_eta(eta_dt)


def normalize_cache_key(text):
    return " ".join(str(text).strip().lower().split())


def fetch_json(url, params=None):
    try:
        final_url = url
        if params:
            final_url = f"{url}?{urlencode(params)}"
        request_obj = Request(final_url, headers=HTTP_HEADERS)
        with urlopen(request_obj, timeout=15) as response:
            return json.load(response)
    except Exception:
        return None


def suggest_addresses(query, limit=5):
    query = str(query or "").strip()
    if len(query) < 3:
        return []

    data = fetch_json(
        NOMINATIM_URL,
        {
            "q": query,
            "format": "jsonv2",
            "addressdetails": 1,
            "limit": limit,
        },
    )

    suggestions = []
    seen = set()

    for item in data or []:
        label = str(item.get("display_name", "")).strip()
        if not label:
            continue
        normalized = normalize_cache_key(label)
        if normalized in seen:
            continue
        seen.add(normalized)
        suggestions.append({"label": label})

    return suggestions


def geocode_address(address):
    address = str(address or "").strip()
    if not address:
        return None

    cache = load_geo_cache()
    cache_key = normalize_cache_key(address)
    cached = cache.get(cache_key)
    if cached:
        return cached

    data = fetch_json(
        NOMINATIM_URL,
        {
            "q": address,
            "format": "jsonv2",
            "addressdetails": 1,
            "limit": 1,
        },
    )
    if not data:
        return None

    item = data[0]
    result = {
        "lat": float(item["lat"]),
        "lon": float(item["lon"]),
        "display_name": item.get("display_name", address),
    }

    cache[cache_key] = result
    save_geo_cache(cache)
    return result


def get_route_metrics(origin, destination):
    if not origin or not destination:
        return None

    coordinates = f"{origin['lon']},{origin['lat']};{destination['lon']},{destination['lat']}"
    data = fetch_json(
        f"{OSRM_ROUTE_URL}/{quote(coordinates, safe=';,.-0123456789')}",
        {"overview": "false"},
    )
    if not data or data.get("code") != "Ok" or not data.get("routes"):
        return None

    route = data["routes"][0]
    duration_seconds = int(route.get("duration", 0))
    distance_miles = route.get("distance", 0) / 1609.344

    return {
        "duration_seconds": duration_seconds,
        "distance_miles": round(distance_miles, 1),
    }


def haversine_miles(coord_a, coord_b):
    lat1 = math.radians(coord_a["lat"])
    lon1 = math.radians(coord_a["lon"])
    lat2 = math.radians(coord_b["lat"])
    lon2 = math.radians(coord_b["lon"])

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return 3958.7613 * c


def get_table_metrics(source_coords, destination_coord):
    if not source_coords or not destination_coord:
        return None

    coordinates = source_coords + [destination_coord]
    coord_string = ";".join(
        f"{coord['lon']},{coord['lat']}" for coord in coordinates
    )
    source_indexes = ";".join(str(index) for index in range(len(source_coords)))
    destination_index = str(len(source_coords))

    data = fetch_json(
        f"{OSRM_TABLE_URL}/{quote(coord_string, safe=';,.-0123456789')}",
        {
            "sources": source_indexes,
            "destinations": destination_index,
            "annotations": "duration,distance",
        },
    )
    if not data or data.get("code") != "Ok":
        return None

    durations = data.get("durations", [])
    distances = data.get("distances", [])
    metrics = []

    for index in range(len(source_coords)):
        duration_row = durations[index] if index < len(durations) else []
        distance_row = distances[index] if index < len(distances) else []
        duration_seconds = duration_row[0] if duration_row else None
        distance_meters = distance_row[0] if distance_row else None
        metrics.append(
            {
                "duration_seconds": int(duration_seconds) if duration_seconds is not None else None,
                "distance_miles": round(distance_meters / 1609.344, 1) if distance_meters is not None else None,
            }
        )

    return metrics


def build_route_eta(driver_location, delivery_address):
    origin = geocode_address(clean_location(driver_location))
    destination = geocode_address(delivery_address)
    if not origin or not destination:
        return None

    route_metrics = get_route_metrics(origin, destination)
    if not route_metrics:
        return None

    eta_dt = datetime.now() + timedelta(seconds=route_metrics["duration_seconds"])
    return {
        "eta_dt": eta_dt,
        "eta": format_eta(eta_dt),
        "distance_miles": route_metrics["distance_miles"],
    }


def build_drivers():
    data = load_data()
    drivers = []

    for key, d in data.items():
        driver = d.copy()
        driver["driver_key"] = key
        driver["location"] = pretty_city_case(clean_location(driver.get("location", "")))
        driver["risk"] = get_risk(driver.get("status", ""), driver.get("minutes", 0))
        drivers.append(driver)

    return drivers


def find_nearest_matches(address, drivers):
    destination = geocode_address(address)
    if not destination:
        return None

    prepared = []
    for driver in drivers:
        location_query = clean_location(driver.get("location", ""))
        if not location_query:
            continue

        origin = geocode_address(location_query)
        if not origin:
            continue

        prepared.append(
            {
                "driver": driver,
                "origin": origin,
            }
        )

    if not prepared:
        return []

    matches = []
    chunk_size = 40

    for chunk_start in range(0, len(prepared), chunk_size):
        chunk = prepared[chunk_start:chunk_start + chunk_size]
        source_coords = [item["origin"] for item in chunk]
        table_metrics = get_table_metrics(source_coords, destination)

        for index, item in enumerate(chunk):
            metrics = table_metrics[index] if table_metrics and index < len(table_metrics) else None
            if metrics and metrics["distance_miles"] is not None:
                distance_miles = metrics["distance_miles"]
                duration_minutes = int(round((metrics["duration_seconds"] or 0) / 60))
            else:
                distance_miles = round(haversine_miles(item["origin"], destination), 1)
                duration_minutes = int(round((distance_miles / 55) * 60))

            matches.append(
                {
                    "driver_key": item["driver"]["driver_key"],
                    "distance_miles": distance_miles,
                    "duration_minutes": duration_minutes,
                }
            )

    matches.sort(key=lambda item: item["distance_miles"])
    return matches


def require_login():
    return "user" in session


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        users = load_login_users()

        if username in users and users[username] == password:
            session["user"] = username
            return redirect(url_for("all_drivers"))

        return render_template("login.html", error="Wrong login")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))


@app.route("/")
def dashboard():
    if not require_login():
        return redirect(url_for("login"))
    return redirect(url_for("all_drivers"))


@app.route("/all-drivers")
def all_drivers():
    if not require_login():
        return redirect(url_for("login"))

    drivers = build_drivers()
    users = load_users()
    selected_user = session["user"]
    users.setdefault(selected_user, [])

    return render_template(
        "index.html",
        drivers=drivers,
        users=users,
        selected_user=selected_user,
        current_page="all"
    )


@app.route("/my-drivers")
def my_drivers():
    if not require_login():
        return redirect(url_for("login"))

    users = load_users()
    selected_user = session["user"]
    users.setdefault(selected_user, [])

    assigned_keys = set(users.get(selected_user, []))
    all_data = build_drivers()
    drivers = [d for d in all_data if d["driver_key"] in assigned_keys]

    return render_template(
        "index.html",
        drivers=drivers,
        users=users,
        selected_user=selected_user,
        current_page="my"
    )


@app.route("/at-risk")
def at_risk():
    if not require_login():
        return redirect(url_for("login"))

    users = load_users()
    selected_user = session["user"]
    users.setdefault(selected_user, [])

    drivers = build_drivers()
    risk_drivers = []

    for d in drivers:
        status = str(d.get("status", "")).upper()
        minutes = int(d.get("minutes", 0))
        eta_status = str(d.get("eta_status", "")).upper()
        appt = d.get("appt_time", "")
        distance = str(d.get("distance_miles", "")).strip()

        is_risk = False

        if "OFF" in status and minutes >= 60:
            is_risk = True
        if eta_status == "LATE":
            is_risk = True
        if "OFF" in status and minutes >= 30:
            is_risk = True
        if eta_status == "CLOSE":
            is_risk = True
        if appt and not distance:
            is_risk = True

        if is_risk:
            risk_drivers.append(d)

    return render_template(
        "index.html",
        drivers=risk_drivers,
        users=users,
        selected_user=selected_user,
        current_page="risk"
    )


@app.route("/assign-driver", methods=["POST"])
def assign_driver():
    if not require_login():
        return redirect(url_for("login"))

    selected_user = session["user"]
    driver_key = request.form.get("driver_key", "").strip()

    users = load_users()
    users.setdefault(selected_user, [])

    if driver_key and driver_key not in users[selected_user]:
        users[selected_user].append(driver_key)
        save_users(users)

    next_page = request.form.get("next_page", "all")
    if next_page == "risk":
        return redirect(url_for("at_risk"))
    return redirect(url_for("all_drivers"))


@app.route("/remove-driver", methods=["POST"])
def remove_driver():
    if not require_login():
        return redirect(url_for("login"))

    selected_user = session["user"]
    driver_key = request.form.get("driver_key", "").strip()

    users = load_users()
    users.setdefault(selected_user, [])

    if driver_key in users[selected_user]:
        users[selected_user].remove(driver_key)
        save_users(users)

    return redirect(url_for("my_drivers"))


@app.route("/address-suggest")
def address_suggest():
    if not require_login():
        return jsonify({"success": False, "error": "Not logged in"}), 401

    query = request.args.get("q", "").strip()
    return jsonify({
        "success": True,
        "suggestions": suggest_addresses(query),
    })


@app.route("/nearest-drivers")
def nearest_drivers():
    if not require_login():
        return jsonify({"success": False, "error": "Not logged in"}), 401

    address = request.args.get("address", "").strip()
    if not address:
        return jsonify({"success": False, "error": "Address is required"}), 400

    drivers = build_drivers()
    matches = find_nearest_matches(address, drivers)
    if matches is None:
        return jsonify({"success": False, "error": "Address not found"}), 404

    return jsonify({
        "success": True,
        "address": address,
        "matches": matches,
    })


@app.route("/autosave", methods=["POST"])
def autosave():
    if not require_login():
        return jsonify({"success": False, "error": "Not logged in"}), 401

    driver_key = request.form.get("driver_key", "").strip()
    delivery_address = request.form.get("delivery_address", "").strip()
    appt_time = request.form.get("appt_time", "").strip()
    distance_input = request.form.get("distance_miles", "").strip()
    notes = request.form.get("notes", "").strip()

    data = load_data()

    if driver_key not in data:
        return jsonify({"success": False, "error": "Driver not found"}), 404

    driver = data[driver_key]
    driver["delivery_address"] = delivery_address
    driver["appt_time"] = appt_time
    driver["notes"] = notes
    driver["eta"] = ""
    driver["eta_status"] = ""
    driver["eta_delay_minutes"] = 0
    driver["eta_delay_text"] = ""

    appt_dt = parse_appt_time(appt_time)
    route_eta = None

    if delivery_address:
        route_eta = build_route_eta(driver.get("location", ""), delivery_address)

    if route_eta:
        driver["distance_miles"] = str(route_eta["distance_miles"])
        driver["eta"] = route_eta["eta"]
        eta_status, eta_delay_text, eta_delay_minutes = get_eta_status(route_eta["eta_dt"], appt_dt)
        driver["eta_status"] = eta_status
        driver["eta_delay_text"] = eta_delay_text
        driver["eta_delay_minutes"] = eta_delay_minutes
    else:
        driver["distance_miles"] = distance_input
        if distance_input:
            eta_dt, eta_str = simple_eta(distance_input)
            if eta_dt:
                driver["eta"] = eta_str
                eta_status, eta_delay_text, eta_delay_minutes = get_eta_status(eta_dt, appt_dt)
                driver["eta_status"] = eta_status
                driver["eta_delay_text"] = eta_delay_text
                driver["eta_delay_minutes"] = eta_delay_minutes
            else:
                driver["eta"] = "Invalid distance"
                driver["eta_status"] = "ERROR"

    save_data(data)

    return jsonify({
        "success": True,
        "eta": driver["eta"],
        "eta_status": driver["eta_status"],
        "eta_delay_text": driver["eta_delay_text"],
        "distance_miles": driver["distance_miles"],
        "route_used": bool(route_eta),
    })


if __name__ == "__main__":
    app.run(debug=True)
