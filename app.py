# ----------------------------
# Database
# ----------------------------
import certifi  # 👈 add this at the top with other imports

MONGO_URI = os.getenv("MONGO_URI", "").strip() or None
DB_NAME = os.getenv("DB_NAME", "start_billing")
_use_memory = False

client = None
db = None
customers_collection = None
bills_collection = None
settings_collection = None

def make_in_memory_store():
    return {
        "customers": {},
        "bills": {},
        "settings": {
            "business": {
                "business_name": "My Shop (demo)",
                "address": "Demo Street 1",
                "phone": "0000000000",
            },
            "upi": {
                "upi_id": "demo@upi",
                "qr_code_url": ""
            }
        }
    }

_memory = None

if MONGO_URI:
    try:
        client = MongoClient(
            MONGO_URI,
            tls=True,                        # ✅ keep TLS on
            tlsCAFile=certifi.where(),       # ✅ force Atlas to use trusted certs
            serverSelectionTimeoutMS=5000
        )
        client.admin.command("ping")         # check connection
        db = client.get_database(DB_NAME)
        customers_collection = db["customers"]
        bills_collection = db["bills"]
        settings_collection = db["settings"]
        logger.info("✅ MongoDB connected (MONGO_URI provided)")
    except Exception as e:
        logger.error("❌ MongoDB connection failed: %s", e)
        logger.error(traceback.format_exc())
        _use_memory = True
        _memory = make_in_memory_store()
else:
    logger.info("ℹ️  No MONGO_URI provided — using in-memory fallback for demo")
    _use_memory = True
    _memory = make_in_memory_store()
