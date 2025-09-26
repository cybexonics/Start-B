#!/usr/bin/env python3
"""
app.py - Flask API for Start-B with MongoDB + sequential bills + fixed settings routes
"""

from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime
from dotenv import load_dotenv
import os
import threading
import uuid
import logging
import traceback
import certifi   # for SSL CA file

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("start-b-api")

app = Flask(__name__)

# ----------------------------
# CORS Config
# ----------------------------
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://star-frontend-chi.vercel.app"
]

CORS(
    app,
    resources={r"/api/*": {"origins": ALLOWED_ORIGINS}},
    supports_credentials=True,
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With", "Accept"],
    expose_headers=["Content-Type", "Authorization"]
)

@app.before_request
def _handle_options_preflight():
    if request.method == "OPTIONS":
        resp = make_response("", 200)
        origin = request.headers.get("Origin")
        if origin and origin in ALLOWED_ORIGINS:
            resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With, Accept"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        return resp

@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin")
    if origin and origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With, Accept"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
    response.headers["Access-Control-Allow-Credentials"] = "true"
    return response

# ----------------------------
# Database
# ----------------------------
MONGO_URI = os.getenv("MONGO_URI", "").strip() or None
DB_NAME = os.getenv("MONGO_DB_NAME", "start_billing")
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
            serverSelectionTimeoutMS=5000,
            tls=True,
            tlsCAFile=certifi.where()
        )
        client.admin.command("ping")
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
    logger.info("ℹ️  No MONGO_URI provided — using in-memory fallback")
    _use_memory = True
    _memory = make_in_memory_store()

# ----------------------------
# Utilities
# ----------------------------
def gen_id():
    return uuid.uuid4().hex

def iso(dt):
    if isinstance(dt, datetime):
        return dt.isoformat()
    return dt

def serialize_doc(doc: dict):
    if not doc:
        return doc
    out = {}
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            out[k] = str(v)
        elif isinstance(v, datetime):
            out[k] = iso(v)
        else:
            out[k] = v
    if "_id" in out:
        out["_id"] = str(out["_id"])
    return out

def log_and_500(e):
    logger.error("Exception: %s", e)
    logger.error(traceback.format_exc())
    return jsonify({"error": "Internal server error", "details": str(e)}), 500

# ----------------------------
# Atomic sequential bill number
# ----------------------------
def get_next_bill_number():
    counter = db["counters"]
    result = counter.find_one_and_update(
        {"_id": "bill_number"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True
    )
    return result["seq"]

# ----------------------------
# Routes - Health / Settings
# ----------------------------
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "message": "Backend running ✅"})

@app.route("/api/settings/business", methods=["GET"])
def get_business_settings():
    try:
        if _use_memory:
            return jsonify(_memory["settings"]["business"])
        else:
            doc = settings_collection.find_one({"_id": "business"}) or {}
            return jsonify(doc)
    except Exception as e:
        return log_and_500(e)

@app.route("/api/settings/upi", methods=["GET"])
def get_upi_settings():
    try:
        if _use_memory:
            return jsonify(_memory["settings"]["upi"])
        else:
            doc = settings_collection.find_one({"_id": "upi"}) or {}
            return jsonify(doc)
    except Exception as e:
        return log_and_500(e)

# ----------------------------
# Customers
# ----------------------------
@app.route("/api/customers", methods=["POST"])
def create_customer():
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"error": "Request body must be JSON"}), 400
        name = data.get("name")
        phone = data.get("phone")
        if not name or not phone:
            return jsonify({"error": "Name and phone are required"}), 400

        now = datetime.utcnow()
        if _use_memory:
            new_id = gen_id()
            customer = {
                "_id": new_id,
                "name": name,
                "phone": phone,
                "email": data.get("email", ""),
                "address": data.get("address", ""),
                "notes": data.get("notes", ""),
                "created_at": iso(now),
                "updated_at": iso(now),
                "total_orders": 0,
                "total_spent": 0,
                "outstanding_balance": 0,
                "bills": []
            }
            _memory["customers"][new_id] = customer
            return jsonify({"message": "Customer created", "customer": customer}), 201
        else:
            payload = {
                "name": name,
                "phone": phone,
                "email": data.get("email", ""),
                "address": data.get("address", ""),
                "notes": data.get("notes", ""),
                "created_at": now,
                "updated_at": now,
                "total_orders": 0,
                "total_spent": 0,
                "outstanding_balance": 0,
                "bills": []
            }
            result = customers_collection.insert_one(payload)
            payload["_id"] = str(result.inserted_id)
            payload["created_at"] = iso(now)
            payload["updated_at"] = iso(now)
            return jsonify({"message": "Customer created", "customer": payload}), 201
    except Exception as e:
        return log_and_500(e)

@app.route("/api/customers", methods=["GET"])
def list_customers():
    try:
        search = request.args.get("search", "").strip()
        customers_list = []
        if _use_memory:
            for c in _memory["customers"].values():
                if not search or search.lower() in c.get("name", "").lower() or search in c.get("phone", ""):
                    customers_list.append(c)
        else:
            query = {}
            if search:
                query = {"$or": [{"name": {"$regex": search, "$options": "i"}}, {"phone": {"$regex": search}}]}
            cursor = customers_collection.find(query).sort("created_at", -1)
            for doc in cursor:
                customers_list.append(serialize_doc(doc))
        return jsonify({"customers": customers_list})
    except Exception as e:
        return log_and_500(e)

# ----------------------------
# Bills
# ----------------------------
@app.route("/api/bills", methods=["POST"])
def create_bill():
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"error": "Request body must be JSON"}), 400
        customer_id = data.get("customer_id")
        items = data.get("items", [])
        total = data.get("total", 0)
        if not customer_id or not isinstance(items, list):
            return jsonify({"error": "customer_id and items[] required"}), 400

        now = datetime.utcnow()

        if _use_memory:
            last_number = max([b.get("bill_number", 0) for b in _memory["bills"].values()], default=0)
            next_number = last_number + 1
            if customer_id not in _memory["customers"]:
                return jsonify({"error": "Customer not found"}), 404
            new_id = gen_id()
            bill = {
                "_id": new_id,
                "bill_number": next_number,
                "bill_no_str": str(next_number).zfill(5),  # <-- padded string
                "customer_id": customer_id,
                "items": items,
                "subtotal": total,
                "total": total,
                "created_at": iso(now),
                "status": "unpaid"
            }
            _memory["bills"][new_id] = bill
            _memory["customers"][customer_id].setdefault("bills", []).append(bill)
            return jsonify({"message": "Bill created", "bill": bill}), 201
        else:
            cust_doc = customers_collection.find_one({"_id": ObjectId(customer_id)})
            if not cust_doc:
                return jsonify({"error": "Customer not found"}), 404
            next_number = get_next_bill_number()
            bill_doc = {
                "bill_number": next_number,
                "bill_no_str": str(next_number).zfill(5),  # <-- padded string
                "customer_id": customer_id,
                "items": items,
                "subtotal": total,
                "total": total,
                "created_at": now,
                "status": "unpaid"
            }
            res = bills_collection.insert_one(bill_doc)
            bill_doc["_id"] = str(res.inserted_id)
            bill_doc["created_at"] = iso(now)
            customers_collection.update_one(
                {"_id": ObjectId(customer_id)},
                {"$inc": {"total_orders": 1, "total_spent": total}}
            )
            return jsonify({"message": "Bill created", "bill": bill_doc}), 201
    except Exception as e:
        return log_and_500(e)

@app.route("/api/bills", methods=["GET"])
def list_bills():
    try:
        bills_list = []
        if _use_memory:
            bills_list = list(_memory["bills"].values())
        else:
            cursor = bills_collection.find().sort("created_at", -1)
            for doc in cursor:
                bills_list.append(serialize_doc(doc))
        return jsonify({"bills": bills_list})
    except Exception as e:
        return log_and_500(e)

# ----------------------------
# Admin Dashboard Placeholder APIs
# ----------------------------
@app.route("/api/tailors", methods=["GET"])
def list_tailors():
    try:
        return jsonify({"tailors": []})
    except Exception as e:
        return log_and_500(e)

@app.route("/api/jobs", methods=["GET"])
def list_jobs():
    try:
        return jsonify({"jobs": []})
    except Exception as e:
        return log_and_500(e)

@app.route("/api/dashboard/stats", methods=["GET"])
def dashboard_stats():
    try:
        stats = {
            "total_customers": len(_memory["customers"]) if _use_memory else customers_collection.count_documents({}),
            "total_bills": len(_memory["bills"]) if _use_memory else bills_collection.count_documents({}),
            "total_revenue": (
                sum([b.get("total", 0) for b in _memory["bills"].values()])
                if _use_memory
                else sum([b.get("total", 0) for b in bills_collection.find()])
            ),
        }
        return jsonify(stats)
    except Exception as e:
        return log_and_500(e)

# ----------------------------
# Background task
# ----------------------------
def background_task():
    while True:
        try:
            threading.Event().wait(30)
        except Exception:
            break

# ----------------------------
# Run
# ----------------------------
if __name__ == "__main__":
    logger.info("Starting app (port %s) - memory fallback=%s", os.getenv("PORT", 5000), _use_memory)
    try:
        t = threading.Thread(target=background_task, daemon=True)
        t.start()
    except Exception:
        logger.exception("Failed to start background thread")

    debug_mode = os.getenv("DEBUG", "False").lower() in ("1", "true", "yes")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=debug_mode)
