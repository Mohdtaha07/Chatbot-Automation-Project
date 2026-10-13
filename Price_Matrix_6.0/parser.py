import os
import re
import pandas as pd
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Shared plumbing (folder/CSV locations + progress log routing).
# The extraction logic below is untouched; these two imports only decide WHERE
# files are read from / written to and WHERE the status text is printed.
# ---------------------------------------------------------------------------
from paths import PROJECT_ROOT, output_path
from logger import log


def process_data(product_input, site_name, source_folder=None, output_file=None):
    """
    Parse every saved `.html` card for one platform into a cleaned CSV.

    Parameters
    ----------
    product_input : str   e.g. "laptop"                     (drives default names)
    site_name     : str   "amazon" or "flipkart"            (drives the parser branch)
    source_folder : str   optional explicit folder of raw HTML files.
                          Defaults to "<site_name>_<product_input>_data" next to
                          this file — which is exactly what scrapper.py creates.
    output_file   : str   optional explicit CSV path. Defaults to
                          "<site_name>_<product_input>_data_cleaned.csv" in the
                          project root, which is exactly what comparator.py reads.

    app.py passes both explicitly (per-job temp folders / timestamped CSVs);
    running this file directly keeps the original behaviour.
    """
    # Normalize inputs to match folder structures
    product_input = product_input.strip().lower()
    site_name = site_name.strip().lower()
    target_folder = source_folder or os.path.join(PROJECT_ROOT, f"{site_name}_{product_input}_data")

    # Initialize dictionary with target columns
    d = {
        'Title': [],
        'Price': [],
        'Link': [],
        'RAM': [],
        'Storage': [],
        'Processor': [],
        'Review': [],
        # additive: how many ratings/reviews the card advertised (0 when absent)
        'Review_Count': [],
    }

    # Dynamic folder checking
    if not os.path.exists(target_folder):
        log(f"Error: The '{target_folder}' folder does not exist!")
        return

    files = os.listdir(target_folder)
    if not files:
        log(f"Warning: The '{target_folder}' folder is empty.")
        return

    for file in files:
        if file.startswith('.') or not file.endswith('.html'):
            continue

        try:
            file_path = os.path.join(target_folder, file)
            with open(file_path, "r", encoding="utf-8") as f:
                html_doc = f.read()

            soup = BeautifulSoup(html_doc, 'html.parser')

            # 1. Base Information Extraction based on Platform
            if site_name == "amazon":
                # Title
                title_tag = soup.find("h2") or soup.find("span", class_="a-size-medium")
                title = title_tag.get_text().strip() if title_tag else "Unknown Title"

                # Link
                link_tag = soup.find("a", class_=["a-link-normal", "s-no-outline"])
                originalLink = "https://amazon.in" + link_tag['href'] if link_tag and 'href' in link_tag.attrs else "Link Not Available"

                # Price
                price_tag = soup.find("span", class_="a-offscreen")
                currentPrice = price_tag.get_text().strip() if price_tag else "No Price Listed"

                # Review
                review_tag = soup.find("span", class_="a-icon-alt") or soup.find("i", class_="a-icon-star")
                review_text = review_tag.get_text().strip() if review_tag else ""

            else: # FLIPKART PARSING CONSTRAINTS
                # Title (Typically matches images alt text or specific structural divs)
                title_tag = soup.find("div", class_=["KzDlHZ", "wjcE9g"]) or soup.find("a", class_="wX67w9")
                if not title_tag and soup.find("img"):
                    title = soup.find("img").get("alt", "Unknown Title").strip()
                else:
                    title = title_tag.get_text().strip() if title_tag else "Unknown Title"

                # Link
                link_tag = soup.find("a", href=True)
                originalLink = "https://www.flipkart.com" + link_tag['href'] if link_tag else "Link Not Available"

                # Price
                price_tag = soup.select_one('div.hZ3P6w.DeU9vF')
                currentPrice = price_tag.text.strip() if price_tag else "No Price Listed"

                # Review
                review_tag = soup.find('div', class_='MKiFS6')
                review_text = review_tag.text.strip() if review_tag else ""
                # NOTE: on Flipkart the FIRST div.MKiFS6 is the star rating (e.g. "4.4"),
                # not the "2,231 Ratings & 118 Reviews" block, so `review` below ends up
                # holding the rating and `Review_Count` stays 0 for Flipkart. That is why
                # review volume reads 0 on Flipkart side (the value score's 16% term and
                # the "Most reviewed" sort lean Amazon). Left as-is deliberately — changing
                # it means altering this parsing block. One-line fix if wanted:
                #   soup.find_all('div', class_='MKiFS6')[-1]  (or find the Ratings div)

            # 2. Extract Technical Specs from Title using Regex (Shared Logics)
            ram_match = re.search(r'(\d+\s*(?:GB|RAM))', title, re.IGNORECASE)
            ram = ram_match.group(1).strip() if ram_match else "N/A"

            storage_match = re.search(r'(\d+\s*(?:GB|TB)\s*(?:SSD|HDD|Storage)?)', title, re.IGNORECASE)
            if storage_match and storage_match.group(1).strip() == ram:
                storage_matches = re.findall(r'(\d+\s*(?:GB|TB)\s*(?:SSD|HDD|Storage)?)', title, re.IGNORECASE)
                storage = storage_matches[1].strip() if len(storage_matches) > 1 else "N/A"
            else:
                storage = storage_match.group(1).strip() if storage_match else "N/A"

            processor_match = re.search(r'(Core\s*i\d+|Ryzen\s*\d+|Intel|AMD|Celeron|Snapdragon|Apple\s*M\d+)', title, re.IGNORECASE)
            processor = processor_match.group(1).strip() if processor_match else "N/A"

            # 3. Clean Review Data for Sorting
            numeric_review = re.search(r'(\d+\.\d+|\d+)', review_text)
            review = float(numeric_review.group(1)) if numeric_review else None

            # 3b. Review count (e.g. "4.3 out of 5 stars 1,842 ratings" / "1,842 Ratings & 96 Reviews").
            #      Extra, additive column — nothing downstream depends on it, but the
            #      web UI ranks "most reviewed" and the assistant reads it when present.
            count_match = re.search(r'([\d,]+)\s*(?:ratings?|reviews?|count)', review_text, re.IGNORECASE)
            review_count = int(count_match.group(1).replace(',', '')) if count_match else 0

            # Append data to dictionary
            d['Title'].append(title)
            d['Price'].append(currentPrice)
            d['Link'].append(originalLink)
            d['RAM'].append(ram)
            d['Storage'].append(storage)
            d['Processor'].append(processor)
            d['Review'].append(review)
            d['Review_Count'].append(review_count)

            log(f"Successfully processed: {title[:30]}...")
        except Exception as e:
            log(f"Failed to parse file {file} due to: {e}")

    # Save cleaned dataframe to CSV
    if len(d['Title']) > 0:
        df = pd.DataFrame(data=d)
        df['Review'] = df['Review'].fillna(0)
        output_filename = output_file or os.path.join(PROJECT_ROOT, f"{site_name}_{product_input}_data_cleaned.csv")
        df.to_csv(output_filename, index=False, encoding="utf-8-sig")
        log(f"\nSuccess! Saved {len(df)} products to {output_filename}")
    else:
        log("\nWarning: No data was collected.")


# Manual testing engine — unchanged behaviour, now path-independent.
if __name__ == "__main__":
    standalone_site = input("Enter platform name (amazon/flipkart): ").strip().lower()
    standalone_input = input("Enter product folder name keywords (e.g., laptop): ").strip().lower()
    process_data(standalone_input, standalone_site)
