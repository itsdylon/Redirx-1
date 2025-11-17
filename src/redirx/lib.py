from selenium import webdriver
from selenium.webdriver.chrome.options import Options

def create_web_driver() -> webdriver.Chrome:
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    driver = webdriver.Chrome(options=chrome_options)
    return driver

if __name__ == '__main__':
    driver = create_web_driver()
    