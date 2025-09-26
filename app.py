#!/usr/bin/env python3
"""
app.py - Flask API for Start-B with MongoDB Atlas connection
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
import certifi

# Load environment variables
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("start-b-api")

# Flask app setup
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

# ----------------------------
# Database connection
# ----------------------------
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME", "start_billing")

client = None
db = None
_use_memory = False

try:
    if MONGO_URI:
        client = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=5000,
            tlsCAFile=certifi.where(),   # ensures valid SSL cert
        )
        client.admin.command("ping")
        db = client[DB_NAME]
        logger.info("✅ MongoDB connected successfully")
    else:
        raise ValueError("No MONGO_URI provided")
except Exception as e:
    logger.error("❌ MongoDB connection failed: %s", e)
    logger.error(traceback.format_exc())
    _use_memory = True

# ----------------------------
# Healthcheck route
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
    logger.info("Starting app (port %s)", os.getenv("PORT", 5000))
    t = threading.Thread(target=background_task, daemon=True)
    t.start()
    debug_mode = os.getenv("DEBUG", "False").lower() in ("1", "true", "yes")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=debug_mode)
