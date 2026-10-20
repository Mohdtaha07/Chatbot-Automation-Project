
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "spec_labels", "spec_key_order", "strip_labels", "GENERIC_SPEC_RULES",
    "Category",
    "CATEGORIES",
    "CATEGORY_BY_ID",
    "GROUP_ORDER",
    "detect_category",
    "detect_category_scored",
    "category_label",
    "category_group",
    "extract_specs",
    "specs_to_text",
    "spec_similarity",
    "variant_conflict",
    "refine_query",
    "catalog",
    "spec_keys_sorted",
    "query_matches_category",
    "is_accessory",
    "ACCESSORY_MARKERS",
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _norm(text: Any) -> str:
    t = str(text or "").lower()
    t = t.replace("\u00a0", " ")
    t = re.sub(r"[^a-z0-9.+\"'\s\-/]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _fmt_num(value: float) -> str:
    """1.0 -> '1', 1.50 -> '1.5', 190.0 -> '190'."""
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


def _titlecase_units(text: str) -> str:
    return text.strip()


# ---------------------------------------------------------------------------
# Shared vocabulary
# ---------------------------------------------------------------------------
COLOUR_WORDS = (
    "black", "white", "blue", "navy", "grey", "gray", "silver", "gold", "green",
    "red", "pink", "purple", "violet", "yellow", "orange", "brown", "beige",
    "marble", "titanium", "graphite", "midnight", "starlight", "champagne",
    "rose gold", "space grey", "space gray", "copper", "bronze", "teal", "mint",
    "lavender", "cream", "ivory", "multicolor", "multi",
)

MATERIAL_WORDS = (
    "cotton", "leather", "genuine leather", "faux leather", "denim", "polyester",
    "silk", "linen", "wool", "rayon", "viscose", "nylon", "spandex", "lycra",
    "stainless steel", "steel", "aluminium", "aluminum", "plastic", "abs",
    "polycarbonate", "glass", "ceramic", "wood", "sheesham", "teak", "engineered wood",
    "mdf", "particle board", "bamboo", "silicone", "rubber", "cast iron", "iron",
    "non-stick", "titanium", "carbon fibre", "carbon fiber", "velvet", "mesh",
)

SIZE_LETTERS = ("xxs", "xs", "s", "m", "l", "xl", "xxl", "xxxl", "3xl", "4xl", "5xl")

GENDER_WORDS = ("men", "mens", "men's", "women", "womens", "women's", "boys", "girls",
                "kids", "unisex", "baby", "adult")

_NUM = r"(\d+(?:\.\d+)?)"


# ---------------------------------------------------------------------------
# Category model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Category:
    """A product vertical plus the rules that make its listings comparable."""

    id: str
    label: str
    group: str
    emoji: str
    keywords: Tuple[str, ...]          # signals in the query/title
    query_hint: str = ""               # appended to a bare query for relevance
    example: str = ""                  # quick-chip label
    spec_rules: Tuple[Tuple[str, str, str], ...] = ()   # (key, label, regex)
    spec_order: Tuple[str, ...] = ()
    variant_keys: Tuple[str, ...] = ()  # differing values => different SKU
    brand_strict: bool = True           # False: don't punish brand mismatch
    unit_hint: Dict[str, str] = field(default_factory=dict)


def _r(key: str, label: str, pattern: str) -> Tuple[str, str, str]:
    return (key, label, pattern)


# The catalogue. `keywords` are matched as whole words/phrases against the
# normalized text; the longest match wins, which keeps "running shoes" from
# being swallowed by "shoes" or a "smart tv" from being read as a phone.
CATEGORIES: Tuple[Category, ...] = (
    # ---------------------------------------------------------------- computing
    Category(
        id="laptops", label="Laptops", group="Computers", emoji="💻",
        keywords=("laptop", "notebook", "ultrabook", "macbook", "chromebook",
                  "gaming laptop", "thinkpad", "ideapad", "vivobook", "pavilion"),
        query_hint="laptop", example="gaming laptop",
        spec_rules=(
            _r("cpu", "CPU", r"\b(core\s?i[3579](?:\s?-?\s?\d{4,5}\w*)?|ryzen\s?[3579](?:\s?-?\s?\d{3,4}\w*)?|apple\s?m[1-4](?:\s?(?:pro|max|ultra))?|snapdragon\s?x\s?\w+|celeron(?:\s?\w+)?|pentium(?:\s?\w+)?|athlon\s?\w+|intel\s?core\s?ultra\s?[3579]\w*)\b"),
            _r("gpu", "GPU", r"\b(rtx\s?\d{3,4}(?:\s?(?:ti|super))?|gtx\s?\d{3,4}(?:\s?(?:ti|super))?|radeon\s?(?:rx\s?)?\w*\s?\d{3,4}\w*|arc\s?a\d{3}\w*|iris\s?xe|geforce\s?\w+)\b"),
            _r("ram", "RAM", r"\b(?:(\d{1,3})\s?gb\s?(?:ddr\d?x?\s?)?(?:ram|memory|unified)|\b(?:ram|memory)\s?[:\-]?\s?(\d{1,3})\s?gb|(\d{1,3})\s?gb\s?(?:ddr\d?x?)\b)"),
            _r("storage", "Storage", r"\b(?:(\d{2,4})\s?gb|\b(\d{1,2})\s?tb)\s?(?:ssd|hdd|emmc|nvme|pcie|flash|rom|storage|hard\s?disk)"),
            _r("screen", "Screen", r"\b(\d{1,2}(?:\.\d+)?)\s?(?:inch|inches|\"|”|-inch|in)\b"),
            _r("refresh", "Refresh", r"\b(\d{2,3})\s?hz\b"),
            _r("os", "OS", r"\b(windows\s?1[01](?:\s?(?:home|pro|s))?|mac\s?os\s?\w*|macos\s?\w*|chrome\s?os|dos|ubuntu|linux)\b"),
        ),
        spec_order=("cpu", "gpu", "ram", "storage", "screen", "refresh", "os"),
        variant_keys=("ram", "storage", "gpu", "screen", "cpu"),
    ),
    Category(
        id="smartphones", label="Smartphones", group="Mobiles", emoji="📱",
        keywords=("smartphone", "mobile", "phone", "iphone", "galaxy s", "galaxy m",
                  "galaxy a", "redmi", "realme", "poco", "oneplus", "iqoo", "5g phone",
                  "feature phone", "keypad phone"),
        query_hint="smartphone", example="smartphone under 20000",
        spec_rules=(
            _r("cpu", "Chipset", r"\b(snapdragon\s?\d+\s?\w*|dimensity\s?\d+\w*|exynos\s?\d+\w*|helio\s?\w+\d*\w*|apple\s?a\d{2}\s?\w*|bionic\s?a?\d*|unisoc\s?\w*|tensor\s?\w*)\b"),
            _r("ram", "RAM", r"\b(?:(\d{1,3})\s?gb\s?ram|ram\s?[:\-]?\s?(\d{1,3})\s?gb)\b"),
            _r("storage", "Storage", r"\b(?:(\d{2,4})\s?gb|\b(\d{1,2})\s?tb)\s?(?:storage|rom|internal\s?memory|ufs)\b"),
            _r("battery", "Battery", r"\b(\d{4,5})\s?mah\b"),
            _r("camera", "Camera", r"\b(\d{1,3}(?:\s?mp)?)\s?mp\b"),
            _r("display", "Display", r"\b(\d\.\d{1,2})\s?(?:inch|inches|\"|”|display)\b"),
            _r("network", "Network", r"\b(5g|4g\s?volte|4g|3g)\b"),
            _r("refresh", "Refresh", r"\b(\d{2,3})\s?hz\b"),
        ),
        spec_order=("cpu", "ram", "storage", "camera", "battery", "display", "refresh", "network"),
        variant_keys=("ram", "storage", "network"),
    ),
    Category(
        id="tablets", label="Tablets", group="Mobiles", emoji="📲",
        keywords=("tablet", "ipad", "tab ", "tab s", "tab a", "matepad", "redmi pad", "realme pad"),
        query_hint="tablet", example="android tablet",
        spec_rules=(
            _r("ram", "RAM", r"\b(\d{1,3})\s?gb\s?ram\b"),
            _r("storage", "Storage", r"\b(?:(\d{2,4})\s?gb|\b(\d{1,2})\s?tb)\s?(?:storage|rom|internal)\b"),
            _r("screen", "Screen", r"\b(\d{1,2}(?:\.\d+)?)\s?(?:inch|inches|\"|”)\b"),
            _r("battery", "Battery", r"\b(\d{4,5})\s?mah\b"),
            _r("network", "Network", r"\b(5g|4g|wifi\s?only|wi-fi)\b"),
        ),
        spec_order=("ram", "storage", "screen", "battery", "network"),
        variant_keys=("ram", "storage", "network"),
    ),
    Category(
        id="computer-accessories", label="PC Accessories", group="Computers", emoji="🖱️",
        keywords=("monitor", "keyboard", "mouse", "printer", "router", "ups",
                  "external hard drive", "pen drive", "pendrive", "ssd", "webcam",
                  "projector", "scanner", "graphics card", "motherboard", "ram kit",
                  "power bank", "docking station", "cpu cooler", "cabinet"),
        query_hint="", example="mechanical keyboard",
        spec_rules=(
            _r("screen", "Size", r"\b(\d{2}(?:\.\d+)?)\s?(?:inch|inches|\"|”)\b"),
            _r("resolution", "Resolution", r"\b(8k|4k|uhd|qhd|2k|wuxga|full\s?hd|fhd|1080p|1440p|720p|hd)\b"),
            _r("refresh", "Refresh", r"\b(\d{2,3})\s?hz\b"),
            _r("capacity", "Capacity", r"\b(?:(\d{1,4})\s?gb|\b(\d{1,2})\s?tb)\b"),
            _r("interface", "Interface", r"\b(usb\s?3\.[0-2]|usb\s?type\s?c|type-c|usb|bluetooth\s?\d?\.?\d?|wireless|wired|2\.4\s?ghz|wi-fi\s?\d?|wifi\s?\d?|hdmi|vga|nvme|sata)\b"),
            _r("power", "Power", r"\b(\d{3,4})\s?(?:w|watt|watts|va)\b"),
        ),
        spec_order=("screen", "resolution", "refresh", "capacity", "interface", "power"),
        variant_keys=("capacity", "resolution", "screen"),
    ),
    Category(
        id="audio", label="Headphones & Speakers", group="Electronics", emoji="🎧",
        keywords=("headphone", "headphones", "earphone", "earphones", "earbuds", "earbud",
                  "neckband", "speaker", "soundbar", "tws", "airpods", "headset",
                  "bluetooth speaker", "party speaker"),
        query_hint="headphones", example="wireless earbuds",
        spec_rules=(
            _r("type", "Type", r"\b(in-ear|on-ear|over-ear|over ear|tws|true\s?wireless|neckband|earbuds?|headphones?|headset|soundbar|party\s?speaker|bluetooth\s?speaker|speaker)\b"),
            _r("battery", "Playback", r"\b(\d{1,3})\s?(?:hours?|hrs?|h)\s?(?:playback|playtime|battery)?\b"),
            _r("driver", "Driver", r"\b(\d{1,2}(?:\.\d+)?)\s?mm\s?(?:driver|dynamic|speaker)?\b"),
            _r("anc", "ANC", r"\b(anc|active\s?noise\s?cancellation|enc|noise\s?cancellation|noise\s?isolat\w+)\b"),
            _r("connectivity", "Connectivity", r"\b(bluetooth\s?\d?\.?\d?|bt\s?\d+\.\d|wireless|wired|2\.4\s?ghz|aux|usb\s?type\s?c|type-c)\b"),
            _r("power", "Power", r"\b(\d{1,4})\s?(?:w|watt|watts|rms)\b"),
            _r("impedance", "Impedance", r"\b(\d{2,3})\s?(?:ohm|ohms)\b"),
        ),
        spec_order=("type", "driver", "anc", "battery", "connectivity", "power", "impedance"),
        variant_keys=("type", "impedance"),
    ),
    Category(
        id="tv-video", label="TVs & Home Cinema", group="Electronics", emoji="📺",
        keywords=("smart tv", "led tv", "oled", "qled", "television", "tv ", "projector",
                  "set top box", "streaming device", "fire tv stick", "chromecast"),
        query_hint="smart tv", example="43 inch smart tv",
        spec_rules=(
            _r("screen", "Screen", r"\b(\d{2,3}(?:\.\d+)?)\s?(?:inch|inches|\"|”|-inch)(?![a-z0-9])"),
            _r("resolution", "Resolution", r"\b(8k|4k|uhd|full\s?hd\s?1080p|fhd|1080p|hd\s?ready|720p|hd)\b"),
            _r("panel", "Panel", r"\b(oled|qled|neo\s?qled|mini\s?led|led|nanocell|uled|ips|va)\b"),
            _r("refresh", "Refresh", r"\b(\d{2,3})\s?hz\b"),
            _r("smart", "Smart OS", r"\b(android\s?tv|google\s?tv|fire\s?tv|webos|tizen|roku\s?tv|smart\s?tv|linux\s?tv)\b"),
            _r("power", "Output", r"\b(\d{1,3})\s?w\s?(?:output|speaker|sound)?\b"),
        ),
        spec_order=("screen", "resolution", "panel", "refresh", "smart", "power"),
        variant_keys=("screen", "resolution", "panel"),
    ),
    # ------------------------------------------------------------ home & kitchen
    Category(
        id="large-appliances", label="Home Appliances", group="Home & Kitchen", emoji="🏠",
        keywords=("refrigerator", "fridge", "washing machine", "air conditioner", "ac ",
                  "split ac", "window ac", "microwave", "dishwasher", "water purifier",
                  "chimney", "geyser", "water heater", "dryer", "air cooler",
                  "vacuum cleaner", "trimmer"),
        query_hint="", example="1.5 ton 5 star ac",
        spec_rules=(
            _r("capacity", "Capacity", r"\b(\d{1,4}(?:\.\d+)?)\s?(ton|tons|tr)\b"),
            _r("volume", "Capacity", r"\b(\d{2,4}(?:\.\d+)?)\s?(l|ltr|litre|liter|litres|liters)\b"),
            _r("star", "Star Rating", r"\b([1-5])\s?star\b"),
            _r("appliance_type", "Type", r"\b(inverter|split\s?ac|window\s?ac|front\s?load|top\s?load|semi\s?automatic|fully\s?automatic|single\s?door|double\s?door|side\s?by\s?side|multi\s?door|direct\s?cool|frost\s?free|solo|grill|convection|ro\s?\+?\s?uv?|ro|uv|uf|convection|tower|ducted)\b"),
            _r("kg", "Load", r"\b(\d{1,2}(?:\.\d+)?)\s?kg\b"),
            _r("power", "Power", r"\b(\d{3,5})\s?(?:w|watt|watts)\b"),
            _r("energy", "Energy", r"\b(\d\s?star|\d\.\d\s?(?:iseer|eerr))\b"),
            _r("material", "Material", r"\b(copper|stainless\s?steel|aluminium)\b"),
        ),
        spec_order=("appliance_type", "capacity", "volume", "star", "kg", "power", "material", "energy"),
        variant_keys=("capacity", "volume", "star", "kg", "appliance_type"),
    ),
    Category(
        id="kitchen", label="Kitchen & Dining", group="Home & Kitchen", emoji="🍳",
        keywords=("mixer grinder", "mixie", "air fryer", "kettle", "cookware", "non stick",
                  "frying pan", "pressure cooker", "cooker", "casserole", "blender",
                  "food processor", "chopper", "ottoman", "induction cooktop", "toaster",
                  "sandwich maker", "juicer", "idli", "dinner set", "cooker", "tawa",
                  "knife", "bottle", "lunch box", "storage container", "roti maker"),
        query_hint="", example="air fryer",
        spec_rules=(
            _r("product_type", "Type", r"\b(mixer\s?grinder|mixie|air\s?fryer|electric\s?kettle|induction\s?cooktop|induction\s?stove|pressure\s?cooker|cooker|frying\s?pan|non[\s-]?stick\s?\w*|casserole|blender|food\s?processor|chopper|toaster|sandwich\s?maker|juicer|idli\s?maker|roti\s?maker|dinner\s?set|knife\s?set|water\s?bottle|lunch\s?box|storage\s?container|tawa|kadai)\b"),
            _r("volume", "Capacity", r"\b(\d+(?:\.\d+)?)\s?(l|ltr|litre|liter|litres|liters|ml)\b"),
            _r("power", "Power", r"\b(\d{3,4})\s?(?:w|watt|watts)\b"),
            _r("jars", "Jars", r"\b(\d)\s?(?:jars?|jar)\b"),
            _r("pieces", "Pieces", r"\b(?:pack\s?of\s?(\d{1,3})|set\s?of\s?(\d{1,3})|(\d{1,3})\s?(?:pcs|pieces|pc)\b)"),
            _r("material", "Material", r"\b(stainless\s?steel|non[\s-]?stick|cast\s?iron|aluminium|granite|ceramic|glass|copper|steel)\b"),
            _r("induction", "Induction Ready", r"\b(induction\s?(?:compatible|ready|base)|gas\s?&\s?induction)\b"),
        ),
        spec_order=("product_type", "volume", "power", "jars", "pieces", "material"),
        variant_keys=("volume", "jars", "pieces"),
    ),
    Category(
        id="furniture", label="Furniture", group="Home & Kitchen", emoji="🛋️",
        keywords=("sofa", "chair", "table", "bed", "mattress", "wardrobe", "bookshelf",
                  "study table", "dining set", "recliner", "ottoman", "couch", "shelf",
                  "bean bag", "footstool", "dressing table"),
        query_hint="", example="office chair",
        spec_rules=(
            _r("furniture_type", "Type", r"\b(3\s?seater|2\s?seater|single\s?seater|sofa\s?set|recliner|rocking|office\s?chair|gaming\s?chair|study\s?table|coffee\s?table|dining\s?table|queen|king|single\s?bed|double\s?bed)\b"),
            _r("material", "Material", r"\b(sheesham|teak|pine|engineered\s?wood|solid\s?wood|mdf|particle\s?board|metal|velvet|leatherette|fabric|foam)\b"),
            _r("dimensions", "Size", r"\b(\d{2,4}(?:\.\d+)?)\s?[x×]\s?(\d{2,4}(?:\.\d+)?)(?:\s?[x×]\s?(\d{2,4}(?:\.\d+)?))?\s?(cm|mm|inch|ft|in)?\b"),
            _r("thickness", "Thickness", r"\b(\d{1,3})\s?(?:mm|inch|in)\s?(?:thick|thickness)\b"),
        ),
        spec_order=("furniture_type", "material", "dimensions", "thickness"),
        variant_keys=("furniture_type", "dimensions"),
    ),
    # ------------------------------------------------------------------- fashion
    Category(
        id="footwear", label="Footwear", group="Fashion", emoji="👟",
        keywords=("shoes", "shoe", "sneakers", "sneaker", "running shoes", "sandals",
                  "flip flops", "slippers", "flip-flops", "heels", "loafers", "boots",
                  "sports shoes", "slipper", "clogs", "ballerinas", "ethnic footwear"),
        query_hint="shoes", example="running shoes",
        spec_rules=(
            _r("shoe_type", "Type", r"\b(running|walking|sneakers?|casual|formal|sports|training|basketball|football|hiking|loafers?|sandals?|slippers?|flip[\s-]?flops?|heels?|boots?|clogs?|ballerinas?)\b"),
            _r("size", "Size", r"\b(?:uk|us|eu|size)\s?[:\-]?\s?(\d{1,2}(?:\.\d+)?|[456][5]?)\b"),
            _r("gender", "For", r"\b(men|mens|men's|women|womens|women's|boys|girls|kids|unisex|baby)\b"),
            _r("material", "Material", r"\b(leather|genuine\s?leather|faux\s?leather|mesh|canvas|synthetic|rubber|knit|suede|evA|eva|pvc)\b"),
            _r("colour", "Colour", r"\b(" + "|".join(re.escape(c) for c in COLOUR_WORDS) + r")\b"),
        ),
        spec_order=("shoe_type", "gender", "size", "material", "colour"),
        variant_keys=("size", "colour", "shoe_type"),
    ),
    Category(
        id="clothing", label="Clothing", group="Fashion", emoji="👕",
        keywords=("t-shirt", "tshirt", "shirt", "jeans", "kurta", "saree", "dress",
                  "top", "bottom", "trousers", "chinos", "shorts", "track pants",
                  "hoodie", "sweatshirt", "sweater", "jacket", "blazer", "lehenga",
                  "salwar", "dupatta", "night suit", "pajama", "trunk", "brief",
                  "innerwear", "sportswear", "co-ord", "co ord"),
        query_hint="", example="men cotton t-shirt",
        spec_rules=(
            _r("garment", "Type", r"\b(t[\s-]?shirts?|shirts?|jeans|kurtas?|kurtis?|sarees?|dress(?:es)?|tops?|trousers?|chinos|shorts|track\s?pants|joggers|hoodies?|sweatshirts?|sweaters?|jackets?|blazers?|lehengas?|salwar|dupatta|night\s?suit|pyjamas?|pajamas?|trunks?|briefs?|co[\s-]?ord\s?sets?)\b"),
            _r("size", "Size", r"\b(?:size|uk|us)\s?[:\-]?\s?(xxs|xs|s|m|l|xl|xxl|xxxl|[3-5]xl|\d{2})\b"),
            _r("fit", "Fit", r"\b(slim\s?fit|regular\s?fit|relaxed\s?fit|skinny|straight\s?fit|oversized|bodycon|a[\s-]?line|high\s?rise|mid\s?rise)\b"),
            _r("gender", "For", r"\b(men|mens|men's|women|womens|women's|boys|girls|kids|unisex|baby)\b"),
            _r("material", "Fabric", r"\b(cotton|pure\s?cotton|denim|polyester|silk|linen|wool|rayon|viscose|nylon|spandex|lycra|chiffon|georgette|crepe|flannel|terry\s?cotton)\b"),
            _r("pattern", "Pattern", r"\b(solid|printed|striped|checked|floral|embroidered|plain|self\s?design|graphic|typography)\b"),
            _r("colour", "Colour", r"\b(" + "|".join(re.escape(c) for c in COLOUR_WORDS) + r")\b"),
            _r("sleeve", "Sleeve", r"\b(half\s?sleeve|full\s?sleeve|sleeveless|three[\s-]?quarter\s?sleeve|short\s?sleeve|long\s?sleeve)\b"),
        ),
        spec_order=("garment", "gender", "fit", "size", "sleeve", "material", "pattern", "colour"),
        variant_keys=("size", "colour", "garment", "fit"),
    ),
    Category(
        id="watches", label="Watches & Wearables", group="Fashion", emoji="⌚",
        keywords=("watch", "smartwatch", "smart watch", "band", "fitness tracker",
                  "fitness band", "analog watch", "chronograph"),
        query_hint="", example="smartwatch",
        spec_rules=(
            _r("watch_type", "Type", r"\b(smart\s?watch|smartwatch|analog|digital|analogue|chronograph|automatic|quartz|fitness\s?(?:band|tracker))\b"),
            _r("display", "Display", r"\b(\d\.\d{1,2})\s?(?:inch|inches|\"|”|amoled|lcd|tft)|(amoled|lcd|tft|e[\s-]?ink)\b"),
            _r("strap", "Strap", r"\b(leather\s?strap|silicone\s?strap|metal\s?strap|stainless\s?steel\s?strap|rubber\s?strap|nylon\s?strap|bracelet|strap)\b"),
            _r("dial", "Dial", r"\b(\d{2})\s?mm\b"),
            _r("water", "Water Resist", r"\b(\d{1,2})\s?atm\b|\b(ip6[78]|5\s?atm|water\s?resist\w*)\b"),
            _r("battery", "Battery", r"\b(\d{1,3})\s?(?:days?|hours?|hrs?)\s?(?:battery|backup)?\b"),
        ),
        spec_order=("watch_type", "display", "dial", "strap", "water", "battery"),
        variant_keys=("watch_type", "dial"),
    ),
    Category(
        id="bags-luggage", label="Bags & Luggage", group="Fashion", emoji="🎒",
        keywords=("backpack", "bag", "handbag", "trolley", "suitcase", "luggage",
                  "wallet", "duffel", "rucksack", "messenger bag", "laptop bag",
                  "tote", "sling bag", "travel bag"),
        query_hint="", example="laptop backpack",
        spec_rules=(
            _r("bag_type", "Type", r"\b(backpack|rucksack|handbag|tote|sling|messenger|duffel|trolley|suitcase|hard\s?case|soft\s?case|wallet|clutch)\b"),
            _r("volume", "Capacity", r"\b(\d{1,3})\s?(?:l|ltr|litre|liter|litres|liters)\b"),
            _r("size", "Size", r"\b(\d{2})\s?(?:inch|in|\")\b"),
            _r("material", "Material", r"\b(polyester|nylon|leather|faux\s?leather|canvas|polycarbonate|abs|denim|jute|microfibre)\b"),
            _r("wheels", "Wheels", r"\b(\d)\s?wheels?\b"),
            _r("laptop_compat", "Laptop Fit", r"\b(\d{2}(?:\.\d+)?)\s?(?:inch|in|\")\s?(?:laptop|sleeve|compartment)(?![a-z])"),
        ),
        spec_order=("bag_type", "volume", "size", "laptop_compat", "wheels", "material"),
        variant_keys=("volume", "size", "bag_type"),
    ),
    Category(
        id="beauty", label="Beauty & Personal Care", group="Beauty & Health", emoji="💄",
        keywords=("face wash", "cleanser", "shampoo", "conditioner", "serum", "cream",
                  "moisturiser", "moisturizer", "sunscreen", "lipstick", "perfume",
                  "deodorant", "body wash", "hair oil", "face mask", "kajal", "eyeliner",
                  "foundation", "nail polish", "razor", "epilator", "hair dryer",
                  "straightener", "toothpaste", "soap", "talc", "beard oil"),
        query_hint="", example="vitamin c face serum",
        spec_rules=(
            _r("product_type", "Type", r"\b(face\s?wash|cleanser|shampoo|conditioner|serum|moisturiser|moisturizer|cream|lotion|sunscreen|lipstick|perfume|deodorant|body\s?wash|hair\s?oil|face\s?mask|kajal|eyeliner|foundation|nail\s?polish|hair\s?dryer|straightener|trimmer|epilator|toothpaste|soap)\b"),
            _r("volume", "Size", r"\b(\d+(?:\.\d+)?)\s?(ml|l|ltr|litre|liter|g|gm|gram|grams|oz)\b"),
            _r("spf", "SPF", r"\bspf\s?(\d{1,3})\b"),
            _r("skin_hair", "For", r"\b(oily|dry|normal|combination|sensitive|acne[\s-]?prone|anti[\s-]?aging|dandruff|hair\s?fall|damaged|curly|straight|coloured|colored|all\s?skin)\b"),
            _r("shade", "Shade", r"\b(shade\s?\w+|nude|matte|glossy|natural\s?beige|ivory|tan|berry|red\s?\d*)\b"),
            _r("power", "Power", r"\b(\d{3,4})\s?(?:w|watt|watts)\b"),
        ),
        spec_order=("product_type", "volume", "spf", "skin_hair", "shade", "power"),
        variant_keys=("volume", "spf", "shade"),
    ),
    Category(
        id="health", label="Health & Nutrition", group="Beauty & Health", emoji="💊",
        keywords=("supplement", "protein", "whey", "vitamin", "tablets", "capsules",
                  "thermometer", "bp monitor", "glucometer", "oximeter", "massager",
                  "sanitizer", "first aid", "multivitamin", "omega", "creatine",
                  "shaker", "weighing scale", "nebulizer", "heating pad"),
        query_hint="", example="whey protein",
        spec_rules=(
            _r("form", "Form", r"\b(tablets?|capsules?|powder|syrup|liquid|sachets?|softgels?|gummies|drops|spray)\b"),
            _r("pack", "Pack", r"\b(?:pack\s?of\s?(\d{1,3})|(\d{1,3})\s?(?:tablets?|capsules?|sachets?|softgels?|servings?|count))\b"),
            _r("weight", "Net Wt", r"\b(\d+(?:\.\d+)?)\s?(kg|g|gm|grams|lb|lbs)\b"),
            _r("volume", "Volume", r"\b(\d+(?:\.\d+)?)\s?(ml|l|litre|liter)\b"),
            _r("flavour", "Flavour", r"\b(chocolate|vanilla|mango|strawberry|butterscotch|kesar|elaichi|coffee|orange|lemon|unflavoured|unflavored|berry)\b"),
            _r("dosage", "Dosage", r"\b(\d{2,4})\s?(?:mg|mcg|iu|g)\b"),
            _r("diet", "Diet", r"\b(vegan|vegetarian|gluten[\s-]?free|sugar[\s-]?free|keto|organic)\b"),
        ),
        spec_order=("form", "pack", "weight", "volume", "dosage", "flavour", "diet"),
        variant_keys=("weight", "volume", "flavour", "pack"),
    ),
    # --------------------------------------------------------------- media etc.
    Category(
        id="books", label="Books", group="Books & Media", emoji="📚",
        keywords=("book", "books", "paperback", "hardcover", "hardback", "novel",
                  "kindle edition", "audiobook", "comic", "manga", "dictionary",
                  "encyclopedia", "guide to", "workbook"),
        query_hint="book", example="atomic habits book",
        spec_rules=(
            _r("format", "Format", r"\b(paperback|paper\s?back|hardcover|hard\s?cover|hardbound|kindle\s?edition|ebook|e-book|audiobook|board\s?book|boxed\s?set)\b"),
            _r("language", "Language", r"\b(english|hindi|tamil|telugu|marathi|bengali|kannada|malayalam|gujarati|urdu|spanish|french|german)\b"),
            _r("pages", "Pages", r"\b(\d{2,4})\s?pages?\b"),
            _r("edition", "Edition", r"\b(\d{1,2}(?:st|nd|rd|th)\s?edition|revised\s?edition|anniversary\s?edition|special\s?edition)\b"),
            _r("publisher", "Publisher", r"\b(penguin|harpercollins|harper\s?collins|bloomsbury|oxford|scholastic|prakash|westland|rupa|jaico|bantam|wiley|pearson)\b"),
            _r("series", "Series", r"\b(series|volume\s?\d+|book\s?\d+|part\s?\d+)\b"),
            _r("award", "Note", r"\b(bestseller|best\s?seller|award[\s-]?winning|national\s?book\s?award|pulitzer|booker)\b"),
        ),
        spec_order=("format", "language", "pages", "edition", "publisher", "award"),
        variant_keys=("format", "language", "edition"),
        brand_strict=False,   # "brand" on a book is really the publisher — noisy
    ),
    Category(
        id="toys-games", label="Toys, Games & Baby", group="Toys & Baby", emoji="🧸",
        keywords=("toy", "toys", "puzzle", "lego", "board game", "doll", "rc car",
                  "remote control car", "building blocks", "soft toy", "baby",
                  "diapers", "pram", "stroller", "cradle", "baby monitor", "play mat",
                  "action figure", "card game", "ludo", "chess"),
        query_hint="", example="building blocks",
        spec_rules=(
            _r("toy_type", "Type", r"\b(puzzle|board\s?game|card\s?game|building\s?blocks?|lego|doll|action\s?figure|rc\s?car|remote\s?control|soft\s?toy|plush|stroller|pram|diapers?|play\s?mat|ride[\s-]?on)\b"),
            _r("age", "Age", r"\b(\d{1,2})\s?(?:\+)?\s?(?:years?|yrs?|months?)\b"),
            _r("pieces", "Pieces", r"\b(?:(\d{1,4})\s?(?:pcs|pieces|blocks|cards|count))"),
            _r("material", "Material", r"\b(wood|wooden|plastic|abs|foam|plush|fabric|silicone|magnetic)\b"),
            _r("battery", "Battery", r"\b(\d{1,2})\s?(?:aa|aaa|batteries)\b"),
        ),
        spec_order=("toy_type", "age", "pieces", "material", "battery"),
        variant_keys=("toy_type", "pieces", "age"),
    ),
    Category(
        id="sports", label="Sports & Fitness", group="Sports & Outdoors", emoji="🏋️",
        keywords=("yoga mat", "dumbbell", "treadmill", "cycle", "bicycle", "gym",
                  "protein shaker", "cricket bat", "football", "badminton", "racket",
                  "skipping rope", "resistance band", "kettlebell", "exercise bike",
                  "tent", "sleeping bag", "camping", "helmet", "skates", "swimming"),
        query_hint="", example="yoga mat",
        spec_rules=(
            _r("sport_type", "Type", r"\b(yoga\s?mat|dumbbell|treadmill|exercise\s?bike|cycle|bicycle|cricket\s?bat|football|badminton|racket|racquet|skipping\s?rope|resistance\s?band|kettlebell|tent|sleeping\s?bag|helmet|skates|swimsuit|goggles)\b"),
            _r("weight", "Weight", r"\b(\d+(?:\.\d+)?)\s?(kg|g|gm|lb|lbs)\b"),
            _r("size", "Size", r"\b(\d{1,3})\s?(?:mm|cm|inch|in|ft|m)(?![a-z0-9])"),
            _r("material", "Material", r"\b(tpe|eva|nbr|pvc|rubber|steel|cast\s?iron|neoprene|silicone|carbon\s?fibre|carbon\s?fiber|aluminium|willow|leather)\b"),
            _r("level", "Level", r"\b(beginner|intermediate|advanced|professional|pro)\b"),
        ),
        spec_order=("sport_type", "weight", "size", "material", "level"),
        variant_keys=("weight", "size", "sport_type"),
    ),
    Category(
        id="grocery", label="Grocery & Gourmet", group="Grocery", emoji="🛒",
        keywords=("dal", "rice", "atta", "flour", "oil", "ghee", "masala", "spice",
                  "tea", "coffee", "snacks", "biscuit", "chocolate", "dry fruit",
                  "nuts", "honey", "sugar", "salt", "pasta", "noodles", "cereal",
                  "peanut butter", "sauce", "jam", "juice", "milk powder", "besan",
                  "poha", "suji", "namkeen", "chips", "instant food"),
        query_hint="", example="almonds 1kg",
        spec_rules=(
            _r("weight", "Net Wt", r"\b(\d+(?:\.\d+)?)\s?(kg|kgs|g|gm|gms|grams|gram|lb|lbs)\b"),
            _r("volume", "Volume", r"\b(\d+(?:\.\d+)?)\s?(ml|l|ltr|litre|liter|litres)\b"),
            _r("pack", "Pack", r"\b(?:pack\s?of\s?(\d{1,3})|(\d{1,3})\s?(?:x\s?\d+\s?(?:g|gm|ml|kg|l)|packs?|units?|pouches?))\b"),
            _r("food_type", "Type", r"\b(unpolished|polished|organic|natural|roasted|salted|unsalted|whole|ground|refined|cold[\s-]?pressed|virgin|instant|ready\s?to\s?eat|flavoured|flavored)\b"),
            _r("flavour", "Flavour", r"\b(chocolate|vanilla|mango|strawberry|butterscotch|kesar|elaichi|coffee|orange|lemon|masala|pudina|tomato|cheese|peri\s?peri|barbecue|salted caramel)\b"),
            _r("diet", "Diet", r"\b(vegan|vegetarian|gluten[\s-]?free|sugar[\s-]?free|no\s?added\s?sugar|keto|jain|halal)\b"),
        ),
        spec_order=("weight", "volume", "pack", "food_type", "flavour", "diet"),
        variant_keys=("weight", "volume", "flavour", "pack"),
    ),
    Category(
        id="cameras", label="Cameras", group="Electronics", emoji="📷",
        keywords=("camera", "dslr", "mirrorless", "gopro", "action camera", "lens",
                  "camcorder", "webcam", "drone", "dash cam"),
        query_hint="camera", example="mirrorless camera",
        spec_rules=(
            _r("camera_type", "Type", r"\b(dslr|mirrorless|action\s?camera|point\s?and\s?shoot|compact|drone|dash\s?cam|webcam|camcorder)\b"),
            _r("megapixels", "MP", r"\b(\d{2,3}(?:\.\d+)?)\s?mp\b"),
            _r("zoom", "Zoom", r"\b(\d{1,3})x\s?(?:optical|digital|zoom)?\b"),
            _r("video", "Video", r"\b(8k|4k|2\.7k|2k|1080p|full\s?hd)\b"),
            _r("sensor", "Sensor", r"\b(aps[\s-]?c|full[\s-]?frame|1\s?inch|micro\s?four\s?thirds|cmos|bsi)\b"),
            _r("lens", "Lens", r"\b(\d{1,3}\s?-\s?\d{2,3})\s?mm\b|\b(\d{2})\s?mm\s?(?:f\/?\d(?:\.\d+)?)?\b"),
        ),
        spec_order=("camera_type", "megapixels", "sensor", "video", "zoom", "lens"),
        variant_keys=("camera_type", "megapixels", "lens"),
    ),
    Category(
        id="gaming", label="Gaming", group="Electronics", emoji="🎮",
        keywords=("playstation", "ps5", "ps4", "xbox", "nintendo", "switch",
                  "gaming console", "gamepad", "controller", "game cd", "game disk"),
        query_hint="", example="ps5 controller",
        spec_rules=(
            _r("platform", "Platform", r"\b(ps5|ps4|ps3|playstation\s?[2-5]|xbox(?:\s?(?:series\s?[xs]|one|360))?|nintendo\s?switch|switch\s?(?:oled|lite))\b"),
            _r("console_type", "Type", r"\b(console|controller|gamepad|headset|game\s?(?:cd|disk|disc)|digital\s?edition|disc\s?edition)\b"),
            _r("edition", "Edition", r"\b(digital\s?edition|disc\s?edition|standard\s?edition|deluxe|ultimate|bundled?|bundle)\b"),
            _r("storage", "Storage", r"\b(?:(\d{2,4})\s?gb|\b(\d{1,2})\s?tb)\b"),
        ),
        spec_order=("platform", "console_type", "edition", "storage"),
        variant_keys=("platform", "console_type", "edition"),
    ),
    Category(
        id="automotive", label="Car & Bike Care", group="Automotive", emoji="🚗",
        keywords=("engine oil", "car cover", "bike cover", "tyre", "tire", "helmet",
                  "car care", "bike care", "horn", "car charger", "seat cover",
                  "dash cam", "jump starter", "car vacuum", "wiper", "sunshade"),
        query_hint="", example="engine oil",
        spec_rules=(
            _r("volume", "Volume", r"\b(\d+(?:\.\d+)?)\s?(l|ltr|litre|liter|ml)\b"),
            _r("vehicle_type", "For", r"\b(car|bike|motorcycle|scooter|suv|sedan|hatchback|auto)\b"),
            _r("oil_grade", "Grade", r"\b(\d{1,2}w[\s-]?\d{2}|\d{2}w[\s-]?\d{2})\b"),
            _r("size", "Size", r"\b(\d{2,3}(?:\.\d+)?)\s?(?:cm|mm|inch|in|\")\b"),
            _r("watt", "Power", r"\b(\d{2,4})\s?(?:w|watt|watts|cc)\b"),
        ),
        spec_order=("vehicle_type", "oil_grade", "volume", "size", "watt"),
        variant_keys=("volume", "oil_grade", "size"),
    ),
    Category(
        id="office-supplies", label="Office & Stationery", group="Office", emoji="🖇️",
        keywords=("notebook", "diary", "pen", "pencil", "stationery", "paper",
                  "a4 paper", "a4 size", "a4", "printer paper", "gsm", "sheets",
                  "file folder", "stapler", "marker", "highlighter",
                  "sticky notes", "whiteboard", "calculator", "printer cartridge"),
        query_hint="", example="a4 printer paper",
        spec_rules=(
            _r("paper_size", "Size", r"\b(a4|a3|a5|a6|letter|legal)\b"),
            _r("sheets", "Sheets", r"\b(\d{2,4})\s?(?:sheets|pages|pcs|pieces)\b"),
            _r("gsm", "GSM", r"\b(\d{2,3})\s?gsm\b"),
            _r("ruling", "Ruling", r"\b(single\s?line|double\s?line|ruled|unruled|plain|dotted|graph|four\s?line)\b"),
            _r("colour", "Colour", r"\b(" + "|".join(re.escape(c) for c in COLOUR_WORDS) + r")\b"),
        ),
        spec_order=("paper_size", "ruling", "gsm", "sheets", "colour"),
        variant_keys=("paper_size", "ruling", "sheets"),
    ),
)

CATEGORY_BY_ID: Dict[str, Category] = {c.id: c for c in CATEGORIES}

GROUP_ORDER = (
    "Computers", "Mobiles", "Electronics", "Home & Kitchen", "Fashion",
    "Beauty & Health", "Grocery", "Books & Media", "Toys & Baby",
    "Sports & Outdoors", "Automotive", "Office", "General",
)

# Group used by the UI when a product's category could not be determined.
GENERAL_CATEGORY = Category(
    id="general", label="General", group="General", emoji="📦",
    keywords=(), query_hint="", example="",
    spec_order=(),
)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
# A few high-precision phrases that should beat a single generic keyword.
_STRONG_PHRASES: Tuple[Tuple[str, str], ...] = (
    ("wireless earbuds", "audio"), ("bluetooth speaker", "audio"),
    ("noise cancellation", "audio"), ("soundbar", "audio"),
    ("smart tv", "tv-video"), ("led tv", "tv-video"), ("oled tv", "tv-video"),
    ("air fryer", "kitchen"), ("mixer grinder", "kitchen"),
    ("washing machine", "large-appliances"), ("air conditioner", "large-appliances"),
    ("water purifier", "large-appliances"), ("vacuum cleaner", "large-appliances"),
    ("running shoes", "footwear"), ("sports shoes", "footwear"),
    ("face wash", "beauty"), ("hair dryer", "beauty"),
    ("protein powder", "health"), ("whey protein", "health"),
    ("yoga mat", "sports"), ("office chair", "furniture"),
    ("board game", "toys-games"), ("building blocks", "toys-games"),
    ("external hard drive", "computer-accessories"), ("power bank", "computer-accessories"),
    ("engine oil", "automotive"), ("a4 paper", "office-supplies"),
    ("laptop bag", "bags-luggage"), ("laptop backpack", "bags-luggage"),
)

# Brand words that appear in several verticals ("Redmi" phone *and* RedmiBook,
# "OnePlus" phone *and* OnePlus Bullets earbuds). They hint, they don't decide —
# so they carry a fraction of the weight of a descriptive keyword.
# Keywords that are true but too common to decide a category alone — an SSD
# string appears in most laptop titles, which used to send them to
# "PC Accessories" instead of "Laptops".
_WEAK_KEYWORDS = {"ssd", "hdd", "printer", "paper", "gaming", "display", "keypad phone"}

_CROSS_BRAND_KEYWORDS = {
    "redmi", "realme", "poco", "oneplus", "iqoo", "samsung", "apple", "xiaomi",
    "mi ", "oppo", "vivo", "infinix", "nokia", "motorola", "honor", "nothing",
    "hp", "dell", "lenovo", "asus", "acer", "msi", "noise", "boat",
}

# Words that show up in almost every listing / query and must not be treated
# as category signals on their own (they were the v2 stopword trap).
_CATEGORY_STOP = {
    "product", "buy", "online", "best", "price", "india", "new", "latest",
    "original", "combo", "pack", "set", "with", "and", "for", "the",
}


# Spec keys that genuinely define a vertical. A category can only win the
# spec vote if at least one of these fires — "Pack of 3" + "500 g" describe
# almost anything, so they must not be enough to label a product.
_SIGNATURE_KEYS = {
    "cpu", "gpu", "os", "camera", "network", "anc", "driver", "impedance",
    "jars", "induction", "shoe_type", "garment", "fit", "sleeve", "pattern",
    "watch_type", "strap", "bag_type", "product_type", "form", "spf",
    "skin_hair", "flavour", "food_type", "diet", "format", "language", "pages",
    "edition", "publisher", "toy_type", "sport_type", "furniture_type",
    "camera_type", "console_type", "platform", "oil_grade", "vehicle_type",
    "paper_size", "ruling", "gsm", "appliance_type", "megapixels",
    "smart", "energy", "shade",
    "series", "award", "certification", "paper_size", "ruling", "type",
}


def _signature_vote(text: str, cat: "Category") -> int:
    """How many category-defining (not generic) spec rules fire."""
    n = 0
    for key, _label, pattern in cat.spec_rules:
        if key in _SIGNATURE_KEYS and re.search(pattern, text, re.I):
            n += 1
    return n


def _spec_vote(text: str, cat: "Category") -> float:
    """
    Count how many of a category's *spec* rules fire on the text.

    Titles like "ASUS TUF Gaming F15 - Core i5 11th Gen (16GB/512GB SSD/144Hz)"
    never say the word "laptop", so keyword scoring alone dumped them into PC
    Accessories. Their GTX/144Hz/Windows spec talk is a much stronger signal.
    """
    hits = 0.0
    for key, _label, pattern in cat.spec_rules:
        if key in {"model", "colour", "material", "gender"}:
            continue
        if re.search(pattern, text, re.I):
            hits += 2.0 if key in _SIGNATURE_KEYS else 1.0
    return hits


def detect_category_scored(text: Any) -> Tuple[str, float]:
    """Return (category_id, score). score 0 => unknown ('general')."""
    t = _norm(text)
    if not t:
        return "general", 0.0

    best_id, best_score = "general", 0.0

    # 1) strong multi-word phrases win outright
    for phrase, cid in _STRONG_PHRASES:
        if phrase in t:
            return cid, 0.95

    # 2) weighted keyword hits; multi-word keywords score higher than single ones.
    #    A trailing "(?![a-z])" instead of "\b" matters: "galaxy s" must still
    #    match "galaxy s24", and "tab " must match "tab a9" — but "phone" must
    #    NOT match "phones"/"phonetics" unless the plural is listed too.
    words = set(re.findall(r"[a-z0-9']+", t))
    for cat in CATEGORIES:
        score = 0.0
        for kw in cat.keywords:
            kw = kw.strip()
            if not kw or kw in _CATEGORY_STOP:
                continue
            weight = 0.45 if kw.strip() in _CROSS_BRAND_KEYWORDS else (
                0.35 if kw.strip() in _WEAK_KEYWORDS else 1.0)
            if " " in kw:
                if re.search(r"\b" + re.escape(kw) + r"(?![a-z])", t):
                    score += 1.35 * weight
            elif kw in words or re.search(r"\b" + re.escape(kw) + r"(?![a-z])", t):
                score += weight
        if score > best_score:
            best_id, best_score = cat.id, score

    # 3) spec vote: authoritative for gadget titles that omit the category noun.
    #    Only consulted when keyword evidence is thin or contested.
    if best_score < 2.0:
        votes: Dict[str, float] = {}
        signatures: Dict[str, int] = {}
        for cat in CATEGORIES:
            v = _spec_vote(t, cat)
            if v > 0:
                votes[cat.id] = v
                signatures[cat.id] = _signature_vote(t, cat)
        # a category needs a clearly richer spec profile to steal the lead
        if votes:
            # Rank by category-defining rules first, generic rule count second.
            ranked = sorted(votes.items(), key=lambda kv: (-signatures.get(kv[0], 0), -kv[1]))
            top_id, top_vote = ranked[0]
            top_sig = signatures.get(top_id, 0)
            leaders = {cid for cid, v in votes.items()
                       if signatures.get(cid, 0) == top_sig and v == top_vote}
            runner = next((kv for kv in ranked if kv[0] not in leaders), None)
            runner_sig = signatures.get(runner[0], 0) if runner else 0
            runner_vote = runner[1] if runner else 0.0
            keyword_top = best_score or 0.0

            # Clear win: strictly more defining rules than the next candidate,
            # or a wide vote margin on top of at least one defining rule.
            better = top_sig >= 1 and (top_sig > runner_sig or (top_vote - runner_vote) >= 2)
            # Ambiguous tie (e.g. phones vs tablets on RAM/storage/5G): fall back
            # to whichever the keyword layer already preferred, if it is one of
            # the tied leaders — the two signals agreeing is the whole point.
            consistent = bool(keyword_top) and best_id in leaders
            if top_vote >= 2 and top_vote > max(keyword_top, 0.0) and (better or consistent):
                winner = best_id if consistent else top_id
                best_id = winner
                best_score = max(best_score, votes[winner] * 0.75)

    if best_score <= 0:
        return "general", 0.0

    # normalise: 1 hit = 0.5, 2 hits = 0.72, 3+ ≈ 0.85+
    conf = min(0.95, 0.5 + 0.22 * (best_score - 1) + 0.08 * (best_score - 1) ** 2)
    return best_id, round(conf, 3)


def detect_category(*texts: Any) -> str:
    """Best category across several pieces of text (query first, then title)."""
    best_id, best_conf = "general", 0.0
    for text in texts:
        cid, conf = detect_category_scored(text)
        if conf > best_conf:
            best_id, best_conf = cid, conf
    return best_id


def query_matches_category(text: Any, category_id: Optional[str]) -> bool:
    """Loose check used to decide whether a query needs disambiguating."""
    if not category_id or category_id in ("auto", "all", "general"):
        return True
    cid, conf = detect_category_scored(text)
    return cid == category_id and conf >= 0.5


# Markers that mean "this listing is an accessory FOR the thing you searched",
# not the thing itself. Without this, "Samsung Galaxy S24 Back Cover" happily
# "matched" a Galaxy S24 on the other store and produced a fake ₹64,000 saving.
ACCESSORY_MARKERS = (
    "back cover", "backcase", "back case", "case cover", "flip cover", "cover for",
    "tempered glass", "screen guard", "screen protector", "protector",
    "charger", "charging cable", "data cable", "usb cable", "adapter", "car charger",
    "earphone case", "buds case", "skin", "pouch", "sleeve", "holster",
    "mount", "stand for", "holder", "tripod", "selfie stick",
    "spare part", "replacement", "refill", "cartridge", "drum unit", "toner",
    "stylus", "remote control for", "keyboard cover", "dust plug", "lens protector",
    "grip", "ring light", "bracket", "clamp", "extension board",
    "combo of", "strap for", "watch strap", "band strap", "screen replacement",
)

_ACCESSORY_REGEXES = (
    re.compile(r"\b(?:for|compatible with)\s+(?:samsung|apple|iphone|redmi|realme|"
               r"oneplus|xiaomi|poco|oppo|vivo|nokia|motorola|hp|dell|lenovo|asus|acer|"
               r"sony|lg|boat|noise|lenovo|msi)\b", re.I),
    re.compile(r"\b\d{2,3}\s?(?:gb|tb)\s?(?:case|cover|protector)\b", re.I),
)


def is_accessory(title: Any) -> bool:
    """True for covers/cables/chargers/refills — i.e. NOT the product itself."""
    t = _norm(title)
    if not t:
        return False
    if any(marker in t for marker in ACCESSORY_MARKERS):
        return True
    return any(rx.search(t) for rx in _ACCESSORY_REGEXES)


def category_label(category_id: Optional[str]) -> str:
    cat = CATEGORY_BY_ID.get(str(category_id or "").strip().lower())
    return cat.label if cat else GENERAL_CATEGORY.label


def category_group(category_id: Optional[str]) -> str:
    cat = CATEGORY_BY_ID.get(str(category_id or "").strip().lower())
    return cat.group if cat else GENERAL_CATEGORY.group


def refine_query(query: str, category_id: Optional[str]) -> str:
    """
    Disambiguate a bare query for the store search box: pinning "Footwear" and
    searching "nike" should hit shoes, not Nike sunglasses. We only append the
    hint when the query does not already signal the category, and never for
    'auto'/'all', so behaviour is unchanged when the user does not pick one.
    """
    q = (query or "").strip()
    if not q or not category_id or category_id in ("auto", "all", "general"):
        return q
    cat = CATEGORY_BY_ID.get(category_id)
    if not cat or not cat.query_hint:
        return q
    if query_matches_category(q, category_id):
        return q
    hint = cat.query_hint
    if hint.lower() in q.lower():
        return q
    return f"{q} {hint}".strip()


# ---------------------------------------------------------------------------
# Spec extraction
# ---------------------------------------------------------------------------
# Rules that work for every product, regardless of category.
# ---------------------------------------------------------------------------
# Spec extraction
# ---------------------------------------------------------------------------
# Rules that work for EVERY product, no matter the category. These guarantee a
# non-empty spec column even for a vertical nobody taught the engine about.
GENERIC_SPEC_RULES: Tuple[Tuple[str, str, str], ...] = (
    ("pack", "Pack", r"\b(?:pack\s?of\s?(\d{1,3})|set\s?of\s?(\d{1,3})|(\d{1,4})\s?(?:pcs|pieces|piece|pc|tablets|capsules|sachets|units|sheets|count)\b|(\d{1,3})\s?x\s?\d{1,4}\s?(?:pcs|pieces|pack|units|combo))"),
    ("weight", "Weight", r"\b(\d+(?:\.\d+)?)\s?(kg|kgs|kilogram|kilograms|gm|gms|gram|grams|g|mg|lb|lbs)\b"),
    ("volume", "Volume", r"\b(\d+(?:\.\d+)?)\s?(ml|ltr|litre|liter|litres|liters|fl\s?oz|cl|l)\b"),
    ("power", "Power", r"\b(\d{2,5})\s?(?:w|watt|watts|kw|va|rms)\b"),
    ("dimensions", "Dimensions", r"\b(\d{1,4}(?:\.\d+)?)\s?[x×]\s?(\d{1,4}(?:\.\d+)?)(?:\s?[x×]\s?(\d{1,4}(?:\.\d+)?))?\s?(cm|mm|inch|in|ft)?\b"),
    ("age", "Age", r"\b(\d{1,2})\s?(?:\+)?\s?(?:years?|yrs?|months?)\b"),
    ("size", "Size", r"\b(\d{1,3}(?:\.\d+)?)\s?(?:inch|inches|\"|”|ft|feet)(?![a-z0-9])"),
    ("certification", "Certified", r"\b(bis|isi|fssai|ce|rohs|iso\s?\d{4,5}|fda|gmp|energy\s?star)\b"),
    ("material", "Material", r"\b(" + "|".join(re.escape(m) for m in MATERIAL_WORDS) + r")\b"),
    ("colour", "Colour", r"\b(" + "|".join(re.escape(c) for c in COLOUR_WORDS) + r")\b"),
    ("gender", "For", r"\b(men|mens|men's|women|womens|women's|boys|girls|kids|unisex|baby)\b"),
    ("model", "Model", r"\b([a-z]{1,5}-?\d{2,5}(?:[a-z]{0,3}\d{0,3})?)\b"),
)

_GENERIC_ORDER = ("model", "type", "appliance_type", "product_type", "garment",
                  "shoe_type", "bag_type", "watch_type", "sport_type", "toy_type",
                  "furniture_type", "camera_type", "console_type", "pack", "weight",
                  "volume", "power", "dimensions", "age", "material", "colour", "gender")

# Keys whose value is free text and may legitimately span adjacent matches
# ("Inverter" + "Split AC", "Marble" + "Gray", "Stainless" + "Steel").
_MERGEABLE_KEYS = {
    "appliance_type", "product_type", "garment", "shoe_type", "bag_type",
    "watch_type", "sport_type", "toy_type", "furniture_type", "camera_type",
    "console_type", "type", "colour", "material", "pattern", "fit", "ruling",
    "flavour", "skin_hair", "food_type", "diet", "level", "smart", "interface",
    "connectivity", "anc", "induction", "os", "edition", "publisher", "series",
    "award", "format", "language", "sensor", "video", "resolution", "panel",
    "energy", "vehicle_type", "paper_size", "laptop_compat", "wheels", "sleeve",
    "the", "cpu", "gpu",
}

# Unit words that must never be mistaken for a product model code.
_UNIT_WORDS = {
    "l", "ml", "cl", "kg", "g", "gm", "mg", "lb", "oz", "w", "kw", "v", "hz",
    "mah", "mp", "cm", "mm", "ft", "in", "ton", "gb", "tb", "tr", "bt", "hr",
    "hrs", "pc", "pcs", "x", "d", "p", "gen",
}

# Pretty-printing for abbreviations that title-case would butcher.
_ABBREV_FIX = {
    "Ac": "AC", "Led": "LED", "Oled": "OLED", "Qled": "QLED", "Uhd": "UHD",
    "Tv": "TV", "Ro": "RO", "Uv": "UV", "Uf": "UF", "Hd": "HD", "Fhd": "FHD",
    "Usb": "USB", "Hdmi": "HDMI", "Tws": "TWS", "Anc": "ANC", "Enc": "ENC",
    "Ips": "IPS", "Va": "VA", "Bis": "BIS", "Isi": "ISI", "Fssai": "FSSAI",
    "Ce": "CE", "Mg": "MG", "Iu": "IU", "Gsm": "GSM", "Rc": "RC", "Dslr": "DSLR",
    "Eva": "EVA", "Tpe": "TPE", "Nbr": "NBR", "Pvc": "PVC", "Abs": "ABS",
    "Mdf": "MDF", "Spf": "SPF", "Atm": "ATM", "Ipx": "IPX", "Bt": "BT",
    "Vga": "VGA", "Dvi": "DVI", "Sata": "SATA", "Nvme": "NVMe", "Pcie": "PCIe",
    "Tft": "TFT", "Lcd": "LCD", "Amoled": "AMOLED", "Nfc": "NFC", "Gps": "GPS",
    "Rgb": "RGB", "Wifi": "Wi-Fi", "Webos": "webOS", "Tizen": "Tizen",
    "Cmos": "CMOS", "Bsi": "BSI", "Ufs": "UFS", "Emc": "eMMC", "Emmc": "eMMC",
    "Ssd": "SSD", "Hdd": "HDD", "Pc": "PC", "Va": "VA", "Ips": "IPS",
    "Full": "Full", "Rtx": "RTX", "Gtx": "GTX", "Ryzen": "Ryzen",
}


def _fix_chip_names(text: str) -> str:
    text = re.sub(r"(?<=\d)([a-z])(?=\b)(?<![a-z]{2})", lambda m: m.group(1).upper(), text)
    """'Core I5 13th Gen' -> 'Core i5 13th Gen'; 'Rtx 4050' -> 'RTX 4050'."""
    text = re.sub(r"\bCore\s?I([3579])\b", r"Core i\1", text, flags=re.I)
    text = re.sub(r"\bApple\s?M([1-4])\b", r"Apple M\1", text, flags=re.I)
    text = re.sub(r"\bRtx\b", "RTX", text, flags=re.I)
    text = re.sub(r"\bGtx\b", "GTX", text, flags=re.I)
    return text


def _smart_title(text: str) -> str:
    words = []
    for w in re.split(r"\s+", text.strip()):
        w = w.capitalize()
        words.append(_ABBREV_FIX.get(w, w))
    return " ".join(words)


def _clean_value(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" ,.-")


def _pretty_number(value: str) -> str:
    try:
        return _fmt_num(float(value))
    except (TypeError, ValueError):
        return value


def _iter_matches(text: str, pattern: str) -> List[Tuple[int, int, str]]:
    try:
        return [(m.start(), m.end(), m.group(0)) for m in re.finditer(pattern, text, re.I)]
    except re.error:
        return []


def _merged_span(text: str, matches: List[Tuple[int, int, str]], mergeable: bool) -> Optional[Tuple[int, int, str]]:
    """
    Pick the best match for a rule. For free-text keys we merge *adjacent*
    matches, which is what turns "Inverter" + "Split AC" into "Inverter Split AC"
    and "Marble" + "Gray" into "Marble Gray" instead of reporting whichever
    alternative happened to appear first in the pattern.
    """
    if not matches:
        return None
    if not mergeable:
        return matches[0]

    best = matches[0]
    best_len = best[1] - best[0]
    for start, end, _txt in matches[1:]:
        if start - best[1] <= 2:           # directly adjacent (just a space/hyphen)
            merged = (best[0], end, text[best[0]:end])
            if (merged[1] - merged[0]) > best_len:
                best, best_len = merged, merged[1] - merged[0]
    return best


def _first_group(match_text: str, m: Optional["re.Match[str]"]) -> str:
    if m is not None:
        for g in m.groups():
            if g:
                return g.strip()
    return match_text.strip()


def _format_value(key: str, span_text: str, group: str) -> str:
    """Turn a raw match into the display value the dashboard shows."""
    lowered = span_text.lower()

    if key == "pack":
        return f"Pack of {_pretty_number(group)}"

    if key == "weight":
        # "Redmi Note 13 5G" is a network generation, not five grams
        if re.fullmatch(r"[2-5]g", span_text.strip().lower()):
            return ""
        unit = ""
        for cand in ("kgs", "kg", "kilogram", "kilograms", "gms", "gm", "grams",
                     "gram", "mg", "lbs", "lb", "g"):
            if re.search(r"\d\s?" + cand + r"\b", lowered):
                unit = cand
                break
        unit = {"kgs": "kg", "kilogram": "kg", "kilograms": "kg", "gms": "g",
                "gm": "g", "grams": "g", "gram": "g", "lbs": "lb"}.get(unit, unit)
        return f"{_pretty_number(group)} {unit}".strip()

    if key == "volume":
        unit = ""
        for cand in ("ml", "ltr", "litres", "liters", "litre", "liter", "cl", "fl oz", "l"):
            if re.search(r"\d\s?" + cand + r"\b", lowered):
                unit = {"ltr": "L", "litres": "L", "liters": "L", "litre": "L",
                        "liter": "L"}.get(cand, cand)
                break
        unit = "L" if unit == "l" else unit
        return f"{_pretty_number(group)} {unit}".strip()

    if key in {"power", "watt"}:
        unit = "kW" if re.search(r"kw\b", lowered) else ("VA" if re.search(r"va\b", lowered) else "W")
        return f"{_pretty_number(group)} {unit}"

    if key == "capacity":
        unit = ""
        if re.search(r"\b(ton|tons|tr)\b", lowered):
            unit = "Ton"
        elif re.search(r"\b(ml)\b", lowered):
            unit = "ml"
        elif re.search(r"\bl\b|\bltr\b|\blitre\b|\bliter\b", lowered):
            unit = "L"
        elif re.search(r"\bkg\b", lowered):
            unit = "kg"
        elif re.search(r"\bgb\b", lowered):
            unit = "GB"
        elif re.search(r"\btb\b", lowered):
            unit = "TB"
        return f"{_pretty_number(group)} {unit}".strip()

    if key == "storage":
        media = ""
        for cand in ("nvme", "ssd", "hdd", "emmc", "ufs", "pcie", "flash", "rom"):
            if re.search(r"\b" + cand + r"\b", lowered):
                media = cand.upper()
                break
        unit = "TB" if re.search(r"\d\s?tb\b", lowered) else "GB"
        base = f"{_pretty_number(group)} {unit}"
        return f"{base} {media}".strip()

    if key == "ram":
        return f"{_pretty_number(group)}GB"

    if key == "kg":
        return f"{_pretty_number(group)} kg"

    if key in {"camera", "megapixels"}:
        m = re.search(r"(\d{1,3}(?:\.\d+)?)", span_text)
        return f"{_pretty_number(m.group(1))} MP" if m else _clean_value(span_text)

    if key == "zoom":
        m = re.search(r"(\d{1,3})", span_text)
        return f"{_pretty_number(m.group(1))}x zoom" if m else _clean_value(span_text)

    if key == "gsm":
        return f"{_pretty_number(group)} GSM"

    if key == "sheets":
        return f"{_pretty_number(group)} Sheets"

    if key == "jars":
        return f"{_pretty_number(group)} Jars"

    if key == "pieces":
        return f"{_pretty_number(group)} Pieces"

    if key == "pages":
        return f"{_pretty_number(group)} Pages"

    if key == "age":
        return f"{_pretty_number(group)}+ Years"

    if key == "dosage":
        m = re.search(r"(\d+(?:\.\d+)?)\s?(mg|mcg|iu|g)\b", lowered)
        return f"{_pretty_number(m.group(1))} {m.group(2).upper()}" if m else _pretty_number(group)

    if key == "dimension":
        return _clean_value(span_text)

    if key == "laptop_compat":
        m = re.search(r"(\d+(?:\.\d+)?)", span_text)
        return f'Fits {_pretty_number(m.group(1))}"' if m else _clean_value(span_text)

    if key == "oil_grade":
        m = re.search(r"(\d{1,2}w[\s-]?\d{2})", lowered)
        return m.group(1).upper().replace(" ", "-") if m else _clean_value(span_text).upper()

    if key == "battery":
        if re.search(r"mah", lowered):
            return f"{_pretty_number(group)} mAh"
        if re.search(r"\d\s?(?:hours?|hrs?|h)\b", lowered):
            return f"{_pretty_number(group)} h playback"
        if re.search(r"\bdays?\b", lowered):
            return f"{_pretty_number(group)} days"
        return f"{_pretty_number(group)} mAh"

    if key == "refresh":
        return f"{_pretty_number(group)}Hz"

    if key == "screen" or key == "dial" or key == "size":
        if key == "screen" and re.search(r"inch|inches|\"|”|\bin\b", lowered):
            return f'{_pretty_number(group)}"'
        if key == "size":
            # apparel / footwear sizes: "size l" -> "L", "uk 9" -> "UK 9"
            letter = re.search(r"\b(xxs|xs|s|m|l|xl|xxl|xxxl|[3-5]xl)\b", lowered)
            if letter:
                return letter.group(1).upper()
            region = re.search(r"\b(uk|us|eu)\b", lowered)
            if region:
                return f"{region.group(1).upper()} {_pretty_number(group)}"
            metric = re.search(r"(\d+(?:\.\d+)?)\s?(mm|cm|m|inch|in|ft)\b", lowered)
            if metric:
                unit = metric.group(2)
                unit = {"inch": "inch", "in": '"', "ft": "ft"}.get(unit, unit)
                return f"{_pretty_number(metric.group(1))} {unit}".strip()
            if re.search(r'\d\s?(?:inch|in|\")', lowered):
                return f'{_pretty_number(group)}"'
        return f"{_pretty_number(group)} mm" if key == "dial" else _pretty_number(group)

    if key == "dimensions":
        nums = re.findall(r"\d+(?:\.\d+)?", span_text)
        unit = ""
        for u in ("cm", "mm", "inch", "in", "ft"):
            if re.search(r"\b" + u + r"\b", lowered):
                unit = u
                break
        return " × ".join(_pretty_number(n) for n in nums) + (f" {unit}" if unit else "")

    if key == "dimension" or key == "resolution":
        return span_text.strip().upper() if len(span_text.strip()) <= 6 else _smart_title(span_text)

    if key == "model":
        return group.upper()

    if key in {"cpu", "gpu", "type", "appliance_type", "product_type", "garment",
               "shoe_type", "bag_type", "watch_type", "sport_type", "toy_type",
               "furniture_type", "camera_type", "console_type", "colour",
               "material", "pattern", "fit", "ruling", "flavour", "skin_hair",
               "food_type", "diet", "level", "smart", "interface", "connectivity",
               "anc", "induction", "os", "edition", "publisher", "series", "award",
               "format", "language", "sensor", "video", "panel", "energy",
               "vehicle_type", "paper_size", "laptop_compat", "sleeve", "form"}:
        # multi-match values are already merged upstream; title-case the result
        return _fix_chip_names(_smart_title(span_text))

    if key in {"ram", "jars", "pieces", "pages", "wheels", "star", "kg", "spf",
               "dosage", "gsm", "sheets", "megapixels", "zoom", "megapixel", "pages"}:
        return _pretty_number(group)

    if key in {"strap", "wheels", "series", "publisher", "award", "certification",
               "energy", "ruling", "induction", "anc", "level", "smart", "edition"}:
        if key == "wheels":
            return f"{_pretty_number(group)} Wheels"
        return _fix_chip_names(_smart_title(span_text))

    if key in {"network", "resolution", "panel", "video", "sensor"}:
        return _fix_chip_names(_smart_title(span_text)).upper() if len(span_text) <= 8 else _smart_title(span_text)

    if key == "gender":
        return _smart_title(span_text)

    return _clean_value(group) or _clean_value(span_text)


_WARRANTY_AFTER = re.compile(
    r"^\s*(?:warrant\w*|onsite|on-site|guarantee|adp|insurance|extended)", re.I)
_WARRANTY_BEFORE = re.compile(r"(warrant\w*|guarantee|insurance)\s*$", re.I)


def _apply_rules(text: str, rules: Sequence[Tuple[str, str, str]],
                 out: Dict[str, str], labels: Dict[str, str],
                 blocked: Optional[set] = None) -> None:
    for key, label, pattern in rules:
        if out.get(key):
            continue
        if blocked and key in blocked:
            continue
        matches = _iter_matches(text, pattern)
        if not matches:
            continue
        span = _merged_span(text, matches, key in _MERGEABLE_KEYS)
        if span is None:
            continue
        start, end, span_text = span
        if key in {"screen", "display", "size"} and re.match(
                r"\s*(sensor|camera|cmos|pixel|mp)\b", text[end:end + 18], re.I):
            continue
        # "1 Year Warranty" is not a product age — that tag sends shoppers the
        # wrong way, so skip age matches that are really warranty wording.
        if key == "age":
            tail = text[end:end + 40]
            if (_WARRANTY_AFTER.search(text[end:end + 20])
                    or _WARRANTY_BEFORE.search(text[:start])
                    or re.search(r"warrant|onsite|on-site|guarantee|insurance|extended", tail, re.I)):
                continue
        group = ""
        for m in re.finditer(pattern, span_text, re.I):
            group = _first_group(span_text, m)
            break
        value = _format_value(key, span_text, group)
        if not value:
            continue
        value = str(value).strip()
        if not value or value.lower() in {"n/a", "-", "none"}:
            continue
        out[key] = value
        labels[key] = label


def extract_specs(title: str, category: Optional[str] = None) -> Dict[str, str]:
    """
    Category-aware spec extraction. Returns {key: display_value}; use
    `specs_to_text()` for the display string and `strip_labels()` before export.

    >>> extract_specs("Voltas 1.5 Ton 5 Star Inverter Split AC (Copper)")
    {'appliance_type': 'Inverter Split AC', 'capacity': '1.5 Ton', 'star': '5 Star', ...}
    """
    raw = str(title or "")
    text = _norm(raw)
    if not text:
        return {}

    cid = category if category and category in CATEGORY_BY_ID else detect_category(raw)
    cat = CATEGORY_BY_ID.get(cid)

    out: Dict[str, str] = {}
    labels: Dict[str, str] = {}

    # 1) category-specific fields are authoritative
    if cat is not None:
        _apply_rules(text, cat.spec_rules, out, labels)

    # 2) generic fields fill whatever is still missing (any category at all).
    #    Keys the category already expressed better are blocked — "500 Sheets"
    #    shouldn't also surface as "Pack of 500", and "454 g" shouldn't repeat.
    blocked = set()
    if out.get("sheets") or out.get("pages") or out.get("pieces"):
        blocked.add("pack")
    if out.get("weight") or out.get("volume"):
        blocked |= {"weight", "volume"} - ({"weight"} if "weight" not in out else set())
    _apply_rules(text, GENERIC_SPEC_RULES, out, labels, blocked)

    # 3) shape tidy-ups
    if out.get("star") and not out["star"].lower().endswith("star"):
        out["star"] = f"{out['star']} Star"
    if out.get("screen") and not out["screen"].endswith('"'):
        out["screen"] = f'{_pretty_number(out["screen"])}"'
    if out.get("display") and not out["display"].lower().endswith(("inch", '"', "”")):
        out["display"] = f'{_pretty_number(out["display"])}"'
    if out.get("model"):
        code = out["model"].lower().strip("-")
        if code in _UNIT_WORDS or len(code) < 3 or re.fullmatch(r"\d+", code):
            out.pop("model", None)
        elif re.fullmatch(r"[a-z]{1,3}\d{4,}", code) and code[:1] in {"z", "e", "q"} and len(code) > 8:
            out.pop("model", None)   # long gibberish SKU fragments
    if out.get("spf") and not out["spf"].lower().startswith("spf"):
        out["spf"] = f"SPF {out['spf']}"
    if out.get("star"):
        out["star"] = _clean_value(out["star"])

    # 4) collapse duplicates: identical text ("7 kg" twice) and cases where a
    #    short value is contained in a longer one ("Leather" vs "Leather Strap")
    keys = [k for k in out.keys() if not str(k).startswith("_")]
    keep: List[str] = []
    for k in keys:
        val = str(out[k]).strip().lower()
        if not val:
            continue
        if any(val == str(out[o]).strip().lower() for o in keep):
            out.pop(k); labels.pop(k, None); continue
        # same physical value under two names ("14.1\"" screen vs "14.1 inch" size)
        canon_k = _canonical_value(k, out[k])
        if canon_k is not None and any(
                _canonical_value(o, out[o]) == canon_k for o in keep):
            out.pop(k); labels.pop(k, None); continue
        if any(val in str(out[o]).strip().lower() for o in keep):
            out.pop(k); labels.pop(k, None); continue
        for o in list(keep):
            if str(out[o]).strip().lower() in val:
                out.pop(o); labels.pop(o, None); keep.remove(o)
        keep.append(k)

    out["_labels"] = labels  # consumed by specs_to_text; stripped before export
    return out


GENERIC_CATEGORY_KEYS = frozenset(k for k, _l, _p in GENERIC_SPEC_RULES)

_SPEC_LABEL_OVERRIDES = {
    "cpu": "CPU", "gpu": "GPU", "ram": "RAM", "storage": "Storage", "screen": "Screen",
    "refresh": "Refresh", "battery": "Battery", "camera": "Camera", "network": "Network",
    "capacity": "Capacity", "star": "Star", "kg": "Load", "volume": "Capacity",
    "weight": "Weight", "power": "Power", "material": "Material", "colour": "Colour",
    "pack": "Pack", "age": "Age", "model": "Model", "format": "Format", "language": "Language",
    "pages": "Pages", "size": "Size", "gender": "For", "type": "Type", "appliance_type": "Type",
    "product_type": "Type", "garment": "Type", "shoe_type": "Type", "bag_type": "Type",
}


def spec_key_order(category: Optional[str]) -> List[str]:
    """Preferred field order for a category, falling back to the generic order."""
    if category and category in CATEGORY_BY_ID:
        cat = CATEGORY_BY_ID[category]
        order = list(cat.spec_order)
        order += [k for k in _GENERIC_ORDER if k not in order]
        return order
    return list(_GENERIC_ORDER)


def spec_keys_sorted(specs: Dict[str, str], category: Optional[str] = None) -> List[str]:
    """Order the spec keys for display: category-relevant fields first."""
    keys = [k for k in specs.keys() if not str(k).startswith("_")]
    order = spec_key_order(category)
    ordered = [k for k in order if k in keys]
    ordered += [k for k in keys if k not in ordered]
    return ordered


def specs_to_text(specs: Dict[str, str], category: Optional[str] = None,
                  limit: int = 7) -> str:
    """Render a spec dict as the compact 'CPU · RAM · …' string the UI shows."""
    if not specs:
        return ""
    parts, seen = [], set()
    for k in spec_keys_sorted(specs, category):
        v = specs.get(k)
        if not v or str(k).startswith("_"):
            continue
        if str(v).lower() in seen:
            continue
        seen.add(str(v).lower())
        parts.append(str(v))
        if len(parts) >= limit:
            break
    return " · ".join(parts)


def spec_labels(specs: Dict[str, str], category: Optional[str] = None) -> List[Tuple[str, str]]:
    """[(label, value)] pairs — used by exports and the chatbot's detail view."""
    labels = specs.get("_labels", {}) if isinstance(specs, dict) else {}
    out: List[Tuple[str, str]] = []
    for k in spec_keys_sorted(specs, category):
        v = specs.get(k)
        if not v or str(k).startswith("_"):
            continue
        label = labels.get(k) or _SPEC_LABEL_OVERRIDES.get(k, k.replace("_", " ").title())
        out.append((label, str(v)))
    return out

# ---------------------------------------------------------------------------
# Matching support
# ---------------------------------------------------------------------------
_NUMERIC_KEYS = {"ram", "storage", "capacity", "volume", "weight", "screen",
                 "refresh", "battery", "camera", "page"}

_UNIT_FAMILY = {
    "kg": ("mass", 1000.0), "g": ("mass", 1.0), "mg": ("mass", 0.001), "lb": ("mass", 453.592),
    "l": ("volume", 1000.0), "ml": ("volume", 1.0),
    "ton": ("trade", 1.0),
}


def _canonical_value(key: str, value: Any) -> Optional[Any]:
    """
    Turn a display string into something comparable: "1.5 Ton" -> (1.5, "ton"),
    "16GB" -> 16.0, "UK 9" -> "9". Returns None when nothing usable is found.
    """
    if value is None:
        return None
    s = str(value).lower().strip()
    if not s:
        return None

    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if not m:
        return re.sub(r"\s+", " ", s)

    num = float(m.group(1))
    if key in {"screen", "display", "dial", "size"} and not re.search(r"\b(uk|us|eu)\b", s):
        return num
    unit_match = re.search(r"([a-z]+)", s)
    unit = unit_match.group(1) if unit_match else ""
    if unit in _UNIT_FAMILY:
        family, factor = _UNIT_FAMILY[unit]
        return (family, round(num * factor, 4))
    if key == "capacity" and unit in ("ton", "tons", "tr"):
        return ("trade", num)
    if key in _NUMERIC_KEYS or unit in {"gb", "tb", "hz", "mah", "mp", "w", "mm", "kg", "l", "ml"}:
        # GB/TB need normalising so 1TB == 1024GB comparisons stay sane
        if unit == "tb":
            return ("data", num * 1024.0)
        if unit == "gb":
            return ("data", num)
        return num
    return re.sub(r"\s+", " ", s)


def spec_similarity(a_specs: Dict[str, Any], b_specs: Dict[str, Any],
                    category: Optional[str] = None) -> float:
    """
    0..1 agreement on shared spec fields. Fields both sides expose and agree on
    push the score up; fields that *conflict* push it down. Categories differ
    from the matcher's perspective only in which fields exist at all.
    """
    if not a_specs or not b_specs:
        return 0.5  # unknown => neutral, don't reward or punish

    cat = CATEGORY_BY_ID.get(category or "")
    keys = set(a_specs) & set(b_specs)
    keys -= {"_labels"}
    if cat is not None and cat.variant_keys:
        keys |= {k for k in cat.variant_keys if k in a_specs and k in b_specs}

    if not keys:
        return 0.5

    agree = conflict = 0
    for k in keys:
        va = _canonical_value(k, a_specs.get(k))
        vb = _canonical_value(k, b_specs.get(k))
        if va is None or vb is None:
            continue
        if va == vb:
            agree += 1
        else:
            # numeric fields: near-misses count as conflicts for variant keys
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                lo, hi = sorted((float(va), float(vb)))
                if lo > 0 and hi / lo <= 1.02:
                    agree += 1
                    continue
            conflict += 1

    total = agree + conflict
    if total == 0:
        return 0.5
    return round(agree / total, 4)


_CONFLICT_KEYS = {
    "ram", "storage", "capacity", "volume", "weight", "size", "screen",
    "kg", "pages", "pack", "shade", "spf", "edition", "network", "format",
    "language", "resolution", "pieces", "jars", "wheels", "age",
}


def variant_conflict(a_specs: Dict[str, Any], b_specs: Dict[str, Any],
                     category: Optional[str] = None) -> Tuple[bool, List[str]]:
    """
    True when the two listings are clearly different SKUs of the same product
    (8GB vs 16GB RAM, UK 8 vs UK 10, 1 Pack vs Pack of 2, 190 L vs 320 L).
    Returns the conflicting keys so the UI/log can explain a rejected match.
    """
    if not a_specs or not b_specs:
        return False, []

    cat = CATEGORY_BY_ID.get(category or "")
    keys = set(_CONFLICT_KEYS)
    if cat is not None:
        keys |= set(cat.variant_keys)

    conflicts: List[str] = []
    for k in sorted(keys):
        if k not in a_specs or k not in b_specs:
            continue
        va = _canonical_value(k, a_specs.get(k))
        vb = _canonical_value(k, b_specs.get(k))
        if va is None or vb is None:
            continue
        if va == vb:
            continue
        # treat tiny float noise as equal
        if isinstance(va, float) and isinstance(vb, float) and abs(va - vb) <= 1e-6:
            continue
        conflicts.append(k)
    return (len(conflicts) > 0), conflicts


# ---------------------------------------------------------------------------
# UI / API helpers
# ---------------------------------------------------------------------------
def catalog(include_general: bool = False) -> List[Dict[str, Any]]:
    """Serialisable category list for the dashboard's picker."""
    items: List[Dict[str, Any]] = []
    if include_general:
        items.append({
            "id": "general", "label": GENERAL_CATEGORY.label, "group": "General",
            "emoji": GENERAL_CATEGORY.emoji, "example": "", "query_hint": "",
        })
    for c in CATEGORIES:
        items.append({
            "id": c.id, "label": c.label, "group": c.group,
            "emoji": c.emoji, "example": c.example, "query_hint": c.query_hint,
        })
    items.sort(key=lambda it: (GROUP_ORDER.index(it["group"]) if it["group"] in GROUP_ORDER
                               else len(GROUP_ORDER), it["label"]))
    return items


def strip_labels(specs: Dict[str, Any]) -> Dict[str, Any]:
    """Remove private keys so specs can be exported/serialised safely."""
    return {k: v for k, v in (specs or {}).items() if not str(k).startswith("_")}


if __name__ == "__main__":  # pragma: no cover - tiny manual smoke check
    samples = [
        "Voltas 1.5 Ton 5 Star Inverter Split AC (Copper, 2024 Model)",
        "Nike Revolution 7 Running Shoes For Men (Black, UK 9)",
        "Atomic Habits Paperback English by James Clear",
        "Sony WH-CH520 Wireless On-Ear Bluetooth Headphones, 50 Hours Battery",
        "Tata Sampann Unpolished Toor Dal 1 kg",
        "Philips Air Fryer 4.1 L 1400W Rapid Air Technology",
        "Samsung Galaxy S24 5G (Marble Gray, 8GB RAM, 256GB Storage)",
        "HP Pavilion 15 Intel Core i5 13th Gen 15.6\" FHD Laptop, 16GB RAM, 512GB SSD",
    ]
    for s in samples:
        cid, conf = detect_category_scored(s)
        specs = strip_labels(extract_specs(s, cid))
        print(f"{conf:>5} {cid:<22} {specs_to_text(specs, cid)}")
