from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch_persistent_context(
        user_data_dir="user_data",
        headless=False,
        channel="chrome"
    )

    page = browser.new_page()
    page.goto("https://cloud.safelaneeld.com/auth/login")

    print("Loginni 1 marta qo'lda qiling")
    input("Login qilib bo'lgach Enter bosing...")

    print("Session saqlandi")
    browser.close()