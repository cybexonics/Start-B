from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime
import os
import threading

# ----------------------------
# App & Config
# ----------------------------
app = Flask(__name__)

# Allowed origins for CORS
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://star-frontend-chi.vercel.app"
]

# Enable CORS globally
CORS(
    app,
    origins=ALLOWED_ORIGINS,
    supports_credentials=True,
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With", "Accept"],
    expose_headers=["Content-Type", "Authorization"]
)

@app.after_request
def after_request(response):
    origin = request.headers.get("Origin")
    if origin and origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
    return response


# ----------------------------
# MongoDB Setup
# ----------------------------
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
client = MongoClient(MONGO_URI)
db = client["start-billing"]

customers_collection = db["customers"]
bills_collection = db["bills"]


# ----------------------------
# Utility: JSON Encoder for ObjectId
# ----------------------------
def serialize_doc(doc):
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


# ----------------------------
# API ROUTES
# ----------------------------

@app.route("/api/settings/business", methods=["GET"])
def get_business_settings():
    """Static business settings (you can later fetch from DB)."""
    return jsonify({
        "business_name": "My Shop",
        "address": "123 Street",
        "phone": "9876543210"
    })


@app.route("/api/settings/upi", methods=["GET"])
def get_upi_settings():
    """Static UPI settings (you can later fetch from DB)."""
    return jsonify({
        "upi_id": "myshop@upi",
        "qr_code_url": "https://example.com/qr.png"
    })


# -------- Customers --------

@app.route("/api/customers", methods=["POST"])
def create_customer():
    data = request.json
    if not data or not data.get("name") or not data.get("phone"):
        return jsonify({"error": "Name and phone are required"}), 400

    result = customers_collection.insert_one({
        "name": data.get("name"),
        "phone": data.get("phone"),
        "email": data.get("email", ""),
        "address": data.get("address", ""),
        "notes": data.get("notes", ""),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    })

    customer_id = str(result.inserted_id)
    return jsonify({
        "message": "Customer created successfully",
        "customer": {
            "_id": customer_id,
            "name": data.get("name"),
            "phone": data.get("phone"),
            "email": data.get("email", ""),
            "address": data.get("address", ""),
            "notes": data.get("notes", ""),
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
    }), 201


@app.route("/api/customers", methods=["GET"])
def list_customers():
    customers = [serialize_doc(c) for c in customers_collection.find()]
    return jsonify(customers)


@app.route("/api/customers/<customer_id>", methods=["GET"])
def get_customer(customer_id):
    try:
        customer = customers_collection.find_one({"_id": ObjectId(customer_id)})
        if not customer:
            return jsonify({"error": "Customer not found"}), 404
        return jsonify(serialize_doc(customer))
    except Exception:
        return jsonify({"error": "Invalid customer ID"}), 400


# -------- Bills --------

@app.route("/api/bills", methods=["POST"])
def create_bill():
    data = request.json
    if not data or not data.get("customer_id") or not data.get("items"):
        return jsonify({"error": "Customer ID and items are required"}), 400

    try:
        customer = customers_collection.find_one({"_id": ObjectId(data["customer_id"])})
        if not customer:
            return jsonify({"error": "Customer not found"}), 404
    except Exception:
        return jsonify({"error": "Invalid customer ID"}), 400

    bill = {
        "customer_id": data["customer_id"],
        "items": data.get("items", []),
        "total": data.get("total", 0),
        "created_at": datetime.utcnow()
    }

    result = bills_collection.insert_one(bill)
    bill["_id"] = str(result.inserted_id)

    return jsonify({"message": "Bill created successfully", "bill": bill}), 201


@app.route("/api/bills", methods=["GET"])
def list_bills():
    bills = [serialize_doc(b) for b in bills_collection.find()]
    return jsonify(bills)


# ----------------------------
# Background tasks example
# ----------------------------
def background_task():
    while True:
        # Example: cleanup or scheduled jobs
        pass


# ----------------------------
# Main Entrypoint
# ----------------------------
if __name__ == "__main__":
    threading.Thread(target=background_task, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
