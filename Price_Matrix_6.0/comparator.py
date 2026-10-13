import os
import re
import pandas as pd

# ---------------------------------------------------------------------------
# Shared plumbing: one place that decides where the cleaned CSVs are read from
# and where compared_<product>_results.csv is written, plus live log routing.
# The matching / scoring logic below is byte-for-byte the original.
# ---------------------------------------------------------------------------
from paths import output_path
from logger import log


def compute_similarity(str1, str2):
    """ Calculates token-intersection overlap. """
    if not isinstance(str1, str) or not isinstance(str2, str):
        return 0.0
    words1 = set(re.sub(r'[^\w\s]', '', str1.lower()).split())
    words2 = set(re.sub(r'[^\w\s]', '', str2.lower()).split())
    if not words1 or not words2:
        return 0.0
    intersection = words1.intersection(words2)
    smaller_set_len = min(len(words1), len(words2))
    return len(intersection) / smaller_set_len


def clean_price_value(val):
    """ Defensively strips strings and safely handles N/A values. """
    if pd.isna(val):
        return None
    val_str = str(val).strip().lower()
    if any(marker in val_str for marker in ["no price", "n/a", "nan", "unknown", "free"]) or val_str == "":
        return None
    try:
        clean_digits = re.sub(r'[^\d]', '', val_str)
        return int(clean_digits) if clean_digits else None
    except ValueError:
        return None


def extract_specifications(title):
    """
    Extracts RAM, Storage, and Processor keywords from the text title using Regex.
    Converts metrics into a uniform lowercase format to ensure accurate evaluation blocks.
    """
    if not isinstance(title, str):
        return pd.Series([None, None, None])

    title_lower = title.lower()

    # 1. RAM Extraction (e.g., 4gb, 8 gb, 16gb ram)
    ram_match = re.search(r'\b(\d+)\s*gb\s*(?:ram|\b)', title_lower)
    ram = f"{ram_match.group(1)}gb" if ram_match else None

    # 2. Storage Extraction (e.g., 128gb, 256 gb ssd, 1tb, 512gb hdd)
    storage_match = re.search(r'\b(\d+)\s*(gb|tb)\s*(?:storage|ssd|hdd|rom|\b)', title_lower)
    storage = f"{storage_match.group(1)}{storage_match.group(2)}" if storage_match else None

    # 3. Processor Target Identification
    processor = None
    processors_list = ['i3', 'i5', 'i7', 'i9', 'ryzen 3', 'ryzen 5', 'ryzen 7', 'ryzen 9', 'snapdragon', 'm1', 'm2', 'm3', 'celeron']
    for proc in processors_list:
        if proc in title_lower:
            processor = proc.title()
            break

    return pd.Series([ram, storage, processor])


def _pick(row, candidates, default='N/A'):
    """Optional column reader — returns default when the column isn't present."""
    for col in candidates:
        if col in row and not pd.isna(row[col]):
            return row[col]
    return default


def run_price_comparison(product_keyword, amazon_file=None, flipkart_file=None, output_filename=None):
    """
    Cross-match Amazon vs Flipkart cleaned CSVs and write the comparison table.

    Parameters
    ----------
    product_keyword : str  the product used in the scrape (e.g. "laptop")
    amazon_file     : str  optional explicit path to the Amazon cleaned CSV.
                           Default: "amazon_<keyword>_data_cleaned.csv" next to this file.
    flipkart_file   : str  optional explicit path to the Flipkart cleaned CSV.
                           Default: "flipkart_<keyword>_data_cleaned.csv" next to this file.
    output_filename : str  optional explicit output path.
                           Default: "compared_<keyword>_results.csv" in the project root
                           (same name the web UI links and the exports read back).

    Returns the comparison DataFrame on success, or None when inputs are missing —
    a purely additive return value, so the CLI behaviour is unchanged.
    """
    folder_keyword = product_keyword.strip().lower().replace(" ", "_")
    amazon_file = amazon_file or output_path(f"amazon_{folder_keyword}_data_cleaned.csv")
    flipkart_file = flipkart_file or output_path(f"flipkart_{folder_keyword}_data_cleaned.csv")

    if not os.path.exists(amazon_file) or not os.path.exists(flipkart_file):
        log(f"[-] Missing structural clean source files: {amazon_file} or {flipkart_file}")
        return None

    log(f"[+] Loading processed data rows...")
    df_amz = pd.read_csv(amazon_file)
    df_fk = pd.read_csv(flipkart_file)

    # Dynamic Column Fallback
    amz_price_col = 'Numeric_Price' if 'Numeric_Price' in df_amz.columns else ('Price' if 'Price' in df_amz.columns else None)
    fk_price_col = 'Numeric_Price' if 'Numeric_Price' in df_fk.columns else ('Price' if 'Price' in df_fk.columns else None)

    if not amz_price_col or not fk_price_col:
        log("[-] Error: Missing recognizable pricing columns ('Price' or 'Numeric_Price') in source files.")
        return None

    # Apply price filters safely
    df_amz['Clean_Price'] = df_amz[amz_price_col].apply(clean_price_value)
    df_fk['Clean_Price'] = df_fk[fk_price_col].apply(clean_price_value)

    # Standardize string titles explicitly
    df_amz['Title'] = df_amz['Title'].fillna('Unknown Product').astype(str)
    df_fk['Title'] = df_fk['Title'].fillna('Unknown Product').astype(str)

    # FEATURE INJECTION: Extract hardware configurations to separate columns
    log("[*] Parsing hardware specifications (RAM, Storage, Processor) from text strings...")
    df_amz[['RAM', 'Storage', 'Processor']] = df_amz['Title'].apply(extract_specifications)
    df_fk[['RAM', 'Storage', 'Processor']] = df_fk['Title'].apply(extract_specifications)

    comparison_records = []
    SIMILARITY_THRESHOLD = 0.40

    log(f"[*] Cross-checking {len(df_amz)} Amazon products against {len(df_fk)} Flipkart items...")
    for amz_idx, amz_row in df_amz.iterrows():
        if pd.isna(amz_row['Clean_Price']):
            continue

        best_match_row = None
        highest_score = 0.0

        for _, fk_row in df_fk.iterrows():
            if pd.isna(fk_row['Clean_Price']):
                continue

            # HARDWARE VALIDATION BLOCK: Block variations with conflicting hardware details
            if amz_row['RAM'] and fk_row['RAM'] and amz_row['RAM'] != fk_row['RAM']:
                continue
            if amz_row['Storage'] and fk_row['Storage'] and amz_row['Storage'] != fk_row['Storage']:
                continue
            if amz_row['Processor'] and fk_row['Processor'] and amz_row['Processor'] != fk_row['Processor']:
                continue

            score = compute_similarity(amz_row['Title'], fk_row['Title'])
            if score > highest_score:
                highest_score = score
                best_match_row = fk_row

        if highest_score >= SIMILARITY_THRESHOLD and best_match_row is not None:
            amz_price = int(amz_row['Clean_Price'])
            fk_price = int(best_match_row['Clean_Price'])

            # Prevent extreme price disparities (e.g. matching accessories vs base units)
            if amz_price > 0 and fk_price > 0:
                ratio = max(amz_price, fk_price) / min(amz_price, fk_price)
                if ratio > 2.5:
                    continue

            price_diff = abs(amz_price - fk_price)
            if amz_price < fk_price:
                cheaper_platform = "Amazon"
                savings_pct = round(((fk_price - amz_price) / fk_price) * 100, 1)
            elif fk_price < amz_price:
                cheaper_platform = "Flipkart"
                savings_pct = round(((amz_price - fk_price) / amz_price) * 100, 1)
            else:
                cheaper_platform = "Tied"
                savings_pct = 0.0

            # BUILD COMPARISON RECORD WITH SEPARATE SPEC COLUMNS
            comparison_records.append({
                'Amazon_Title': amz_row['Title'],
                'Flipkart_Title': best_match_row['Title'],
                'Amazon_RAM': amz_row['RAM'],
                'Flipkart_RAM': best_match_row['RAM'],
                'Amazon_Storage': amz_row['Storage'],
                'Flipkart_Storage': best_match_row['Storage'],
                'Amazon_Processor': amz_row['Processor'],
                'Flipkart_Processor': best_match_row['Processor'],
                'Similarity_Score': round(highest_score, 2),
                'Amazon_Price': amz_price,
                'Flipkart_Price': fk_price,
                'Price_Difference': price_diff,
                'Cheaper_At': cheaper_platform,
                'Savings_Percentage': f"{savings_pct}%",
                'Amazon_Link': amz_row.get('Link', 'N/A'),
                'Flipkart_Link': best_match_row.get('Link', 'N/A'),
                # Optional pass-through columns (present only when parser.py wrote them).
                # They are appended AFTER the original 16 fields, so the classic CSV
                # header order is preserved for anything already consuming it.
                'Amazon_Review': _pick(amz_row, ['Review']),
                'Flipkart_Review': _pick(best_match_row, ['Review']),
                'Amazon_Review_Count': _pick(amz_row, ['Review_Count'], 0),
                'Flipkart_Review_Count': _pick(best_match_row, ['Review_Count'], 0),
            })

    if comparison_records:
        output_df = pd.DataFrame(comparison_records)
        output_df = output_df.sort_values(by='Price_Difference', ascending=False)
        output_filename = output_filename or output_path(f"compared_{folder_keyword}_results.csv")
        output_df.to_csv(output_filename, index=False, encoding="utf-8-sig")
        log(f"\n[+] Success! Found {len(output_df)} valid priced matches across platforms.")
        log(f"[+] Output compiled to: {output_filename}")
        return output_df

    log("\n[-] Warning: No products with valid numeric pricing structures matched.")
    return None


if __name__ == "__main__":
    product_query = input("Enter product keyword used during scrape runs: ").strip()
    run_price_comparison(product_query)
