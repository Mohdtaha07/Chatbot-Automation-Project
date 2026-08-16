from selenium import webdriver
from selenium.webdriver.common.by import By
import time

driver = webdriver.Chrome()
query ="laptop"
for i in range(1,20):
    driver.get(f"https://www.amazon.in/s?k={query}&page={i}&crid=WSP7Y015SQ7V&sprefix=laptop%2Caps%2C286&ref=nb_sb_noss_2")

    elems = driver.find_elements(By.CLASS_NAME,"puis-card-container")
    print(f"{len(elems)} items found")
    for elem in elems:
        print(elem.text)

    # print(elems.get_attribute("outerHTML"))
    # print(elem.text)

    time.sleep(5)
driver.close()