from __future__ import annotations

from flask import Blueprint, jsonify, request

chatbot_bp = Blueprint("chatbot", __name__)

# Structured knowledge base for platform support and navigation
KNOWLEDGE_BASE = [
    {
        "keywords": ["how to use", "use the website", "getting started", "how it works", "guide", "help"],
        "answer": "To search and compare products:\n1. Type the item name into the 'Search Product' input.\n2. Choose how many pages per store to scrape.\n3. Click 'Compare' to start gathering live data from Amazon and Flipkart."
    },
    {
        "keywords": ["amazon", "flipkart", "platform", "store", "stores", "where"],
        "answer": "Price Ledger fetches and compares live pricing and listings from both Amazon India and Flipkart simultaneously."
    },
    {
        "keywords": ["download", "csv", "export", "excel", "save"],
        "answer": "After a search completes, click the 'Download CSV' button next to the system status indicator to save the comparative report."
    },
    {
        "keywords": ["filter", "sort", "cheapest", "price diff", "difference", "specs", "specifications"],
        "answer": "Use the table controls above the results matrix to filter listings by store, set min/max price thresholds, or search for key specifications like '16GB', 'i7', or 'Wireless'."
    },
    {
        "keywords": ["status", "seal", "idle", "scanning", "done", "progress"],
        "answer": "The system seal status shows:\n• 'Idle': System ready for search.\n• 'Scanning': Scrapers are fetching listings.\n• 'Done': Data processed and displayed."
    },
    {
        "keywords": ["product", "price", "rating", "reviews", "best price"],
        "answer": "The KPI summary cards display total parsed listings, minimum price on Amazon vs. Flipkart, and automatically highlight which store has the cheaper deal."
    }
]

def get_bot_response(user_text: str) -> str:
    query = user_text.lower().strip()
    if not query:
        return "Please type a question!"

    for item in KNOWLEDGE_BASE:
        if any(kw in query for kw in item["keywords"]):
            return item["answer"]

    return (
        "I can help answer questions about using the dashboard, searching products, "
        "filtering results, downloading CSV reports, or platform comparisons. How can I assist you?"
    )

@chatbot_bp.post("/api/chat")
def chat():
    body = request.get_json(force=True, silent=True) or {}
    message = (body.get("message") or "").strip()

    if not message:
        return jsonify({"error": "Message is required"}), 400

    reply = get_bot_response(message)
    return jsonify({"reply": reply})