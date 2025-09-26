#!/usr/bin/env python3
"""
app.py - Flask API for Start-B (drop-in ready) with atomic sequential bill numbers
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
import certifi   # ✅ added

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
MONGO_URI = os.getenv("MONGO_URI", "").strip() or None
DB_NAME = os.getenv("MONGO_DB_NAME", "start_billing")   # ✅ use your env var key
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
            tlsCAFile=certifi.where()   # ✅ secure SSL certs
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
    logger.info("ℹ️  No MONGO_URI provided — using in-memory fallback for demo")
    _use_memory = True
    _memory = make_in_memory_store()

# ----------------------------
# (rest of your code unchanged)
# ----------------------------
# ... keep all your routes and logic below ...
