from selenium import webdriver
from selenium.webdriver.common.by import By
import time
import os

driver = webdriver.Chrome()
query ="laptop"
file = 0
for i in range(1,20):
    driver.get(f"https://www.amazon.in/s?k={query}&page={i}&crid=WSP7Y015SQ7V&sprefix=laptop%2Caps%2C286&ref=nb_sb_noss_2")

    elems = driver.find_elements(By.CLASS_NAME,"puis-card-container")
    print(f"{len(elems)} items found")
    for elem in elems:
        d = elem.get_attribute("outerHTML")
        data_folder =f"{query}_data"
        os.makedirs(data_folder, exist_ok=True)
        with open(f"{data_folder}/{query}_{file}.html", "w",encoding="utf-8") as f:
            f.write(d)
            file += 1



    time.sleep(5)
driver.close()