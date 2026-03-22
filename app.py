from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import json
import os
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = "super_secret_key"
LOGIN_FILE = "users.json"

JSON_FILE = "tracked_drivers.json"
USERS_FILE = "user_assignments.json"


def load_data():
    if not os.path.exists(JSON_FILE):
        return {}

    with open(JSON_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    for _, driver in data.items():
        driver.setdefault("delivery_address", "")
        driver.setdefault("appt_time", "")
        driver.setdefault("eta", "")
        driver.setdefault("eta_status", "")
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
    elif "SLEEPER" in status:
        return "rest"
    elif "ON DUTY" in status:
        return "warning"
    elif "OFF" in status:
        if minutes >= 60:
            return "danger"
        elif minutes >= 30:
            return "warning"
        else:
            return "rest"
    else:
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


def get_eta_status(eta_dt, appt_dt):
    if not eta_dt or not appt_dt:
        return ""

    diff_minutes = (eta_dt - appt_dt).total_seconds() / 60

    if diff_minutes > 15:
        return "LATE"
    elif diff_minutes >= -30:
        return "CLOSE"
    else:
        return "ON TIME"


def simple_eta(distance_miles):
    try:
        distance = float(distance_miles)
    except Exception:
        return None, None

    speed = 60
    hours = distance / speed
    eta_dt = datetime.now() + timedelta(hours=hours)
    return eta_dt, format_eta(eta_dt)


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
    driver["distance_miles"] = distance_input
    driver["notes"] = notes
    driver["eta"] = ""
    driver["eta_status"] = ""

    appt_dt = parse_appt_time(appt_time)

    if distance_input:
        eta_dt, eta_str = simple_eta(distance_input)
        if eta_dt:
            driver["eta"] = eta_str
            driver["eta_status"] = get_eta_status(eta_dt, appt_dt) if appt_dt else ""
        else:
            driver["eta"] = "Invalid distance"
            driver["eta_status"] = "ERROR"

    save_data(data)

    return jsonify({
        "success": True,
        "eta": driver["eta"],
        "eta_status": driver["eta_status"]
    })


if __name__ == "__main__":
    app.run(debug=True)