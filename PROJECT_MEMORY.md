# Dispatch System Project Memory

## Purpose

This repository is a small Flask dashboard for dispatchers to:

- log in with credentials stored in `users.json`
- view all tracked drivers loaded from `tracked_drivers.json`
- maintain per-dispatcher assignments in `user_assignments.json`
- edit delivery fields inline and autosave ETA-related data

The app is file-backed. There is no database, ORM, or background worker.

## Runtime Shape

- Backend: Flask app in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py)
- Main UI: [templates/index.html](/Users/bek/PycharmProjects/dispatch-system/templates/index.html)
- Login UI: [templates/login.html](/Users/bek/PycharmProjects/dispatch-system/templates/login.html)
- Driver ingestion: [safe_lane_fetcher.py](/Users/bek/PycharmProjects/dispatch-system/safe_lane_fetcher.py), [samsara_fetcher.py](/Users/bek/PycharmProjects/dispatch-system/samsara_fetcher.py)
- One-off session helper: [save_session.py](/Users/bek/PycharmProjects/dispatch-system/save_session.py)

## Data Files

### `tracked_drivers.json`

Primary source of driver records. Top-level shape:

```json
{
  "driver_key": {
    "name": "Driver Name",
    "status": "DRIVING",
    "location": "17mi SSE from Cushing, OK",
    "vehicle": "930",
    "minutes": 0,
    "delivery_address": "",
    "appt_time": "",
    "eta": "",
    "eta_status": "",
    "distance_miles": "",
    "notes": "",
    "alerted": false,
    "dispatch_status": "",
    "assigned_to": "",
    "source": "safe_lane"
  }
}
```

Important notes:

- `load_data()` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L14) backfills missing keys with defaults.
- `build_drivers()` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L156) adds transient fields:
  - `driver_key`
  - normalized `location`
  - computed `risk`

### `user_assignments.json`

Maps dispatcher usernames to a list of `driver_key` values.

Important notes:

- This file currently contains inconsistent names such as `Dispatcher 1` and `dispatcher1`.
- The app uses `session["user"]` directly, so assignment visibility depends on exact username match.

### `users.json`

Maps username to plaintext password for login.

Important note:

- Authentication is minimal and file-based. Passwords are not hashed.

## Backend Function Index

### Core file I/O

- `load_data()` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L14)
  - Reads `tracked_drivers.json`
  - Ensures expected dashboard fields exist on every driver
  - Returns `{}` when the file is missing

- `save_data(data)` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L38)
  - Writes the full driver dataset back to `tracked_drivers.json`

- `load_users()` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L43)
  - Reads `user_assignments.json`
  - Creates a default mapping if the file does not exist

- `save_users(users)` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L57)
  - Writes assignments back to disk

- `load_login_users()` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L62)
  - Reads login credentials from `users.json`

### Normalization and calculation helpers

- `clean_location(location)` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L70)
  - Strips prefixes like `17mi SSE from ...`

- `pretty_city_case(text)` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L77)
  - Title-cases comma-separated location parts

- `get_risk(status, minutes)` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L83)
  - Maps duty status and minute thresholds into:
    - `good`
    - `rest`
    - `warning`
    - `danger`
    - `unknown`

- `parse_appt_time(appt_time_raw)` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L104)
  - Accepts:
    - `%Y-%m-%dT%H:%M`
    - `%Y-%m-%d %H:%M`
    - `%m/%d/%Y %H:%M`

- `format_eta(dt_obj)` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L124)
  - Formats ETA as `MM/DD HH:MM AM/PM`

- `get_eta_status(eta_dt, appt_dt)` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L130)
  - `LATE` if ETA is more than 15 minutes after appointment
  - `CLOSE` if ETA is from 30 minutes early through 15 minutes late
  - `ON TIME` if ETA is more than 30 minutes early

- `simple_eta(distance_miles)` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L144)
  - Assumes a fixed 60 mph speed
  - Returns `(eta_datetime, formatted_eta)`

- `build_drivers()` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L156)
  - Reads driver data
  - Copies each record
  - Adds display-only fields used by templates

- `require_login()` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L170)
  - Checks for `session["user"]`

## Route Index

- `login()` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L175)
  - GET renders login page
  - POST validates plaintext credentials from `users.json`
  - On success redirects to `/all-drivers`

- `logout()` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L191)
  - Clears `session["user"]`

- `dashboard()` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L197)
  - Root redirect route

- `all_drivers()` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L205)
  - Shows every driver from `build_drivers()`

- `my_drivers()` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L224)
  - Filters `build_drivers()` by assigned `driver_key` values for current user

- `at_risk()` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L246)
  - Derives a filtered list using duty status, minutes, ETA status, appointment, and distance
  - Current logic marks risk if any of these are true:
    - `OFF` and `minutes >= 60`
    - `eta_status == "LATE"`
    - `OFF` and `minutes >= 30`
    - `eta_status == "CLOSE"`
    - appointment exists but distance is blank

- `assign_driver()` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L282)
  - Adds a `driver_key` to the current user’s list
  - Redirects back to `/all-drivers` or `/at-risk` depending on hidden form input

- `remove_driver()` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L304)
  - Removes a `driver_key` from the current user’s list
  - Redirects to `/my-drivers`

- `autosave()` in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L321)
  - Receives form-encoded row edits from the table
  - Updates `delivery_address`, `appt_time`, `distance_miles`, `notes`
  - Recomputes `eta` and `eta_status`
  - Writes the entire dataset back to disk
  - Returns JSON for UI refresh

## Frontend Function Index

Client-side behavior lives inline in [templates/index.html](/Users/bek/PycharmProjects/dispatch-system/templates/index.html#L1180).

- Search filter at [templates/index.html](/Users/bek/PycharmProjects/dispatch-system/templates/index.html#L1185)
  - Filters visible table rows by `innerText`

- `assignDriver(driverKey, nextPage)` at [templates/index.html](/Users/bek/PycharmProjects/dispatch-system/templates/index.html#L1206)
  - Populates hidden form and POSTs to `/assign-driver`

- `removeDriver(driverKey)` at [templates/index.html](/Users/bek/PycharmProjects/dispatch-system/templates/index.html#L1212)
  - Populates hidden form and POSTs to `/remove-driver`

- `applyTheme(mode)` at [templates/index.html](/Users/bek/PycharmProjects/dispatch-system/templates/index.html#L1241)
  - Toggles `light-mode`
  - Persists to `localStorage` under `fleet_theme_mode`

- `startAutoRefresh()` at [templates/index.html](/Users/bek/PycharmProjects/dispatch-system/templates/index.html#L1265)
  - Reloads the page every 5 seconds

- `stopAutoRefresh()` at [templates/index.html](/Users/bek/PycharmProjects/dispatch-system/templates/index.html#L1272)
  - Clears refresh interval

- `applyAutoRefresh(enabled)` at [templates/index.html](/Users/bek/PycharmProjects/dispatch-system/templates/index.html#L1279)
  - Syncs checkbox state and starts/stops the interval

- `setSaveStatus(driverKey, text, className)` at [templates/index.html](/Users/bek/PycharmProjects/dispatch-system/templates/index.html#L1301)
  - Updates per-row autosave status label

- `getRowData(driverKey)` at [templates/index.html](/Users/bek/PycharmProjects/dispatch-system/templates/index.html#L1308)
  - Reads current editable field values from a table row

- `renderEtaStatus(driverKey, etaStatus)` at [templates/index.html](/Users/bek/PycharmProjects/dispatch-system/templates/index.html#L1323)
  - Re-renders ETA status badge from autosave response

- `renderEta(driverKey, etaValue)` at [templates/index.html](/Users/bek/PycharmProjects/dispatch-system/templates/index.html#L1338)
  - Re-renders ETA cell from autosave response

- `autosaveDriver(driverKey)` at [templates/index.html](/Users/bek/PycharmProjects/dispatch-system/templates/index.html#L1349)
  - POSTs row data to `/autosave`
  - Updates ETA fields and save status based on JSON response

Autosave wiring:

- Inputs with `.autosave-input` attach a delayed save on `input`
- Delay is 15 seconds
- Immediate save also happens on `change`

## Fetcher Script Index

### `safe_lane_fetcher.py`

- `setup_driver()` at [safe_lane_fetcher.py](/Users/bek/PycharmProjects/dispatch-system/safe_lane_fetcher.py#L20)
  - Starts Chrome via Selenium and `webdriver_manager`

- `login_safe_lane(driver)` at [safe_lane_fetcher.py](/Users/bek/PycharmProjects/dispatch-system/safe_lane_fetcher.py#L32)
  - Logs into SafeLane with hardcoded credentials

- `go_to_drivers_page(driver)` at [safe_lane_fetcher.py](/Users/bek/PycharmProjects/dispatch-system/safe_lane_fetcher.py#L56)
  - Placeholder only, not implemented

- `extract_drivers(driver)` at [safe_lane_fetcher.py](/Users/bek/PycharmProjects/dispatch-system/safe_lane_fetcher.py#L62)
  - Scrapes `table tbody tr`
  - Builds driver dicts with dashboard-compatible defaults

- `save_json(data)` at [safe_lane_fetcher.py](/Users/bek/PycharmProjects/dispatch-system/safe_lane_fetcher.py#L105)
  - Normalizes list output into `{driver_key: record}`
  - Writes directly to `tracked_drivers.json`

- `main()` at [safe_lane_fetcher.py](/Users/bek/PycharmProjects/dispatch-system/safe_lane_fetcher.py#L120)
  - Opens browser
  - Logs in
  - Waits for manual navigation
  - Extracts and overwrites tracked driver data

### `samsara_fetcher.py`

- `setup_driver()` at [samsara_fetcher.py](/Users/bek/PycharmProjects/dispatch-system/samsara_fetcher.py#L20)
  - Same pattern as SafeLane

- `login_samsara(driver)` at [samsara_fetcher.py](/Users/bek/PycharmProjects/dispatch-system/samsara_fetcher.py#L32)
  - Handles multi-step sign-in
  - Uses hardcoded credentials
  - Pauses for manual email code entry

- `extract_drivers(driver)` at [samsara_fetcher.py](/Users/bek/PycharmProjects/dispatch-system/samsara_fetcher.py#L70)
  - Waits for manual navigation to the drivers table
  - Scrapes rows into dashboard records

- `save_json(data)` at [samsara_fetcher.py](/Users/bek/PycharmProjects/dispatch-system/samsara_fetcher.py#L107)
  - Normalizes by `name`
  - Writes directly to `tracked_drivers.json`

- `main()` at [samsara_fetcher.py](/Users/bek/PycharmProjects/dispatch-system/samsara_fetcher.py#L118)
  - Full manual-assisted scrape flow

### `save_session.py`

- Top-level script only in [save_session.py](/Users/bek/PycharmProjects/dispatch-system/save_session.py#L1)
  - Launches persistent Playwright Chrome profile in `user_data`
  - Opens SafeLane login
  - Waits for manual login
  - Intended to preserve a browser session

## Request and Data Flow

### Dashboard page load

1. Browser requests `/all-drivers`, `/my-drivers`, or `/at-risk`
2. Flask checks session
3. Backend loads JSON files from disk
4. Backend computes display fields and renders `templates/index.html`

### Assignment flow

1. User clicks assign/remove button in the table
2. Hidden form submits `driver_key`
3. Flask mutates `user_assignments.json`
4. Route redirects to the next page

### Autosave flow

1. User edits delivery fields in a row
2. Frontend waits 15 seconds after typing or saves immediately on change
3. `fetch("/autosave")` sends form-encoded row data
4. Flask updates one driver in memory and rewrites full `tracked_drivers.json`
5. JSON response returns `eta` and `eta_status`
6. Frontend updates row cells without full reload

## Known Risks And Weak Spots

- Hardcoded secrets exist in source files:
  - [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L7)
  - [safe_lane_fetcher.py](/Users/bek/PycharmProjects/dispatch-system/safe_lane_fetcher.py#L13)
  - [safe_lane_fetcher.py](/Users/bek/PycharmProjects/dispatch-system/safe_lane_fetcher.py#L14)
  - [safe_lane_fetcher.py](/Users/bek/PycharmProjects/dispatch-system/safe_lane_fetcher.py#L15)
  - [samsara_fetcher.py](/Users/bek/PycharmProjects/dispatch-system/samsara_fetcher.py#L13)
  - [samsara_fetcher.py](/Users/bek/PycharmProjects/dispatch-system/samsara_fetcher.py#L14)
  - [samsara_fetcher.py](/Users/bek/PycharmProjects/dispatch-system/samsara_fetcher.py#L15)

- Any autosave rewrites the whole `tracked_drivers.json` file. There is no locking, so concurrent writes could clobber data.

- Both fetcher scripts overwrite `tracked_drivers.json`. If dispatchers edited notes or appointment fields beforehand, a fresh scrape can erase those changes unless the scraper preserves existing fields.

- `minutes` is mostly defaulted to `0`. Risk logic in [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L83) and [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py#L257) depends on accurate minute values, so current at-risk results may be incomplete.

- User assignment keys are inconsistent in `user_assignments.json`, which can make “My Drivers” appear incorrect depending on login username.

- `safe_lane_fetcher.py` still contains a placeholder `go_to_drivers_page()` and example selectors. It is not a fully hardened scraper.

- `templates/templateslogin.html` exists but appears unused.

## Fast Fixing Checklist

When changing this project later, inspect these files first:

1. [app.py](/Users/bek/PycharmProjects/dispatch-system/app.py)
2. [templates/index.html](/Users/bek/PycharmProjects/dispatch-system/templates/index.html)
3. [tracked_drivers.json](/Users/bek/PycharmProjects/dispatch-system/tracked_drivers.json)
4. [user_assignments.json](/Users/bek/PycharmProjects/dispatch-system/user_assignments.json)

Then decide which category the issue belongs to:

- Login/session bug: inspect `login()`, `logout()`, `require_login()`
- Missing or wrong driver row data: inspect `load_data()`, `build_drivers()`, and the fetcher that last wrote the file
- Assignment bug: inspect `assign_driver()`, `remove_driver()`, and username consistency in `user_assignments.json`
- ETA bug: inspect `parse_appt_time()`, `simple_eta()`, `get_eta_status()`, and frontend `autosaveDriver()`
- UI-only bug: inspect inline JS and the row markup in `templates/index.html`
- Scrape/import bug: inspect Selenium selectors and JSON normalization in the relevant fetcher

## Suggested Next Improvements

- Move secrets to environment variables
- Preserve dispatcher-entered fields when importer scripts refresh driver status/location
- Normalize usernames in `user_assignments.json`
- Split inline CSS/JS out of `templates/index.html`
- Add a small test layer around ETA parsing/status logic
- Add dependency manifest if this repo is going to be rebuilt elsewhere
