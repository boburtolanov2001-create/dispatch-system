import json
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager


SAFE_LANE_URL = "https://cloud.samsara.com/signin"
SAFE_LANE_USERNAME = "creed@sdgloballlc.net"
SAFE_LANE_PASSWORD = "iskandar1708"

OUTPUT_FILE = Path("tracked_drivers.json")


def setup_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    # options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    return driver


def login_samsara(driver):
    driver.get(SAFE_LANE_URL)
    wait = WebDriverWait(driver, 20)

    # 1-step: email
    email_input = wait.until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "input"))
    )
    email_input.clear()
    email_input.send_keys(SAFE_LANE_USERNAME)

    continue_btn = wait.until(
    EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Continue')]"))
    )
    driver.execute_script("arguments[0].click();", continue_btn)

    time.sleep(2)

    # 2-step: password
    password_input = wait.until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='password']"))
    )
    password_input.clear()
    password_input.send_keys(SAFE_LANE_PASSWORD)

    login_btn = wait.until(
    EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Sign in')]"))
    )
    driver.execute_script("arguments[0].click();", login_btn)

    time.sleep(8)
    login_btn = wait.until(
    EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Sign in')]"))
    )
    driver.execute_script("arguments[0].click();", login_btn)

    input("Code ni emaildan kiriting, keyin Enter bosing...")

def extract_drivers(driver):
    wait = WebDriverWait(driver, 20)

    input("Samsara ichida driverlar jadvali chiqadigan sahifaga o'zing o'tib qo'y, keyin Enter bos...")

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
            "alerted": False,
            "source": "samsara"
        }

        drivers.append(driver_data)

    return drivers


def save_json(data):
    normalized = {}

    for i, driver in enumerate(data):
        key = driver.get("name") or f"driver_{i}"
        normalized[key] = driver

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2, ensure_ascii=False)


def main():
    driver = setup_driver()
    try:
        login_samsara(driver)
        drivers = extract_drivers(driver)
        print(drivers)
        save_json(drivers)
        print(f"Saved {len(drivers)} drivers to {OUTPUT_FILE}")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()