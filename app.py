from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient, errors
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

# Enable CORS
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
try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.server_info()  # Force connection test
    db = client["start-billing"]
    customers_collection = db["customers"]
    bills_collection = db["bills"]
except errors.ServerSelectionTimeoutError:
    print("❌ Could not connect to MongoDB. Check MONGO_URI.")
    client = None
    db = None
    customers_collection = None
    bills_collection = None


# ----------------------------
# Utility: JSON Encoder for ObjectId + datetime
# ----------------------------
def serialize_doc(doc):
    if not doc:
        return None
    new_doc = {}
    for key, value in doc.items():
        if isinstance(value, ObjectId):
            new_doc[key] = str(value)
        elif isinstance(value, datetime):
            new_doc[key] = value.isoformat()
        else:
            new_doc[key] = value
    return new_doc


# ----------------------------
# API ROUTES
# ----------------------------

@app.route("/api/settings/business", methods=["GET"])
def get_business_settings():
    return jsonify({
        "business_name": "My Shop",
        "address": "123 Street",
        "phone": "9876543210"
    })


@app.route("/api/settings/upi", methods=["GET"])
def get_upi_settings():
    return jsonify({
        "upi_id": "myshop@upi",
        "qr_code_url": "https://example.com/qr.png"
    })


# -------- Customers --------
@app.route("/api/customers", methods=["POST"])
def create_customer():
    if not customers_collection:
        return jsonify({"error": "Database not connected"}), 500

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

    customer = customers_collection.find_one({"_id": result.inserted_id})
    return jsonify({"message": "Customer created successfully", "customer": serialize_doc(customer)}), 201


@app.route("/api/customers", methods=["GET"])
def list_customers():
    if not customers_collection:
        return jsonify({"error": "Database not connected"}), 500

    try:
        customers = [serialize_doc(c) for c in customers_collection.find()]
        return jsonify(customers)
    except Exception as e:
        return jsonify({"error": f"Failed to fetch customers: {str(e)}"}), 500


@app.route("/api/customers/<customer_id>", methods=["GET"])
def get_customer(customer_id):
    if not customers_collection:
        return jsonify({"error": "Database not connected"}), 500

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
    if not bills_collection or not customers_collection:
        return jsonify({"error": "Database not connected"}), 500

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

    return jsonify({"message": "Bill created successfully", "bill": serialize_doc(bill)}), 201


@app.route("/api/bills", methods=["GET"])
def list_bills():
    if not bills_collection:
        return jsonify({"error": "Database not connected"}), 500

    try:
        bills = [serialize_doc(b) for b in bills_collection.find()]
        return jsonify(bills)
    except Exception as e:
        return jsonify({"error": f"Failed to fetch bills: {str(e)}"}), 500


# ----------------------------
# Background tasks example
# ----------------------------
def background_task():
    while True:
        pass  # Placeholder for cron jobs


# ----------------------------
# Main Entrypoint
# ----------------------------
if __name__ == "__main__":
    threading.Thread(target=background_task, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
