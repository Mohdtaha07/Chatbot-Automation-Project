from selenium import webdriver
from selenium.webdriver.common.by import By
import time
import os
import pandas as pd
from bs4 import BeautifulSoup


# WEBSITE AUTOMATION
query =input("What are you looking for today?\n ")
driver = webdriver.Chrome()
data_folder =f"{query}_data"
file = 0

for i in range(1,20):
    driver.get(f"https://www.amazon.in/s?k={query}&page={i}&crid=WSP7Y015SQ7V&sprefix=laptop%2Caps%2C286&ref=nb_sb_noss_2")

    elems = driver.find_elements(By.CLASS_NAME,"puis-card-container")
    print(f"{len(elems)} items found")
    for elem in elems:
        d = elem.get_attribute("outerHTML")
        os.makedirs(data_folder, exist_ok=True)
        with open(f"{data_folder}/{query}_{file}.html", "w",encoding="utf-8") as f:
            f.write(d)
            file += 1



    time.sleep(5)
driver.close()



# DATA COLLECTION & CONVERSION TO EXCEL SHEET
data_dir = {data_folder}
d = {"title": [], "price": [], "link": []}

def make_full_url(href: str) -> str:
    if not href:
        return ""
    href = href.strip()
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://amazon.in" + href
    return "https://amazon.in/" + href

for fname in os.listdir(data_folder):
    path = os.path.join(data_folder, fname)
    if not os.path.isfile(path):
        continue
    if not fname.lower().endswith((".html", ".htm", ".txt")):
        continue

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            html_doc = f.read()
    except Exception as e:
        print(f"Read error {fname}: {e}")
        continue

    soup = BeautifulSoup(html_doc, "html.parser")

    h2 = soup.find("h2")
    title = h2.get_text(strip=True) if h2 else ""

    # Prefer anchor inside h2, fallback to best candidate anywhere in the page
    a_tag = None
    if h2:
        a_tag = h2.find("a", href=True)
    if not a_tag:
        # prefer product-like links
        candidates = [a for a in soup.find_all("a", href=True)]
        product_candidates = [a for a in candidates if any(p in a["href"] for p in ("/dp/", "/gp/", "/product/"))]
        a_tag = product_candidates[0] if product_candidates else (candidates[0] if candidates else None)

    href = a_tag["href"] if a_tag and a_tag.has_attr("href") else ""
    link = make_full_url(href)

    price_tag = soup.find("span", class_="a-price-whole")
    price = price_tag.get_text(strip=True) if price_tag else ""

    if not link:
        print(f"No link found in {fname}")

    d["title"].append(title)
    d["price"].append(price)
    d["link"].append(link)

df = pd.DataFrame(data=d)
df.to_csv(f"{data_folder}.csv", index=False, encoding="utf-8-sig")
print(f"Wrote {len(df)} rows to {data_folder}.csv")