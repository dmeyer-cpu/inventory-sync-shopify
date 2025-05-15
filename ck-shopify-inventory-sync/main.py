import requests
import xml.etree.ElementTree as ET
import time
import logging
import os
from dotenv import load_dotenv

import certifi
print("Certifi path:", certifi.where())

load_dotenv()

# --------------------------------
# Konfiguration (aus .env)
# --------------------------------
load_dotenv()

SHOPIFY_STORE = os.getenv("SHOPIFY_STORE")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
SHOPIFY_API_URL = f"https://{SHOPIFY_STORE}/admin/api/2024-04/graphql.json"
SHOPIFY_REST_API_URL = f"https://{SHOPIFY_STORE}/admin/api/2024-04"
XML_URL = os.getenv("XML_URL")
SHOPIFY_LOCATION_ID = os.getenv("SHOPIFY_LOCATION_ID")

# Shopify erlaubt max. 2 Requests pro Sekunde → 0.5 Sek Pause
REQUEST_DELAY = 0.5

# --------------------------------
# Logging Setup
# --------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

HEADERS_GRAPHQL = {
    "Content-Type": "application/json",
    "X-Shopify-Access-Token": ACCESS_TOKEN
}

HEADERS_REST = {
    "X-Shopify-Access-Token": ACCESS_TOKEN,
    "Content-Type": "application/json"
}

# --------------------------------
# Shopify Inventory laden
# --------------------------------
def load_shopify_inventory():
    logging.info("Lade alle Varianten (SKU + inventoryItemId + Quantity) von Shopify...")
    products = {}
    cursor = None
    has_next_page = True

    while has_next_page:
        after = f', after: "{cursor}"' if cursor else ''
        query = f"""
        {{
          products(first: 250{after}) {{
            pageInfo {{
              hasNextPage
            }}
            edges {{
              cursor
              node {{
                variants(first: 100) {{
                  edges {{
                    node {{
                      sku
                      inventoryItem {{
                        id
                      }}
                      inventoryQuantity
                    }}
                  }}
                }}
              }}
            }}
          }}
        }}
        """
        response = requests.post(SHOPIFY_API_URL, headers=HEADERS_GRAPHQL, json={"query": query}, verify=certifi.where())
        response.raise_for_status()
        result = response.json()["data"]["products"]

        for product in result["edges"]:
            for variant in product["node"]["variants"]["edges"]:
                variant_node = variant["node"]
                sku = variant_node["sku"]
                inventory_item_id = variant_node["inventoryItem"]["id"]
                quantity = variant_node["inventoryQuantity"]
                if sku:
                    products[sku] = {
                        "inventory_item_id": inventory_item_id.split("/")[-1],  # nur ID
                        "quantity": quantity
                    }

        has_next_page = result["pageInfo"]["hasNextPage"]
        if has_next_page:
            cursor = result["edges"][-1]["cursor"]

    logging.info(f"{len(products)} Varianten geladen.")
    return products

# --------------------------------
# XML Bestand laden
# --------------------------------
def load_xml_stock():
    logging.info("Lade XML Bestand von Falk & Ross...")
    response = requests.get(XML_URL, verify=certifi.where())
    response.raise_for_status()
    root = ET.fromstring(response.content)

    stock_data = {}
    for variant in root.findall('product_variant'):
        sku = variant.find('SKU').text.strip()
        quantity = 0
        for loc in variant:
            if loc.tag != 'SKU':
                qty = int(loc.find('Quantity').text)
                quantity += qty
        stock_data[sku] = quantity

    logging.info(f"{len(stock_data)} Produkte aus XML geladen.")
    return stock_data

# --------------------------------
# Shopify Inventory aktualisieren (REST API)
# --------------------------------
def update_inventory_item(inventory_item_id, new_quantity):
    url = f"{SHOPIFY_REST_API_URL}/inventory_levels/set.json"
    payload = {
        "location_id": SHOPIFY_LOCATION_ID,
        "inventory_item_id": inventory_item_id,
        "available": new_quantity
    }

    for attempt in range(3):
        try:
            response = requests.post(url, headers=HEADERS_REST, json=payload, verify=certifi.where())
            if response.status_code == 200:
                logging.info(f"✔ Bestand geändert: {inventory_item_id} → {new_quantity}")
                time.sleep(REQUEST_DELAY)
                break
            elif response.status_code == 429:
                logging.warning(f"⚠️ Shopify Rate Limit (Versuch {attempt+1}). Warte 2 Sekunden...")
                time.sleep(2)
            else:
                logging.warning(f"❌ Fehler beim Update (Versuch {attempt+1}): {response.text}")
                time.sleep(2)
        except Exception as e:
            logging.error(f"❌ Exception beim Update: {e}")
            time.sleep(2)

# --------------------------------
# Main Workflow
# --------------------------------
def main():
    shopify_data = load_shopify_inventory()
    xml_data = load_xml_stock()

    # Vorab alle Änderungen zählen
    total_changes_needed = sum(
        1 for sku, item in shopify_data.items()
        if sku in xml_data and item["quantity"] != xml_data[sku]
    )
    logging.info(f"Es sind {total_changes_needed} Änderungen notwendig.")

    updates = 0
    for sku, shopify_item in shopify_data.items():
        if sku in xml_data:
            xml_quantity = xml_data[sku]
            if shopify_item["quantity"] != xml_quantity:
                updates += 1
                progress_percent = (updates / total_changes_needed) * 100 if total_changes_needed > 0 else 100
                logging.info(f"[{updates}/{total_changes_needed} | {progress_percent:.2f}%] 🔧 SKU {sku}: {shopify_item['quantity']} → {xml_quantity}")
                update_inventory_item(shopify_item["inventory_item_id"], xml_quantity)

    logging.info(f"✅ Synchronisation abgeschlossen. {updates} Änderungen vorgenommen.")


if __name__ == "__main__":
    main()
