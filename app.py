#!/usr/bin/env python3
"""
app.py - Flask API for Start-B (drop-in ready)

Features:
 - CORS fixed for preflight (Content-Type allowed)
 - /api/settings/business, /api/settings/upi endpoints
 - /api/customers (GET/POST/GET by id/DELETE)
 - /api/bills (GET/POST)
 - MongoDB if MONGO_URI provided, otherwise an in-memory fallback
 - Helpful JSON responses matching frontend expectations
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

# Load local .env if present (useful for local dev)
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("start-b-api")

# ----------------------------
# App & CORS
# ----------------------------
app = Flask(__name__)

ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://star-frontend-chi.vercel.app"
]

# Configure flask-cors for /api/* routes
CORS(
    app,
    resources={r"/api/*": {"origins": ALLOWED_ORIGINS}},
    supports_credentials=True,
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With", "Accept"],
    expose_headers=["Content-Type", "Authorization"]
)

# Ensure preflight OPTIONS responses have the headers the browser expects.
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
        return resp  # short-circuit for preflight


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin")
    if origin and origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
    # Always include these so preflights pass (Content-Type allowed)
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With, Accept"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
    response.headers["Access-Control-Allow-Credentials"] = "true"
    return response

# ----------------------------
# Database: try MongoDB, fallback to in-memory
# ----------------------------
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
        # Try a simple connect/ping
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        # ping to verify connection
        client.admin.command("ping")
        # Get DB: either default or DB_NAME
        try:
            db = client.get_database()
        except Exception:
            db = client[DB_NAME]
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
    """Return a JSON-serializable copy of a db doc (ObjectId -> str, datetimes -> iso)."""
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
    # ensure _id is string
    if "_id" in out:
        out["_id"] = str(out["_id"])
    return out

def log_and_500(e):
    logger.error("Exception: %s", e)
    logger.error(traceback.format_exc())
    return jsonify({"error": "Internal server error", "details": str(e)}), 500

# ----------------------------
# Routes - Health / Settings
# ----------------------------
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "message": "Backend running ✅"})

@app.route("/api/settings/business", methods=["GET"])
def get_business_settings():
    try:
        if not _use_memory:
            doc = settings_collection.find_one({"key": "business"})
            if doc:
                return jsonify({"business": serialize_doc(doc.get("value", {}))})
            # fallback static if not found
        # memory fallback
        return jsonify({"business": _memory["settings"]["business"]})
    except Exception as e:
        return log_and_500(e)

@app.route("/api/settings/upi", methods=["GET"])
def get_upi_settings():
    try:
        if not _use_memory:
            doc = settings_collection.find_one({"key": "upi"})
            if doc:
                return jsonify({"upi": serialize_doc(doc.get("value", {}))})
        return jsonify({"upi": _memory["settings"]["upi"]})
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
            logger.info("Created customer (memory) id=%s", new_id)
            return jsonify({"message": "Customer created successfully", "customer": customer}), 201
        else:
            payload = {
                "name": name,
                "phone": phone,
                "email": data.get("email", ""),
                "address": data.get("address", ""),
                "notes": data.get("notes", ""),
                "created_at": now,
                "updated_at": now,
            }
            result = customers_collection.insert_one(payload)
            inserted_id = result.inserted_id
            # return customer in shape frontend expects
            customer = {
                "_id": str(inserted_id),
                "name": name,
                "phone": phone,
                "email": payload["email"],
                "address": payload["address"],
                "notes": payload["notes"],
                "created_at": iso(now),
                "updated_at": iso(now),
                "total_orders": 0,
                "total_spent": 0,
                "outstanding_balance": 0,
                "bills": []
            }
            logger.info("Created customer id=%s", str(inserted_id))
            return jsonify({"message": "Customer created successfully", "customer": customer}), 201
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

@app.route("/api/customers/<customer_id>", methods=["GET"])
def get_customer(customer_id):
    try:
        if _use_memory:
            c = _memory["customers"].get(customer_id)
            if not c:
                return jsonify({"error": "Customer not found"}), 404
            return jsonify({"customer": c})
        else:
            try:
                doc = customers_collection.find_one({"_id": ObjectId(customer_id)})
            except Exception:
                return jsonify({"error": "Invalid customer ID"}), 400
            if not doc:
                return jsonify({"error": "Customer not found"}), 404
            return jsonify({"customer": serialize_doc(doc)})
    except Exception as e:
        return log_and_500(e)

@app.route("/api/customers/<customer_id>", methods=["DELETE"])
def delete_customer(customer_id):
    try:
        deleted_bills = 0
        deleted_jobs = 0  # placeholder if you have jobs collection
        if _use_memory:
            if customer_id in _memory["customers"]:
                # delete bills referencing this customer
                bills_to_delete = [bid for bid, b in _memory["bills"].items() if b.get("customer_id") == customer_id]
                for bid in bills_to_delete:
                    _memory["bills"].pop(bid, None)
                deleted_bills = len(bills_to_delete)
                _memory["customers"].pop(customer_id, None)
                return jsonify({"message": "Customer deleted", "deleted_bills": deleted_bills, "deleted_jobs": deleted_jobs})
            else:
                return jsonify({"error": "Customer not found"}), 404
        else:
            # delete bills first
            res = bills_collection.delete_many({"customer_id": customer_id})
            deleted_bills = res.deleted_count
            # delete customer
            res2 = customers_collection.delete_one({"_id": ObjectId(customer_id)})
            if res2.deleted_count == 0:
                return jsonify({"error": "Customer not found"}), 404
            return jsonify({"message": "Customer deleted", "deleted_bills": deleted_bills, "deleted_jobs": deleted_jobs})
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

        # verify customer exists
        if _use_memory:
            if customer_id not in _memory["customers"]:
                return jsonify({"error": "Customer not found"}), 404
            new_id = gen_id()
            now = iso(datetime.utcnow())
            bill = {
                "_id": new_id,
                "customer_id": customer_id,
                "items": items,
                "subtotal": total,
                "total": total,
                "created_at": now,
                "status": "unpaid"
            }
            _memory["bills"][new_id] = bill
            # attach to customer
            cust = _memory["customers"][customer_id]
            cust.setdefault("bills", []).append(bill)
            cust["total_orders"] = (cust.get("total_orders") or 0) + 1
            cust["total_spent"] = (cust.get("total_spent") or 0) + total
            logger.info("Created bill (memory) id=%s for customer=%s", new_id, customer_id)
            return jsonify({"message": "Bill created", "bill": bill}), 201
        else:
            try:
                cust_doc = customers_collection.find_one({"_id": ObjectId(customer_id)})
            except Exception:
                return jsonify({"error": "Invalid customer ID"}), 400
            if not cust_doc:
                return jsonify({"error": "Customer not found"}), 404
            now = datetime.utcnow()
            bill_doc = {
                "customer_id": customer_id,
                "items": items,
                "subtotal": total,
                "total": total,
                "created_at": now,
                "status": "unpaid"
            }
            res = bills_collection.insert_one(bill_doc)
            bill_doc["_id"] = str(res.inserted_id)
            # (optional) update customer counters
            customers_collection.update_one({"_id": ObjectId(customer_id)}, {"$inc": {"total_orders": 1, "total_spent": total}})
            logger.info("Created bill id=%s for customer=%s", bill_doc["_id"], customer_id)
            # convert datetimes
            bill_doc["created_at"] = iso(now)
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
# Background task (example)
# ----------------------------
def background_task():
    # placeholder for periodic jobs (cleanup, stats)
    while True:
        # sleep to avoid busy loop; no heavy work here in demo
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
