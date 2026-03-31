from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import json
import math
import os
import socket
import threading
import time
from datetime import datetime, timedelta
from urllib.error import HTTPError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

import httpx

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover
    psycopg = None
    dict_row = None

app = Flask(__name__)
app.secret_key = "super_secret_key"


def load_simple_env(path=".env"):
    if not os.path.exists(path):
        return

    try:
        with open(path, "r", encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        return


load_simple_env()

LOGIN_FILE = "users.json"
JSON_FILE = "tracked_drivers.json"
USERS_FILE = "user_assignments.json"
GEOCODE_CACHE_FILE = "geo_cache.json"
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
AUTOCOMPLETE_SERVICE_URL = os.environ.get("AUTOCOMPLETE_SERVICE_URL", "http://127.0.0.1:8000/autocomplete").strip()
GEOAPIFY_API_KEY = os.environ.get("GEOAPIFY_API_KEY", "").strip()
SAFE_LANE_USERNAME = os.environ.get("SAFELANE_USERNAME", "").strip()
SAFE_LANE_PASSWORD = os.environ.get("SAFELANE_PASSWORD", "").strip()
SAFE_LANE_SYNC_INTERVAL_SECONDS = int(os.environ.get("SAFELANE_SYNC_INTERVAL_SECONDS", "180"))

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
GEOAPIFY_AUTOCOMPLETE_URL = "https://api.geoapify.com/v1/geocode/autocomplete"
OSRM_ROUTE_URL = "https://router.project-osrm.org/route/v1/driving"
OSRM_TABLE_URL = "https://router.project-osrm.org/table/v1/driving"
IPIFY_URL = "https://api.ipify.org/"
SAFE_LANE_SIGN_IN_URL = "https://cloud.safelaneeld.com/rest/rpc/sign_in_v2"
SAFE_LANE_DRIVERS_URL = "https://cloud.safelaneeld.com/rest/logs_by_driver_view"
HTTP_HEADERS = {
    "User-Agent": "dispatch-system/1.0 (+dispatch-dashboard)"
}
SAFE_LANE_STATUS_CODE_MAP = {
    "DS_D": "DRIVING",
    "DS_SB": "SLEEPER",
    "DS_OFF": "OFF DUTY",
    "DS_ON": "ON DUTY",
    "DS_YM": "YARD MOVE",
    "DS_PC": "PERSONAL CONVEYANCE",
}

DB_INIT_DONE = False
SYNC_LOCK = threading.Lock()
SYNC_THREAD_STARTED = False


def db_enabled():
    return bool(DATABASE_URL)


def require_db_driver():
    if not psycopg:
        raise RuntimeError("DATABASE_URL is set but psycopg is not installed.")


def connect_db():
    require_db_driver()
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def load_json_file(path, default):
    if not os.path.exists(path):
        return default

    with open(path, "r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def load_login_users_file():
    data = load_json_file(LOGIN_FILE, {})
    return data if isinstance(data, dict) else {}


def load_users_file():
    data = load_json_file(USERS_FILE, {})
    return data if isinstance(data, dict) else {}


def load_data():
    if not os.path.exists(JSON_FILE):
        return {}

    try:
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

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
        driver.setdefault("source", "")

    return data


def save_data(data):
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_geo_cache():
    data = load_json_file(GEOCODE_CACHE_FILE, {})
    return data if isinstance(data, dict) else {}


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

    if isinstance(appt_time_raw, datetime):
        return appt_time_raw

    appt_time_raw = str(appt_time_raw).strip()
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


def format_appt_value(dt_obj):
    if not dt_obj:
        return ""
    if isinstance(dt_obj, datetime):
        return dt_obj.strftime("%Y-%m-%dT%H:%M")
    return str(dt_obj)


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


def fallback_vehicle(driver_key, driver):
    vehicle = str(driver.get("vehicle", "")).strip()
    if vehicle:
        return vehicle

    key_text = str(driver_key or "").strip()
    if "|" in key_text:
        maybe_vehicle = key_text.rsplit("|", 1)[-1].strip()
        if maybe_vehicle:
            return maybe_vehicle

    return "N/A"


def canonical_status_text(value):
    raw_value = str(value or "").strip().upper()
    if not raw_value:
        return ""

    if raw_value in SAFE_LANE_STATUS_CODE_MAP:
        return SAFE_LANE_STATUS_CODE_MAP[raw_value]
    if raw_value in {"D", "DRIVE", "DRIVING"}:
        return "DRIVING"
    if raw_value in {"SB", "SLEEPER", "SLEEPER BERTH"}:
        return "SLEEPER"
    if raw_value in {"OFF", "OFF DUTY"}:
        return "OFF DUTY"
    if raw_value in {"ON", "ON DUTY"}:
        return "ON DUTY"
    if raw_value in {"YM", "YARD MOVE"}:
        return "YARD MOVE"
    if raw_value in {"PC", "PERSONAL CONVEYANCE"}:
        return "PERSONAL CONVEYANCE"
    return raw_value


def extract_safelane_status(driver):
    status_candidates = [
        driver.get("code"),
        driver.get("status"),
        driver.get("duty_status"),
        driver.get("dutyStatus"),
        driver.get("current_status"),
        driver.get("currentStatus"),
        driver.get("log_status"),
        driver.get("hos_status"),
    ]

    for candidate in status_candidates:
        normalized = canonical_status_text(candidate)
        if normalized:
            return normalized

    connection_status = str(driver.get("connection_status", "")).strip().upper()
    if connection_status == "CONNECTED":
        return "ON DUTY"

    return ""


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


def post_json(url, payload, headers=None):
    try:
        request_headers = {
            "Content-Type": "application/json",
            **HTTP_HEADERS,
        }
        if headers:
            request_headers.update(headers)
        request_obj = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=request_headers,
            method="POST",
        )
        with urlopen(request_obj, timeout=20) as response:
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


def suggest_addresses_geoapify(query, limit=5):
    query = str(query or "").strip()
    if len(query) < 3 or not GEOAPIFY_API_KEY:
        return []

    try:
        with httpx.Client(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
            response = client.get(
                GEOAPIFY_AUTOCOMPLETE_URL,
                params={
                    "text": query,
                    "limit": limit,
                    "format": "json",
                    "apiKey": GEOAPIFY_API_KEY,
                },
                headers=HTTP_HEADERS,
            )
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return []

    suggestions = []
    seen = set()
    for item in payload.get("results", []):
        label = str(item.get("formatted") or item.get("address_line1") or "").strip()
        if not label:
            continue
        normalized = normalize_cache_key(label)
        if normalized in seen:
            continue
        seen.add(normalized)
        suggestions.append({"label": label})

    return suggestions[:limit]


def fetch_autocomplete_suggestions(query, limit=5):
    query = str(query or "").strip()
    if len(query) < 3:
        return []

    if AUTOCOMPLETE_SERVICE_URL:
        try:
            with httpx.Client(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
                response = client.get(
                    AUTOCOMPLETE_SERVICE_URL,
                    params={"q": query},
                )
                response.raise_for_status()
                payload = response.json()
                suggestions = payload.get("suggestions", [])
                if isinstance(suggestions, list):
                    return suggestions[:limit]
        except Exception:
            pass

    geoapify_suggestions = suggest_addresses_geoapify(query, limit=limit)
    if geoapify_suggestions:
        return geoapify_suggestions

    return suggest_addresses(query, limit=limit)


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
        suggestions = suggest_addresses(address, limit=1)
        if not suggestions:
            return None

        suggestion_label = suggestions[0]["label"]
        if normalize_cache_key(suggestion_label) == cache_key:
            return None

        suggestion_result = geocode_address(suggestion_label)
        if suggestion_result:
            cache[cache_key] = suggestion_result
            save_geo_cache(cache)
        return suggestion_result

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
    if route_metrics:
        eta_dt = datetime.now() + timedelta(seconds=route_metrics["duration_seconds"])
        return {
            "eta_dt": eta_dt,
            "eta": format_eta(eta_dt),
            "distance_miles": route_metrics["distance_miles"],
        }

    distance_miles = round(haversine_miles(origin, destination), 1)
    duration_hours = distance_miles / 55 if distance_miles > 0 else 0
    eta_dt = datetime.now() + timedelta(hours=duration_hours)
    return {
        "eta_dt": eta_dt,
        "eta": format_eta(eta_dt),
        "distance_miles": distance_miles,
    }


def ensure_db_ready():
    global DB_INIT_DONE

    if DB_INIT_DONE or not db_enabled():
        return

    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS app_users (
                    username TEXT PRIMARY KEY,
                    password TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS driver_feed (
                    driver_key TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    location TEXT NOT NULL DEFAULT '',
                    vehicle TEXT NOT NULL DEFAULT '',
                    has_violations BOOLEAN NOT NULL DEFAULT FALSE,
                    minutes INTEGER NOT NULL DEFAULT 0,
                    alerted BOOLEAN NOT NULL DEFAULT FALSE,
                    dispatch_status TEXT NOT NULL DEFAULT '',
                    assigned_to TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                ALTER TABLE driver_feed
                ADD COLUMN IF NOT EXISTS has_violations BOOLEAN NOT NULL DEFAULT FALSE
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS user_driver_assignments (
                    username TEXT NOT NULL REFERENCES app_users(username) ON DELETE CASCADE,
                    driver_key TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (username, driver_key)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS driver_dispatch_state (
                    driver_key TEXT PRIMARY KEY,
                    delivery_address TEXT NOT NULL DEFAULT '',
                    appt_time TIMESTAMP NULL,
                    eta TEXT NOT NULL DEFAULT '',
                    eta_status TEXT NOT NULL DEFAULT '',
                    eta_delay_minutes INTEGER NOT NULL DEFAULT 0,
                    eta_delay_text TEXT NOT NULL DEFAULT '',
                    distance_miles TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            seed_db_from_files(conn)
        conn.commit()

    DB_INIT_DONE = True


def get_setting(conn, key):
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM app_settings WHERE key = %s", (key,))
        row = cur.fetchone()
    return row["value"] if row else None


def set_setting(conn, key, value):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                updated_at = NOW()
            """,
            (key, value),
        )


def seed_db_from_files(conn):
    login_users = load_login_users_file()
    assignments = load_users_file()
    raw_drivers = load_data()

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS count FROM app_users")
        user_count = cur.fetchone()["count"]
        if user_count == 0:
            for username, password in login_users.items():
                cur.execute(
                    """
                    INSERT INTO app_users (username, password)
                    VALUES (%s, %s)
                    ON CONFLICT (username) DO NOTHING
                    """,
                    (str(username).strip(), str(password)),
                )

        cur.execute("SELECT COUNT(*) AS count FROM user_driver_assignments")
        assignment_count = cur.fetchone()["count"]
        if assignment_count == 0:
            for username, driver_keys in assignments.items():
                normalized_username = str(username).strip()
                cur.execute(
                    """
                    INSERT INTO app_users (username, password)
                    VALUES (%s, %s)
                    ON CONFLICT (username) DO NOTHING
                    """,
                    (normalized_username, str(login_users.get(normalized_username, ""))),
                )
                for driver_key in driver_keys or []:
                    cur.execute(
                        """
                        INSERT INTO user_driver_assignments (username, driver_key)
                        VALUES (%s, %s)
                        ON CONFLICT (username, driver_key) DO NOTHING
                        """,
                        (normalized_username, str(driver_key).strip()),
                    )

        cur.execute("SELECT COUNT(*) AS count FROM driver_dispatch_state")
        state_count = cur.fetchone()["count"]
        if state_count == 0:
            for driver_key, driver in raw_drivers.items():
                if not isinstance(driver, dict):
                    continue
                cur.execute(
                    """
                    INSERT INTO driver_dispatch_state (
                        driver_key, delivery_address, appt_time, eta, eta_status,
                        eta_delay_minutes, eta_delay_text, distance_miles, notes
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (driver_key) DO NOTHING
                    """,
                    (
                        str(driver_key).strip(),
                        str(driver.get("delivery_address", "")),
                        parse_appt_time(driver.get("appt_time", "")),
                        str(driver.get("eta", "")),
                        str(driver.get("eta_status", "")),
                        int(driver.get("eta_delay_minutes", 0) or 0),
                        str(driver.get("eta_delay_text", "")),
                        str(driver.get("distance_miles", "")),
                        str(driver.get("notes", "")),
                    ),
                )

    sync_driver_feed_to_db(conn, raw_drivers, prune_missing=False)


def sync_driver_feed_to_db(conn, raw_data, prune_missing=True):
    current_keys = []

    if not raw_data:
        return False

    with conn.cursor() as cur:
        for raw_key, driver in raw_data.items():
            if not isinstance(driver, dict):
                continue

            driver_key = str(raw_key).strip()
            if not driver_key:
                continue

            current_keys.append(driver_key)
            cur.execute(
                """
                INSERT INTO driver_feed (
                    driver_key, name, status, location, vehicle, has_violations, minutes,
                    alerted, dispatch_status, assigned_to, source, synced_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (driver_key) DO UPDATE SET
                    name = EXCLUDED.name,
                    status = EXCLUDED.status,
                    location = EXCLUDED.location,
                    vehicle = EXCLUDED.vehicle,
                    has_violations = EXCLUDED.has_violations,
                    minutes = EXCLUDED.minutes,
                    alerted = EXCLUDED.alerted,
                    dispatch_status = EXCLUDED.dispatch_status,
                    assigned_to = EXCLUDED.assigned_to,
                    source = EXCLUDED.source,
                    synced_at = NOW()
                """,
                (
                    driver_key,
                    str(driver.get("name", "")),
                    str(driver.get("status", "")),
                    str(driver.get("location", "")),
                    str(driver.get("vehicle", "")),
                    bool(driver.get("has_violations", False)),
                    int(driver.get("minutes", 0) or 0),
                    bool(driver.get("alerted", False)),
                    str(driver.get("dispatch_status", "")),
                    str(driver.get("assigned_to", "")),
                    str(driver.get("source", "")),
                ),
            )

        if prune_missing and current_keys:
            cur.execute(
                "DELETE FROM driver_feed WHERE NOT (driver_key = ANY(%s))",
                (current_keys,),
            )

    return True


def get_public_ip():
    data = fetch_json(IPIFY_URL, {"format": "json"})
    if not data:
        return ""
    return str(data.get("ip", "")).strip()


def get_safe_lane_device_name():
    host = socket.gethostname()
    return f"{host} Dispatch System"


def sign_in_safe_lane(conn):
    if not SAFE_LANE_USERNAME or not SAFE_LANE_PASSWORD:
        raise RuntimeError("SAFELANE_USERNAME and SAFELANE_PASSWORD are required.")

    public_ip = get_public_ip()
    payload = {
        "username": SAFE_LANE_USERNAME,
        "password": SAFE_LANE_PASSWORD,
        "parameters": {
            "device_id": socket.gethostname(),
            "device_name": get_safe_lane_device_name(),
            "location_text": "",
            "ip": public_ip,
        },
    }
    response = post_json(SAFE_LANE_SIGN_IN_URL, payload)
    if not response or not response.get("token"):
        raise RuntimeError("SafeLane sign-in failed.")

    set_setting(conn, "safelane_token", response["token"])
    set_setting(conn, "safelane_account_id", str(response.get("account_id", "")))
    set_setting(conn, "safelane_company_id", str(response.get("company_id", "")))

    return {
        "token": response["token"],
        "account_id": str(response.get("account_id", "")),
        "company_id": str(response.get("company_id", "")),
    }


def get_safelane_auth(conn):
    token = get_setting(conn, "safelane_token")
    account_id = get_setting(conn, "safelane_account_id")
    company_id = get_setting(conn, "safelane_company_id")
    if token and account_id and company_id:
        return {
            "token": token,
            "account_id": account_id,
            "company_id": company_id,
        }
    return sign_in_safe_lane(conn)


def fetch_safelane_drivers(auth):
    query = {
        "order": "last_seen.desc.nullslast",
        "limit": 1000,
        "and": f"(is_active.eq.true,company_id.eq.{auth['company_id']})",
    }
    request_obj = Request(
        f"{SAFE_LANE_DRIVERS_URL}?{urlencode(query)}",
        headers={
            "Accept": "application/json, text/plain, */*",
            "Authorization": f"Bearer {auth['token']}",
            "x-eld-account-id": auth["account_id"],
            **HTTP_HEADERS,
        },
    )
    with urlopen(request_obj, timeout=30) as response:
        status_code = getattr(response, "status", 200)
        body = response.read().decode("utf-8")
    return status_code, json.loads(body)


def normalize_safelane_driver(driver):
    name = str(
        driver.get("full_name")
        or f"{driver.get('first_name', '')} {driver.get('last_name', '')}".strip()
        or driver.get("username", "")
        or driver.get("id", "")
    ).strip()
    if not name:
        return None

    return {
        "driver_key": name,
        "name": name,
        "status": extract_safelane_status(driver),
        "location": str(driver.get("location_text", "")).strip(),
        "vehicle": str(driver.get("vehicle_name", "")).strip(),
        "has_violations": bool(driver.get("has_violations", False)),
        "minutes": 0,
        "alerted": False,
        "dispatch_status": "",
        "assigned_to": "",
        "source": "safe_lane_api",
    }


def sync_safelane_feed(force=False):
    if not db_enabled():
        return False

    ensure_db_ready()

    if not SYNC_LOCK.acquire(blocking=False):
        return False

    try:
        with connect_db() as conn:
            last_sync_raw = get_setting(conn, "last_driver_sync_at")
            if not force and last_sync_raw:
                try:
                    last_sync = datetime.fromisoformat(last_sync_raw)
                    if (datetime.utcnow() - last_sync).total_seconds() < SAFE_LANE_SYNC_INTERVAL_SECONDS:
                        return False
                except ValueError:
                    pass

            auth = get_safelane_auth(conn)
            try:
                _, payload = fetch_safelane_drivers(auth)
            except HTTPError as exc:
                if exc.code != 401:
                    raise
                auth = sign_in_safe_lane(conn)
                _, payload = fetch_safelane_drivers(auth)

            normalized = {}
            for driver in payload or []:
                normalized_driver = normalize_safelane_driver(driver)
                if not normalized_driver:
                    continue
                normalized[normalized_driver["driver_key"]] = normalized_driver

            sync_driver_feed_to_db(conn, normalized, prune_missing=True)
            set_setting(conn, "last_driver_sync_at", datetime.utcnow().isoformat())
            conn.commit()
        return True
    finally:
        SYNC_LOCK.release()


def safe_safelane_sync(force=False):
    try:
        return sync_safelane_feed(force=force)
    except Exception:
        return False


def background_sync_loop():
    interval_seconds = max(60, SAFE_LANE_SYNC_INTERVAL_SECONDS)
    while True:
        safe_safelane_sync(force=True)
        time.sleep(interval_seconds)


def ensure_background_sync_started():
    global SYNC_THREAD_STARTED

    if SYNC_THREAD_STARTED or not db_enabled():
        return
    if not SAFE_LANE_USERNAME or not SAFE_LANE_PASSWORD:
        return

    thread = threading.Thread(target=background_sync_loop, name="safelane-sync", daemon=True)
    thread.start()
    SYNC_THREAD_STARTED = True


def get_login_users():
    if not db_enabled():
        return load_login_users_file()

    ensure_db_ready()
    ensure_background_sync_started()
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT username, password FROM app_users ORDER BY username")
            return {row["username"]: row["password"] for row in cur.fetchall()}


def get_assignment_map():
    if not db_enabled():
        return load_users_file()

    ensure_db_ready()
    ensure_background_sync_started()
    assignments = {}
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT username FROM app_users ORDER BY username")
            for row in cur.fetchall():
                assignments[row["username"]] = []

            cur.execute(
                """
                SELECT username, driver_key
                FROM user_driver_assignments
                ORDER BY username, driver_key
                """
            )
            for row in cur.fetchall():
                assignments.setdefault(row["username"], []).append(row["driver_key"])

    return assignments


def get_assigned_keys(username):
    if not db_enabled():
        return set(load_users_file().get(username, []))

    ensure_db_ready()
    ensure_background_sync_started()
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT driver_key
                FROM user_driver_assignments
                WHERE username = %s
                """,
                (username,),
            )
            return {row["driver_key"] for row in cur.fetchall()}


def add_assignment(username, driver_key):
    if not db_enabled():
        users = load_users_file()
        users.setdefault(username, [])
        if driver_key not in users[username]:
            users[username].append(driver_key)
            with open(USERS_FILE, "w", encoding="utf-8") as f:
                json.dump(users, f, indent=2, ensure_ascii=False)
        return

    ensure_db_ready()
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_driver_assignments (username, driver_key)
                VALUES (%s, %s)
                ON CONFLICT (username, driver_key) DO NOTHING
                """,
                (username, driver_key),
            )
        conn.commit()


def remove_assignment(username, driver_key):
    if not db_enabled():
        users = load_users_file()
        users.setdefault(username, [])
        if driver_key in users[username]:
            users[username].remove(driver_key)
            with open(USERS_FILE, "w", encoding="utf-8") as f:
                json.dump(users, f, indent=2, ensure_ascii=False)
        return

    ensure_db_ready()
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM user_driver_assignments
                WHERE username = %s AND driver_key = %s
                """,
                (username, driver_key),
            )
        conn.commit()


def upsert_dispatch_state(driver_key, state):
    if not db_enabled():
        data = load_data()
        driver = data.setdefault(driver_key, {})
        driver.update(state)
        save_data(data)
        return

    ensure_db_ready()
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO driver_dispatch_state (
                    driver_key, delivery_address, appt_time, eta, eta_status,
                    eta_delay_minutes, eta_delay_text, distance_miles, notes, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (driver_key) DO UPDATE SET
                    delivery_address = EXCLUDED.delivery_address,
                    appt_time = EXCLUDED.appt_time,
                    eta = EXCLUDED.eta,
                    eta_status = EXCLUDED.eta_status,
                    eta_delay_minutes = EXCLUDED.eta_delay_minutes,
                    eta_delay_text = EXCLUDED.eta_delay_text,
                    distance_miles = EXCLUDED.distance_miles,
                    notes = EXCLUDED.notes,
                    updated_at = NOW()
                """,
                (
                    driver_key,
                    state["delivery_address"],
                    state["appt_time"],
                    state["eta"],
                    state["eta_status"],
                    state["eta_delay_minutes"],
                    state["eta_delay_text"],
                    state["distance_miles"],
                    state["notes"],
                ),
            )
        conn.commit()


def get_driver_location(driver_key):
    if not db_enabled():
        return load_data().get(driver_key, {}).get("location", "")

    ensure_db_ready()
    ensure_background_sync_started()
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT location FROM driver_feed WHERE driver_key = %s",
                (driver_key,),
            )
            row = cur.fetchone()
        conn.commit()
    return row["location"] if row else ""


def build_db_drivers():
    ensure_db_ready()
    ensure_background_sync_started()

    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    f.driver_key,
                    f.name,
                    f.status,
                    f.location,
                    f.vehicle,
                    f.has_violations,
                    f.minutes,
                    COALESCE(s.delivery_address, '') AS delivery_address,
                    s.appt_time AS appt_time,
                    COALESCE(s.eta, '') AS eta,
                    COALESCE(s.eta_status, '') AS eta_status,
                    COALESCE(s.eta_delay_minutes, 0) AS eta_delay_minutes,
                    COALESCE(s.eta_delay_text, '') AS eta_delay_text,
                    COALESCE(s.distance_miles, '') AS distance_miles,
                    COALESCE(s.notes, '') AS notes,
                    f.alerted,
                    f.dispatch_status,
                    f.assigned_to,
                    f.source
                FROM driver_feed f
                LEFT JOIN driver_dispatch_state s
                    ON s.driver_key = f.driver_key
                ORDER BY f.name, f.driver_key
                """
            )
            rows = cur.fetchall()
        conn.commit()

    drivers = []
    for row in rows:
        driver = dict(row)
        driver["location"] = pretty_city_case(clean_location(driver.get("location", "")))
        driver["vehicle"] = fallback_vehicle(driver["driver_key"], driver)
        driver["appt_time"] = format_appt_value(driver.get("appt_time"))
        driver["risk"] = get_risk(driver.get("status", ""), driver.get("minutes", 0))
        drivers.append(driver)

    return drivers


def build_file_drivers():
    data = load_data()
    drivers = []

    for key, d in data.items():
        driver = d.copy()
        driver["driver_key"] = key
        driver["location"] = pretty_city_case(clean_location(driver.get("location", "")))
        driver["vehicle"] = fallback_vehicle(key, driver)
        driver["appt_time"] = format_appt_value(driver.get("appt_time"))
        driver["risk"] = get_risk(driver.get("status", ""), driver.get("minutes", 0))
        drivers.append(driver)

    return drivers


def build_drivers():
    if db_enabled():
        return build_db_drivers()
    return build_file_drivers()


@app.before_request
def bootstrap_runtime_services():
    if not db_enabled():
        return
    ensure_db_ready()
    ensure_background_sync_started()


def find_nearest_matches(address, drivers):
    destination = geocode_address(address)
    if not destination:
        return None

    geocode_cache = {}
    prepared = []
    for driver in drivers:
        location_query = clean_location(driver.get("location", ""))
        if not location_query:
            continue

        cache_key = normalize_cache_key(location_query)
        origin = geocode_cache.get(cache_key)
        if origin is None:
            origin = geocode_address(location_query)
            geocode_cache[cache_key] = origin
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

    for item in prepared:
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

        users = get_login_users()

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
    selected_user = session["user"]

    return render_template(
        "index.html",
        drivers=drivers,
        selected_user=selected_user,
        current_page="all"
    )


@app.route("/my-drivers")
def my_drivers():
    if not require_login():
        return redirect(url_for("login"))

    selected_user = session["user"]
    assigned_keys = get_assigned_keys(selected_user)
    all_data = build_drivers()
    drivers = [d for d in all_data if d["driver_key"] in assigned_keys]

    return render_template(
        "index.html",
        drivers=drivers,
        selected_user=selected_user,
        current_page="my"
    )


@app.route("/at-risk")
def at_risk():
    if not require_login():
        return redirect(url_for("login"))

    selected_user = session["user"]
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
        selected_user=selected_user,
        current_page="risk"
    )


@app.route("/assign-driver", methods=["POST"])
def assign_driver():
    if not require_login():
        return redirect(url_for("login"))

    selected_user = session["user"]
    driver_key = request.form.get("driver_key", "").strip()

    if driver_key:
        add_assignment(selected_user, driver_key)

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

    if driver_key:
        remove_assignment(selected_user, driver_key)

    return redirect(url_for("my_drivers"))


@app.route("/autocomplete")
@app.route("/address-suggest")
def address_suggest():
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401

    query = request.args.get("q", "").strip()
    return jsonify({"suggestions": fetch_autocomplete_suggestions(query)})


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

    current_location = get_driver_location(driver_key)
    if not current_location:
        return jsonify({"success": False, "error": "Driver not found"}), 404

    appt_dt = parse_appt_time(appt_time)
    route_eta = None
    eta = ""
    eta_status = ""
    eta_delay_text = ""
    eta_delay_minutes = 0
    distance_miles = distance_input

    if delivery_address:
        route_eta = build_route_eta(current_location, delivery_address)

    if route_eta:
        distance_miles = str(route_eta["distance_miles"])
        eta = route_eta["eta"]
        eta_status, eta_delay_text, eta_delay_minutes = get_eta_status(route_eta["eta_dt"], appt_dt)
    elif distance_input:
        eta_dt, eta = simple_eta(distance_input)
        if eta_dt:
            eta_status, eta_delay_text, eta_delay_minutes = get_eta_status(eta_dt, appt_dt)
        else:
            eta = "Invalid distance"
            eta_status = "ERROR"

    upsert_dispatch_state(
        driver_key,
        {
            "delivery_address": delivery_address,
            "appt_time": appt_dt,
            "eta": eta,
            "eta_status": eta_status,
            "eta_delay_minutes": eta_delay_minutes,
            "eta_delay_text": eta_delay_text,
            "distance_miles": distance_miles,
            "notes": notes,
        },
    )

    return jsonify({
        "success": True,
        "eta": eta,
        "eta_status": eta_status,
        "eta_delay_text": eta_delay_text,
        "distance_miles": distance_miles,
        "route_used": bool(route_eta),
    })


if __name__ == "__main__":
    app.run(debug=True)
