import json
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager


SAFE_LANE_URL = "https://cloud.safelaneeld.com/auth/login"
SAFE_LANE_USERNAME = "sdglobal@gmail.com"
SAFE_LANE_PASSWORD = "123456"

OUTPUT_FILE = Path("tracked_drivers.json")


def setup_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    # options.add_argument("--headless=new")  # keyin serverda kerak bo‘lsa yoqamiz
    options.add_argument("--disable-blink-features=AutomationControlled")
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    return driver


def login_safe_lane(driver):
    driver.get(SAFE_LANE_URL)

    wait = WebDriverWait(driver, 20)

    username_input = wait.until(
        EC.presence_of_element_located((By.NAME, "username"))
    )
    password_input = wait.until(
        EC.presence_of_element_located((By.NAME, "password"))
    )

    username_input.clear()
    username_input.send_keys(SAFE_LANE_USERNAME)

    password_input.clear()
    password_input.send_keys(SAFE_LANE_PASSWORD)

    login_button = driver.find_element(By.XPATH, "//button[@type='submit']")
    login_button.click()

    time.sleep(5)


def go_to_drivers_page(driver):
    # Bu yer keyin haqiqiy menu / URL ga moslanadi
    # driver.get("https://....../drivers")
    pass


def extract_drivers(driver):
    wait = WebDriverWait(driver, 20)

    # Quyidagi selectorlar misol, keyin saytga qarab almashtiramiz
    rows = wait.until(
        EC.presence_of_all_elements_located((By.CSS_SELECTOR, "table tbody tr"))
    )

    drivers = []

    for row in rows:
        cols = row.find_elements(By.TAG_NAME, "td")
        if len(cols) < 4:
            continue

        driver_data = {
            "name": cols[0].text.strip(),
            "status": cols[1].text.strip(),
            "location": cols[2].text.strip(),
            "vehicle": cols[3].text.strip(),
            "minutes": 0,
            "delivery_address": "",
            "appt_time": "",
            "eta": "",
            "eta_status": "",
            "distance_miles": "",
            "notes": "",
            "alerted": False
        }


        # Dashboarding buzilmasin deb default maydonlar
        driver_data.setdefault("eta_status", "UNKNOWN")
        driver_data.setdefault("dispatch_status", "")
        driver_data.setdefault("notes", "")
        driver_data.setdefault("assigned_to", "")
        driver_data.setdefault("source", "safe_lane")

        drivers.append(driver_data)

    return drivers


def save_json(data):
    normalized = {}

    for i, driver in enumerate(data):
        key = (
            driver.get("driver_name")
            or driver.get("name")
            or f"driver_{i}"
        )
        normalized[key] = driver

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2, ensure_ascii=False)


def main():
    driver = setup_driver()
    try:
        login_safe_lane(driver)
        input("Dashboard ochildi. Endi driverlar table chiqadigan joyga o'zing o'tib qo'y, keyin Enter bos...")
        drivers = extract_drivers(driver)
        print(drivers)
        save_json(drivers)
        print(f"Saved {len(drivers)} drivers to {OUTPUT_FILE}")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()