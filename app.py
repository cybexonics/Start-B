#!/usr/bin/env python3
"""
app.py - Flask API for Start-B (drop-in ready) with atomic sequential bill numbers
"""

import os
import threading
import uuid
import logging
import traceback
from datetime import datetime

from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv
import certifi   # 👈 ensures Atlas TLS works

# ----------------------------
# Setup
# ----------------------------
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("start-b-api")

app = Flask(__name__)

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
MONGO_URI = os.getenv("MONGO_URI")
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
            serverSelectionTimeoutMS=5000,
            tls=True,
            tlsCAFile=certifi.where(),   # 👈 use proper CA file
        )
        client.admin.command("ping")
        db = client.get_database(DB_NAME)
        customers_collection = db["customers"]
        bills_collection = db["bills"]
        settings_collection = db["settings"]
        logger.info("✅ MongoDB connected (Atlas)")
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
# MongoDB atomic sequential bill number
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
