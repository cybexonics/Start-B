from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient, ReturnDocument
from bson import ObjectId
from datetime import datetime, timedelta
import jwt
import bcrypt
from functools import wraps
import os
from dotenv import load_dotenv
import ssl
import time
from cachetools import TTLCache
import threading
from concurrent.futures import ThreadPoolExecutor

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
# ---- add this (Flask) ----
@app.route("/")
def home():
    return {"status": "ok", "message": "Backend is running ✅"}
# --------------------------
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-here')

# Read frontend URL for CORS from env (default to localhost)
FRONTEND_URL = os.getenv('FRONTEND_URL', 'https://star-tailor.vercel.app')

# Allow both development and production URLs for CORS
ALLOWED_ORIGINS = [
    'http://localhost:3000',          # Local development
    'http://127.0.0.1:3000',          # Alternative localhost
    'https://star-tailor-website.vercel.app',
    'https://star-tailor.vercel.app'  # Production
]

# Add custom frontend URL if different from defaults
if FRONTEND_URL not in ALLOWED_ORIGINS:
    ALLOWED_ORIGINS.append(FRONTEND_URL)

print(f"🔧 FRONTEND_URL from env: {FRONTEND_URL}")
print(f"🔧 ALLOWED_ORIGINS: {ALLOWED_ORIGINS}")

# Configure CORS with explicit settings
CORS(app, 
     origins='https://star-tailor.vercel.app',
     supports_credentials=True,
     methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'PATCH'],
     allow_headers=['Content-Type', 'Authorization', 'X-Requested-With', 'Accept'],
     expose_headers=['Content-Type', 'Authorization'])

# Simple caching for frequently accessed data
cache = TTLCache(maxsize=100, ttl=300)  # 5 minute TTL

# Global OPTIONS request handler and performance timing - MUST come before any route processing
@app.before_request
def handle_preflight_requests():
    # Set timing for performance monitoring
    request.start_time = time.time()
    
    # Handle CORS preflight requests
    if request.method == 'OPTIONS':
        origin = request.headers.get('Origin')
        response = jsonify({'message': 'CORS preflight'})
        
        if origin in ALLOWED_ORIGINS:
            response.headers['Access-Control-Allow-Origin'] = origin
        else:
            response.headers['Access-Control-Allow-Origin'] = ALLOWED_ORIGINS[0]
            
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS, PATCH'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With, Accept'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Max-Age'] = '3600'
        
        print(f"🔧 OPTIONS request from origin: {origin}")
        return response, 200

# MongoDB connection with SSL fix
MONGO_URI = os.getenv('MONGO_URI', 'mongodb+srv://star_tailor:fljC9lR6aUPZffka@cluster0.sfkrwck.mongodb.net')

# Initialize collections as None initially
users_collection = None
customers_collection = None
bills_collection = None
tailors_collection = None
settings_collection = None
jobs_collection = None
counters_collection = None

# Connect to MongoDB with SSL options
try:
    client = MongoClient(
        MONGO_URI,
        tls=True,
        tlsAllowInvalidCertificates=True,
        retryWrites=True,
        w='majority',
        connectTimeoutMS=5000,  # 5 second connection timeout
        socketTimeoutMS=10000,  # 10 second socket timeout
        serverSelectionTimeoutMS=5000,  # 5 second server selection timeout
        maxPoolSize=50,  # Increased connection pool size
        minPoolSize=10
    )
    
    # Test the connection
    client.admin.command('ping')
    print("✅ MongoDB connection successful!")
    
    # Only set up collections if connection is successful
    db = client.star_tailors
    users_collection = db.users
    customers_collection = db.customers
    bills_collection = db.bills
    tailors_collection = db.tailors
    settings_collection = db.settings
    jobs_collection = db.jobs
    counters_collection = db.counters
    
    # Create indexes for better performance
    def create_indexes():
        try:
            # Customer indexes
            customers_collection.create_index([("phone", 1)], unique=True)
            customers_collection.create_index([("name", "text"), ("phone", "text"), ("email", "text")])
            customers_collection.create_index([("created_at", -1)])
            
            # Bill indexes
            bills_collection.create_index([("customer_id", 1)])
            bills_collection.create_index([("status", 1)])
            bills_collection.create_index([("created_at", -1)])
            bills_collection.create_index([("bill_no", 1)], unique=True)
            
            # Tailor indexes
            tailors_collection.create_index([("phone", 1)], unique=True)
            tailors_collection.create_index([("name", "text"), ("phone", "text"), ("specialization", "text")])
            
            # Job indexes
            jobs_collection.create_index([("tailor_id", 1)])
            jobs_collection.create_index([("status", 1)])
            jobs_collection.create_index([("created_at", -1)])
            jobs_collection.create_index([("bill_id", 1)])
            
            # User indexes
            users_collection.create_index([("username", 1)], unique=True)
            
            print("✅ Database indexes created successfully!")
        except Exception as e:
            print(f"⚠️  Index creation error: {str(e)}")
    
    # Run index creation in background
    index_thread = threading.Thread(target=create_indexes)
    index_thread.daemon = True
    index_thread.start()
    
except Exception as e:
    print(f"❌ MongoDB connection failed: {str(e)}")
    # Create a dummy client to prevent crashes (for development only)
    client = None

db = client.star_tailors if client else None

# Collections (with fallbacks to prevent crashes)
if client is not None:
    users_collection = db.users
    customers_collection = db.customers
    bills_collection = db.bills
    tailors_collection = db.tailors
    settings_collection = db.settings
    jobs_collection = db.jobs
    counters_collection = db.counters
else:
    # Create dummy collections to prevent crashes during development
    users_collection = customers_collection = bills_collection = None
    tailors_collection = settings_collection = jobs_collection = counters_collection = None
    print("⚠️  Running in dummy mode without database connection")

# JWT token decorator
def token_required(f):
    # Disable auth: allow all requests and inject a dummy user
    @wraps(f)
    def decorated(*args, **kwargs):
        current_user = {
            '_id': ObjectId(),
            'username': 'public',
            'role': 'admin'
        }
        return f(current_user, *args, **kwargs)
    return decorated

# Utility: Atomic sequence generator for bill numbers
def get_next_sequence(name: str) -> int:
    if counters_collection is None:
        # Fallback for when DB is not available
        return 1
    
    try:
        doc = counters_collection.find_one_and_update(
            {'_id': name},
            {'$inc': {'seq': 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER
        )
        return int(doc.get('seq', 1))
    except Exception:
        # Fallback in case counters collection isn't available
        return bills_collection.count_documents({}) + 1 if bills_collection is not None else 1

def format_bill_no(n: int, width: int = 3) -> str:
    try:
        return str(int(n)).zfill(width)
    except Exception:
        return str(n)

# Initialize default admin user
def init_default_user():
    if users_collection is None:
        print("⚠️  Skipping default user creation - no database connection")
        return
        
    # Ensure default users exist: admin, tailor, billing
    defaults = [
        ('admin', 'admin123', 'admin'),
        ('tailor', 'tailor123', 'tailor'),
        ('billing', 'billing123', 'billing'),
    ]
    for username, pwd, role in defaults:
        exists = users_collection.find_one({'username': username})
        if exists is None:
            hashed_password = bcrypt.hashpw(pwd.encode('utf-8'), bcrypt.gensalt())
            user_doc = {
                'username': username,
                'password': hashed_password,
                'role': role,
                'created_at': datetime.now()
            }
            users_collection.insert_one(user_doc)
            print(f"Default user created: username={username}, password={pwd}, role={role}")
        else:
            print(f"User already exists: {username}")

# Authentication Routes
@app.route('/api/auth/login', methods=['POST', 'OPTIONS'])
def login():
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    try:
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
        
        if not username or not password:
            return jsonify({'message': 'Username and password are required'}), 400
        
        # For development when DB is not available
        if users_collection is None:
            print("Using demo mode login")
            if username == 'admin' and password == 'admin123':
                token = jwt.encode({
                    'user_id': str(ObjectId()),
                    'username': 'admin',
                    'role': 'admin',
                    'exp': datetime.utcnow() + timedelta(hours=24)
                }, app.config['SECRET_KEY'], algorithm='HS256')
                
                return jsonify({
                    'message': 'Login successful (demo mode)',
                    'token': token,
                    'user': {
                        'id': str(ObjectId()),
                        'username': 'admin',
                        'role': 'admin'
                    }
                }), 200
            else:
                return jsonify({'message': 'Invalid credentials'}), 401
        
        # Use projection to exclude password from initial query
        user = users_collection.find_one({'username': username}, {'password': 1, 'username': 1, 'role': 1})
        
        if user is not None and bcrypt.checkpw(password.encode('utf-8'), user['password']):
            # Create token without password
            token = jwt.encode({
                'user_id': str(user['_id']),
                'username': user['username'],
                'role': user['role'],
                'exp': datetime.utcnow() + timedelta(hours=24)
            }, app.config['SECRET_KEY'], algorithm='HS256')
            
            # Cache user data
            cache_key = f"user_{user['_id']}"
            cache[cache_key] = {
                '_id': user['_id'],
                'username': user['username'],
                'role': user['role']
            }
            
            return jsonify({
                'message': 'Login successful',
                'token': token,
                'user': {
                    'id': str(user['_id']),
                    'username': user['username'],
                    'role': user['role']
                }
            }), 200
        else:
            return jsonify({'message': 'Invalid credentials'}), 401
            
    except Exception as e:
        print(f"Login error: {str(e)}")
        return jsonify({'message': 'Login failed', 'error': str(e)}), 500
    
@app.route('/api/auth/verify', methods=['GET', 'OPTIONS'])
@token_required
def verify_token(current_user):
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    return jsonify({
        'user': {
            'id': str(current_user['_id']),
            'username': current_user['username'],
            'role': current_user['role']
        }
    }), 200

# Customer Management Routes - Optimized
@app.route('/api/customers', methods=['GET', 'OPTIONS'])
@token_required
def get_customers(current_user):
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    try:
        # For development when DB is not available
        if customers_collection is None:
            return jsonify({
                'customers': [],
                'pagination': {
                    'current_page': 1,
                    'total_pages': 1,
                    'total_customers': 0,
                    'has_next': False,
                    'has_prev': False
                }
            }), 200
            
        search = request.args.get('search', '')
        page = int(request.args.get('page', 1))
        limit = min(int(request.args.get('limit', 10)), 50)  # Limit max results to 50
        skip = (page - 1) * limit
        
        query = {}
        if search:
            query = {
                '$or': [
                    {'name': {'$regex': search, '$options': 'i'}},
                    {'phone': {'$regex': search, '$options': 'i'}},
                    {'email': {'$regex': search, '$options': 'i'}}
                ]
            }
        
        # Use projection to only fetch necessary fields
        projection = {
            'name': 1,
            'phone': 1,
            'email': 1,
            'address': 1,
            'created_at': 1,
            'updated_at': 1
        }
        
        customers = list(customers_collection.find(query, projection)
                          .skip(skip)
                          .limit(limit)
                          .sort('_id', -1))
        total_customers = customers_collection.count_documents(query)
        
        for customer in customers:
            customer['_id'] = str(customer['_id'])
            if 'created_at' in customer and customer['created_at']:
                customer['created_at'] = customer['created_at'].isoformat()
            else:
                customer['created_at'] = datetime.now().isoformat()
            
            if 'updated_at' in customer and customer['updated_at']:
                customer['updated_at'] = customer['updated_at'].isoformat()
            else:
                customer['updated_at'] = datetime.now().isoformat()
        
        return jsonify({
            'customers': customers,
            'pagination': {
                'current_page': page,
                'total_pages': (total_customers + limit - 1) // limit,
                'total_customers': total_customers,
                'has_next': skip + limit < total_customers,
                'has_prev': page > 1
            }
        }), 200
        
    except Exception as e:
        print(f"Error in get_customers: {str(e)}")
        return jsonify({'message': 'Failed to get customers', 'error': str(e)}), 500

@app.route('/api/customers', methods=['POST', 'OPTIONS'])
@token_required
def create_customer(current_user):
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    try:
        # For development when DB is not available
        if customers_collection is None:
            data = request.get_json()
            return jsonify({
                'message': 'Customer created successfully (demo mode)',
                'customer': {
                    '_id': str(ObjectId()),
                    'name': data.get('name', ''),
                    'phone': data.get('phone', ''),
                    'email': data.get('email', ''),
                    'address': data.get('address', ''),
                    'notes': data.get('notes', ''),
                    'created_at': datetime.now().isoformat(),
                    'updated_at': datetime.now().isoformat()
                }
            }), 201
            
        data = request.get_json()
        name = data.get('name')
        phone = data.get('phone')
        email = data.get('email')
        address = data.get('address')
        notes = data.get('notes')
        
        if not name or not phone:
            return jsonify({'message': 'Name and phone are required'}), 400
        
        existing_customer = customers_collection.find_one({'phone': phone})
        if existing_customer is not None:
            return jsonify({'message': 'Customer with this phone number already exists'}), 409
        
        new_customer = {
            'name': name,
            'phone': phone,
            'email': email,
            'address': address,
            'notes': notes,
            'created_at': datetime.now(),
            'updated_at': datetime.now()
        }
        
        result = customers_collection.insert_one(new_customer)
        new_customer['_id'] = str(result.inserted_id)
        new_customer['created_at'] = new_customer['created_at'].isoformat()
        new_customer['updated_at'] = new_customer['updated_at'].isoformat()
        
        return jsonify({
            'message': 'Customer created successfully',
            'customer': new_customer
        }), 201
        
    except Exception as e:
        return jsonify({'message': 'Failed to create customer', 'error': str(e)}), 500

@app.route('/api/customers/<customer_id>', methods=['GET', 'OPTIONS'])
@token_required
def get_customer_by_id(current_user, customer_id):
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    try:
        # For development when DB is not available
        if customers_collection is None:
            return jsonify({
                'customer': {
                    '_id': customer_id,
                    'name': 'Demo Customer',
                    'phone': '1234567890',
                    'email': 'demo@example.com',
                    'address': 'Demo Address',
                    'notes': 'Demo notes',
                    'created_at': datetime.now().isoformat(),
                    'updated_at': datetime.now().isoformat(),
                    'bills': [],
                    'total_orders': 0,
                    'total_spent': 0,
                    'outstanding_balance': 0
                }
            }), 200
            
        customer = customers_collection.find_one({'_id': ObjectId(customer_id)})
        if customer is None:
            return jsonify({'message': 'Customer not found'}), 404
        
        customer['_id'] = str(customer['_id'])
        if 'created_at' in customer and customer['created_at']:
            customer['created_at'] = customer['created_at'].isoformat()
        if 'updated_at' in customer and customer['updated_at']:
            customer['updated_at'] = customer['updated_at'].isoformat()
        
        # Get customer's bills with projection for performance
        bills = []
        if bills_collection is not None:
            bills = list(bills_collection.find(
                {'customer_id': ObjectId(customer_id)},
                {'total': 1, 'balance': 1, 'status': 1, 'created_at': 1, 'bill_no_str': 1}
            ))
            for bill in bills:
                bill['_id'] = str(bill['_id'])
                bill['customer_id'] = str(bill['customer_id'])
                if 'created_at' in bill and bill['created_at']:
                    bill['created_at'] = bill['created_at'].isoformat()
                if 'updated_at' in bill and bill['updated_at']:
                    bill['updated_at'] = bill['updated_at'].isoformat()
        
        customer['bills'] = bills
        customer['total_orders'] = len(bills)
        customer['total_spent'] = sum(bill.get('total', 0) for bill in bills)
        customer['outstanding_balance'] = sum(bill.get('balance', 0) for bill in bills if bill.get('status') == 'pending')
        
        return jsonify({'customer': customer}), 200
        
    except Exception as e:
        return jsonify({'message': 'Failed to get customer', 'error': str(e)}), 500

@app.route('/api/customers/<customer_id>', methods=['PUT', 'OPTIONS'])
@token_required
def update_customer(current_user, customer_id):
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    try:
        # For development when DB is not available
        if customers_collection is None:
            data = request.get_json()
            return jsonify({
                'message': 'Customer updated successfully (demo mode)',
                'customer': {
                    '_id': customer_id,
                    'name': data.get('name', 'Demo Customer'),
                    'phone': data.get('phone', '1234567890'),
                    'email': data.get('email', 'demo@example.com'),
                    'address': data.get('address', 'Demo Address'),
                    'notes': data.get('notes', 'Demo notes'),
                    'created_at': datetime.now().isoformat(),
                    'updated_at': datetime.now().isoformat()
                }
            }), 200
            
        data = request.get_json()
        
        customer = customers_collection.find_one({'_id': ObjectId(customer_id)})
        if customer is None:
            return jsonify({'message': 'Customer not found'}), 404
        
        update_data = {
            'name': data.get('name', customer.get('name')),
            'phone': data.get('phone', customer.get('phone')),
            'email': data.get('email', customer.get('email')),
            'address': data.get('address', customer.get('address')),
            'notes': data.get('notes', customer.get('notes')),
            'updated_at': datetime.now()
        }
        
        result = customers_collection.update_one(
            {'_id': ObjectId(customer_id)},
            {'$set': update_data}
        )
        
        if result.modified_count == 0:
            return jsonify({'message': 'No changes made'}), 200
        
        updated_customer = customers_collection.find_one({'_id': ObjectId(customer_id)})
        updated_customer['_id'] = str(updated_customer['_id'])
        updated_customer['created_at'] = updated_customer['created_at'].isoformat()
        updated_customer['updated_at'] = updated_customer['updated_at'].isoformat()
        
        return jsonify({
            'message': 'Customer updated successfully',
            'customer': updated_customer
        }), 200
        
    except Exception as e:
        return jsonify({'message': 'Failed to update customer', 'error': str(e)}), 500

@app.route('/api/customers/<customer_id>', methods=['DELETE', 'OPTIONS'])
@token_required
def delete_customer(current_user, customer_id):
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    try:
        # For development when DB is not available
        if customers_collection is None:
            return jsonify({'message': 'Customer deleted successfully (demo mode)'}), 200
            
        # Validate ObjectId
        try:
            cust_oid = ObjectId(customer_id)
        except Exception:
            return jsonify({'message': 'Invalid customer ID format'}), 400

        # Gather related bill IDs BEFORE deleting the customer/bills
        bill_ids = []
        if bills_collection is not None:
            try:
                bill_ids = [b['_id'] for b in bills_collection.find({'customer_id': cust_oid}, {'_id': 1})]
            except Exception:
                bill_ids = []

        # Delete the customer
        result = customers_collection.delete_one({'_id': cust_oid})
        if result.deleted_count == 0:
            return jsonify({'message': 'Customer not found'}), 404

        # Cascade delete: jobs (workflow) linked to this customer or their bills
        deleted_jobs = 0
        if jobs_collection is not None:
            cascade_query = {'$or': [{'customer_id': cust_oid}]}
            if bill_ids:
                cascade_query['$or'].append({'bill_id': {'$in': bill_ids}})
            try:
                jobs_res = jobs_collection.delete_many(cascade_query)
                deleted_jobs = jobs_res.deleted_count
            except Exception:
                deleted_jobs = 0

        # Delete associated bills
        deleted_bills = 0
        if bills_collection is not None:
            try:
                bills_res = bills_collection.delete_many({'customer_id': cust_oid})
                deleted_bills = bills_res.deleted_count
            except Exception:
                deleted_bills = 0
        
        return jsonify({
            'message': 'Customer and related data deleted successfully',
            'deleted_bills': deleted_bills,
            'deleted_jobs': deleted_jobs
        }), 200
        
    except Exception as e:
        return jsonify({'message': 'Failed to delete customer', 'error': str(e)}), 500

@app.route('/api/customers/stats', methods=['GET', 'OPTIONS'])
@token_required
def get_customer_stats(current_user):
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    try:
        # For development when DB is not available
        if customers_collection is None:
            return jsonify({
                'total_customers': 0,
                'customers_with_outstanding': 0,
                'total_outstanding_amount': 0
            }), 200
            
        total_customers = customers_collection.count_documents({})
        
        # Count customers with outstanding balances using aggregation for better performance
        pipeline = [
            {
                '$lookup': {
                    'from': 'bills',
                    'localField': '_id',
                    'foreignField': 'customer_id',
                    'as': 'bills'
                }
            },
            {
                '$match': {
                    'bills.status': 'pending',
                    'bills.balance': {'$gt': 0}
                }
            },
            {
                '$count': 'count'
            }
        ]
        
        outstanding_result = list(customers_collection.aggregate(pipeline))
        customers_with_outstanding = outstanding_result[0]['count'] if outstanding_result else 0
        
        # Calculate total outstanding amount
        pipeline = [
            {
                '$match': {
                    'status': 'pending',
                    'balance': {'$gt': 0}
                }
            },
            {
                '$group': {
                    '_id': None,
                    'total_outstanding': {'$sum': '$balance'}
                }
            }
        ]
        
        outstanding_amount_result = list(bills_collection.aggregate(pipeline)) if bills_collection is not None else []
        total_outstanding_amount = outstanding_amount_result[0]['total_outstanding'] if outstanding_amount_result else 0
        
        return jsonify({
            'total_customers': total_customers,
            'customers_with_outstanding': customers_with_outstanding,
            'total_outstanding_amount': total_outstanding_amount
        }), 200
        
    except Exception as e:
        return jsonify({'message': 'Failed to get customer stats', 'error': str(e)}), 500

# Billing System Routes - Optimized
@app.route('/api/bills', methods=['GET', 'OPTIONS'])
@token_required
def get_bills(current_user):
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    try:
        # For development when DB is not available
        if bills_collection is None:
            return jsonify({
                'bills': [],
                'pagination': {
                    'current_page': 1,
                    'total_pages': 1,
                    'total_bills': 0,
                    'has_next': False,
                    'has_prev': False
                }
            }), 200
            
        search = request.args.get('search', '')
        status = request.args.get('status', '')
        customer_id = request.args.get('customer_id', '')
        page = int(request.args.get('page', 1))
        limit = min(int(request.args.get('limit', 10)), 50)  # Limit max results
        skip = (page - 1) * limit
        
        query = {}
        
        if search:
            # Use text search if indexes are available, otherwise use regex
            try:
                # First try to find customer by ID for exact matches
                if ObjectId.is_valid(search):
                    query['customer_id'] = ObjectId(search)
                else:
                    # Use text search or fallback to regex
                    customers = customers_collection.find({
                        '$or': [
                            {'name': {'$regex': search, '$options': 'i'}},
                            {'phone': {'$regex': search, '$options': 'i'}}
                        ]
                    }, {'_id': 1}).limit(10)
                    customer_ids = [customer['_id'] for customer in customers]
                    if customer_ids:
                        query['customer_id'] = {'$in': customer_ids}
            except Exception as e:
                print(f"Search optimization error: {str(e)}")
        
        if status:
            query['status'] = status
        
        if customer_id:
            try:
                query['customer_id'] = ObjectId(customer_id)
            except:
                return jsonify({'message': 'Invalid customer ID format'}), 400
        
        # Use projection to only fetch necessary fields
        projection = {
            'customer_id': 1,
            'customer_name': 1,
            'customer_phone': 1,
            'total': 1,
            'balance': 1,
            'status': 1,
            'created_at': 1,
            'bill_no_str': 1
        }
        
        bills = list(bills_collection.find(query, projection)
                      .skip(skip)
                      .limit(limit)
                      .sort('created_at', -1))
        total_bills = bills_collection.count_documents(query)
        
        formatted_bills = []
        customer_cache = {}
        
        for bill in bills:
            try:
                bill['_id'] = str(bill['_id'])
                bill['customer_id'] = str(bill['customer_id'])
                
                # Handle missing created_at field safely
                if 'created_at' in bill and bill['created_at']:
                    bill['created_at'] = bill['created_at'].isoformat()
                else:
                    bill['created_at'] = datetime.now().isoformat()
                
                # Cache customer data to avoid multiple lookups
                customer_key = bill['customer_id']
                if customer_key not in customer_cache:
                    customer = customers_collection.find_one(
                        {'_id': ObjectId(bill['customer_id'])},
                        {'name': 1, 'phone': 1}
                    )
                    if customer:
                        # Convert ObjectId to string to avoid JSON serialization error
                        customer['_id'] = str(customer['_id'])
                        customer_cache[customer_key] = customer
                    else:
                        customer_cache[customer_key] = {
                            'name': 'Unknown',
                            'phone': 'N/A'
                        }
                
                bill['customer'] = customer_cache[customer_key]
                formatted_bills.append(bill)
            except Exception as e:
                print(f"Error formatting bill: {str(e)}")
                continue
        
        return jsonify({
            'bills': formatted_bills,
            'pagination': {
                'current_page': page,
                'total_pages': (total_bills + limit - 1) // limit,
                'total_bills': total_bills,
                'has_next': skip + limit < total_bills,
                'has_prev': page > 1
            }
        }), 200
        
    except Exception as e:
        print(f"Error in get_bills: {str(e)}")
        return jsonify({
            'message': 'Failed to get bills', 
            'error': str(e)
        }), 500
    
@app.route('/api/bills', methods=['POST', 'OPTIONS'])
@token_required
def create_bill(current_user):
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    try:
        data = request.get_json()
        
        required_fields = ['customer_id', 'customer_name', 'items']
        for field in required_fields:
            if field not in data:
                return jsonify({'message': f'Missing required field: {field}'}), 400

        # For development when DB is not available
        if bills_collection is None:
            next_no = 1
            bill_no_str = format_bill_no(next_no, 3)
            
            new_bill = {
                '_id': str(ObjectId()),
                'customer_id': data['customer_id'],
                'customer_name': data.get('customer_name', 'Demo Customer'),
                'customer_phone': data.get('customer_phone', '1234567890'),
                'customer_address': data.get('customer_address', 'Demo Address'),
                'items': data['items'],
                'subtotal': float(data.get('subtotal', 0)),
                'discount': float(data.get('discount', 0)),
                'total': float(data.get('total', 0)),
                'advance': float(data.get('advance', 0)),
                'balance': float(data.get('balance', 0)),
                'due_date': data.get('due_date', ''),
                'special_instructions': data.get('special_instructions', ''),
                'design_images': data.get('design_images', []),
                'drawings': data.get('drawings', []),
                'signature': data.get('signature', ''),
                'status': data.get('status', 'pending'),
                'created_by': str(current_user['_id']),
                'created_at': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat(),
                'bill_no': int(next_no),
                'bill_no_str': bill_no_str,
            }

            return jsonify({
                'message': 'Bill created successfully (demo mode)',
                'bill': new_bill
            }), 201

        try:
            customer_id = ObjectId(data['customer_id'])
        except:
            return jsonify({'message': 'Invalid customer ID format'}), 400
            
        customer = customers_collection.find_one({'_id': customer_id})
        if customer is None:
            return jsonify({'message': 'Customer not found'}), 404

        if not isinstance(data['items'], list) or len(data['items']) == 0:
            return jsonify({'message': 'Items must be a non-empty array'}), 400

        # Generate sequential bill number
        next_no = get_next_sequence('bill_no')

        new_bill = {
            'customer_id': customer_id,
            'customer_name': data.get('customer_name', customer['name']),
            'customer_phone': data.get('customer_phone', customer.get('phone', '')),
            'customer_address': data.get('customer_address', customer.get('address', '')),
            'items': data['items'],
            'subtotal': float(data.get('subtotal', 0)),
            'discount': float(data.get('discount', 0)),
            'total': float(data.get('total', 0)),
            'advance': float(data.get('advance', 0)),
            'balance': float(data.get('balance', 0)),
            'due_date': data.get('due_date', ''),
            'special_instructions': data.get('special_instructions', ''),
            'design_images': data.get('design_images', []),
            'drawings': data.get('drawings', []),
            'signature': data.get('signature', ''),
            'status': data.get('status', 'pending'),
            'created_by': str(current_user['_id']),
            'created_at': datetime.now(),
            'updated_at': datetime.now(),
            # New fields for sequential bill number
            'bill_no': int(next_no),
            'bill_no_str': format_bill_no(next_no, 3),
        }

        result = bills_collection.insert_one(new_bill)

        # Auto-create a job linked to this bill to kick off workflow at 'cutting'
        try:
            if jobs_collection is not None:
                current_time_iso = datetime.now().isoformat()
                default_stages = [
                    {'name': 'cutting', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': current_time_iso},
                    {'name': 'stitching', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': current_time_iso},
                    {'name': 'finishing', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': current_time_iso},
                    {'name': 'packaging', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': current_time_iso},
                ]
                auto_job = {
                    'title': f"Order {format_bill_no(next_no, 3)} - {new_bill['customer_name']}",
                    'description': f"Auto-created job for bill {format_bill_no(next_no, 3)}",
                    'tailor_id': None,  # to be assigned later
                    'bill_id': result.inserted_id,
                    'status': 'assigned',
                    'priority': 'medium',
                    'due_date': datetime.fromisoformat(new_bill['due_date']) if new_bill.get('due_date') else None,
                    'created_by': str(current_user['_id']),
                    'created_at': datetime.now(),
                    'updated_at': datetime.now(),
                    'workflow_stages': default_stages,
                    'current_stage': 'cutting',
                    'progress_percentage': 0,
                }
                jobs_collection.insert_one(auto_job)
        except Exception as e:
            print(f"⚠️  Failed to auto-create job for bill: {str(e)}")

        new_bill['_id'] = str(result.inserted_id)
        new_bill['customer_id'] = str(new_bill['customer_id'])
        new_bill['created_at'] = new_bill['created_at'].isoformat()
        new_bill['updated_at'] = new_bill['updated_at'].isoformat()

        return jsonify({
            'message': 'Bill created successfully',
            'bill': new_bill
        }), 201
        
    except Exception as e:
        print(f"Error creating bill: {str(e)}")
        return jsonify({
            'message': 'Failed to create bill',
            'error': str(e),
            'received_data': data
        }), 500

# Settings Routes
@app.route('/api/settings/upi', methods=['GET', 'OPTIONS'])
@token_required
def get_upi_settings(current_user):
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    try:
        cache_key = "upi_settings"
        cached_settings = cache.get(cache_key)
        
        if cached_settings:
            return jsonify(cached_settings), 200
            
        settings = None
        if settings_collection is not None:
            settings = settings_collection.find_one({'type': 'upi_settings'})
            
        if settings is None:
            result = {
                'upi_id': 'startailors@paytm',
                'business_name': 'Star Tailors'
            }
            cache[cache_key] = result
            return jsonify(result), 200
        
        result = {
            'upi_id': settings.get('upi_id', 'startailors@paytm'),
            'business_name': settings.get('business_name', 'Star Tailors')
        }
        cache[cache_key] = result
        return jsonify(result), 200
        
    except Exception as e:
        return jsonify({'message': 'Failed to get UPI settings', 'error': str(e)}), 500

@app.route('/api/settings/upi', methods=['PUT', 'OPTIONS'])
@token_required
def update_upi_settings(current_user):
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    try:
        if current_user['role'] != 'admin':
            return jsonify({'message': 'Access denied'}), 403
        
        # For development when DB is not available
        if settings_collection is None:
            return jsonify({'message': 'UPI settings updated successfully (demo mode)'}), 200
            
        data = request.get_json()
        upi_id = data.get('upi_id')
        business_name = data.get('business_name')
        
        if not upi_id or not business_name:
            return jsonify({'message': 'UPI ID and business name are required'}), 400
        
        settings_collection.update_one(
            {'type': 'upi_settings'},
            {
                '$set': {
                    'upi_id': upi_id,
                    'business_name': business_name,
                    'updated_at': datetime.now()
                }
            },
            upsert=True
        )
        
        # Clear cache
        cache.pop("upi_settings", None)
        
        return jsonify({'message': 'UPI settings updated successfully'}), 200
        
    except Exception as e:
        return jsonify({'message': 'Failed to update UPI settings', 'error': str(e)}), 500

# Business information settings
@app.route('/api/settings/business', methods=['GET', 'OPTIONS'])
@token_required
def get_business_settings(current_user):
    if request.method == 'OPTIONS':
        return jsonify(), 200
    try:
        cache_key = "business_settings"
        cached_settings = cache.get(cache_key)
        
        if cached_settings:
            return jsonify(cached_settings), 200
            
        settings = None
        if settings_collection is not None:
            settings = settings_collection.find_one({'type': 'business_info'})
            
        if settings is None:
            # Defaults
            result = {
                'business_name': 'STAR TAILORS',
                'address': 'Baramati, Maharashtra',
                'phone': '+91 00000 00000',
                'email': 'info@startailors.com'
            }
            cache[cache_key] = result
            return jsonify(result), 200
        
        result = {
            'business_name': settings.get('business_name', 'STAR TAILORS'),
            'address': settings.get('address', ''),
            'phone': settings.get('phone', ''),
            'email': settings.get('email', '')
        }
        cache[cache_key] = result
        return jsonify(result), 200
    except Exception as e:
        return jsonify({'message': 'Failed to get business settings', 'error': str(e)}), 500

@app.route('/api/settings/business', methods=['PUT', 'OPTIONS'])
@token_required
def update_business_settings(current_user):
    if request.method == 'OPTIONS':
        return jsonify(), 200
    try:
        if current_user['role'] != 'admin':
            return jsonify({'message': 'Access denied'}), 403
            
        # For development when DB is not available
        if settings_collection is None:
            return jsonify({'message': 'Business settings updated successfully (demo mode)'}), 200
            
        data = request.get_json()
        update_doc = {
            'business_name': data.get('business_name', 'STAR TAILORS'),
            'address': data.get('address', ''),
            'phone': data.get('phone', ''),
            'email': data.get('email', ''),
            'updated_at': datetime.now()
        }
        settings_collection.update_one(
            {'type': 'business_info'},
            {'$set': update_doc},
            upsert=True
        )
        
        # Clear cache
        cache.pop("business_settings", None)
        
        return jsonify({'message': 'Business settings updated successfully'}), 200
    except Exception as e:
        return jsonify({'message': 'Failed to update business settings', 'error': str(e)}), 500

# Tailor Management Routes
@app.route('/api/tailors', methods=['GET', 'OPTIONS'])
@token_required
def get_tailors(current_user):
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    try:
        # For development when DB is not available
        if tailors_collection is None:
            return jsonify({
                'tailors': [],
                'pagination': {
                    'current_page': 1,
                    'total_pages': 1,
                    'total_tailors': 0,
                    'has_next': False,
                    'has_prev': False
                }
            }), 200
            
        search = request.args.get('search', '')
        page = int(request.args.get('page', 1))
        limit = min(int(request.args.get('limit', 10)), 50)
        skip = (page - 1) * limit
        
        query = {}
        if search:
            query = {
                '$or': [
                    {'name': {'$regex': search, '$options': 'i'}},
                    {'phone': {'$regex': search, '$options': 'i'}},
                    {'specialization': {'$regex': search, '$options': 'i'}}
                ]
            }
        
        # Use projection for better performance
        projection = {
            'name': 1,
            'phone': 1,
            'email': 1,
            'specialization': 1,
            'experience': 1,
            'status': 1,
            'created_at': 1,
            'updated_at': 1
        }
        
        tailors = list(tailors_collection.find(query, projection).skip(skip).limit(limit).sort('created_at', -1))
        total_tailors = tailors_collection.count_documents(query)
        
        for tailor in tailors:
            tailor['_id'] = str(tailor['_id'])
            # Handle missing timestamp fields safely
            if 'created_at' in tailor and tailor['created_at']:
                tailor['created_at'] = tailor['created_at'].isoformat()
            else:
                tailor['created_at'] = datetime.now().isoformat()
                
            if 'updated_at' in tailor and tailor['updated_at']:
                tailor['updated_at'] = tailor['updated_at'].isoformat()
            else:
                tailor['updated_at'] = datetime.now().isoformat()
        
        return jsonify({
            'tailors': tailors,
            'pagination': {
                'current_page': page,
                'total_pages': (total_tailors + limit - 1) // limit,
                'total_tailors': total_tailors,
                'has_next': skip + limit < total_tailors,
                'has_prev': page > 1
            }
        }), 200
        
    except Exception as e:
        return jsonify({'message': 'Failed to get tailors', 'error': str(e)}), 500

@app.route('/api/tailors', methods=['POST', 'OPTIONS'])
@token_required
def create_tailor(current_user):
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    try:
        # For development when DB is not available
        if tailors_collection is None:
            data = request.get_json()
            return jsonify({
                'message': 'Tailor created successfully (demo mode)',
                'tailor': {
                    '_id': str(ObjectId()),
                    'name': data.get('name', 'Demo Tailor'),
                    'phone': data.get('phone', '1234567890'),
                    'email': data.get('email', 'demo@example.com'),
                    'specialization': data.get('specialization', 'General Tailoring'),
                    'experience': data.get('experience', '1 year'),
                    'status': 'active',
                    'created_at': datetime.now().isoformat(),
                    'updated_at': datetime.now().isoformat()
                }
            }), 201
            
        data = request.get_json()
        name = data.get('name')
        phone = data.get('phone')
        email = data.get('email')
        specialization = data.get('specialization')
        experience = data.get('experience')
        
        if not name or not phone:
            return jsonify({'message': 'Name and phone are required'}), 400
        
        existing_tailor = tailors_collection.find_one({'phone': phone})
        if existing_tailor is not None:
            return jsonify({'message': 'Tailor with this phone number already exists'}), 409
        
        new_tailor = {
            'name': name,
            'phone': phone,
            'email': email,
            'specialization': specialization,
            'experience': experience,
            'status': 'active',
            'created_at': datetime.now(),
            'updated_at': datetime.now()
        }
        
        result = tailors_collection.insert_one(new_tailor)
        new_tailor['_id'] = str(result.inserted_id)
        new_tailor['created_at'] = new_tailor['created_at'].isoformat()
        new_tailor['updated_at'] = new_tailor['updated_at'].isoformat()
        
        return jsonify({
            'message': 'Tailor created successfully',
            'tailor': new_tailor
        }), 201
        
    except Exception as e:
        return jsonify({'message': 'Failed to create tailor', 'error': str(e)}), 500

@app.route('/api/tailors/<tailor_id>/jobs', methods=['GET', 'OPTIONS'])
@token_required
def get_tailor_jobs(current_user, tailor_id):
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    try:
        # For development when DB is not available
        if tailors_collection is None or jobs_collection is None:
            return jsonify({
                'jobs': [],
                'tailor': {
                    'id': tailor_id,
                    'name': 'Demo Tailor',
                    'phone': '1234567890',
                    'specialization': 'General Tailoring'
                },
                'pagination': {
                    'current_page': 1,
                    'total_pages': 1,
                    'total_jobs': 0,
                    'has_next': False,
                    'has_prev': False
                }
            }), 200
            
        tailor = tailors_collection.find_one({'_id': ObjectId(tailor_id)})
        
        if tailor is None:
            tailor = tailors_collection.find_one({'user_id': tailor_id})
        
        if tailor is None and str(current_user['_id']) == tailor_id:
            new_tailor = {
                'name': current_user.get('username', 'Unknown Tailor'),
                'phone': current_user.get('phone', ''),
                'email': current_user.get('email', ''),
                'specialization': 'General Tailoring',
                'experience': '0 years',
                'status': 'active',
                'user_id': str(current_user['_id']),
                'created_at': datetime.now(),
                'updated_at': datetime.now()
            }
            
            result = tailors_collection.insert_one(new_tailor)
            tailor = tailors_collection.find_one({'_id': result.inserted_id})
        
        if tailor is None:
            return jsonify({'message': 'Tailor not found'}), 404
        
        status = request.args.get('status', '')
        page = int(request.args.get('page', 1))
        limit = min(int(request.args.get('limit', 10)), 50)
        skip = (page - 1) * limit
        
        query = {'tailor_id': tailor['_id']}
        if status:
            query['status'] = status
        
        # Use projection for better performance
        projection = {
            'title': 1,
            'description': 1,
            'status': 1,
            'priority': 1,
            'due_date': 1,
            'created_at': 1,
            'bill_id': 1
        }
        
        jobs = list(jobs_collection.find(query, projection).skip(skip).limit(limit).sort('created_at', -1))
        total_jobs = jobs_collection.count_documents(query)
        
        for job in jobs:
            job['_id'] = str(job['_id'])
            job['tailor_id'] = str(job['tailor_id'])
            job['bill_id'] = str(job['bill_id']) if job.get('bill_id') else None
            job['created_at'] = job['created_at'].isoformat()
            job['updated_at'] = job['updated_at'].isoformat()
            if job.get('due_date'):
                job['due_date'] = job['due_date'].isoformat()
        
        return jsonify({
            'jobs': jobs,
            'tailor': {
                'id': str(tailor['_id']),
                'name': tailor['name'],
                'phone': tailor['phone'],
                'specialization': tailor.get('specialization', '')
            },
            'pagination': {
                'current_page': page,
                'total_pages': (total_jobs + limit - 1) // limit,
                'total_jobs': total_jobs,
                'has_next': skip + limit < total_jobs,
                'has_prev': page > 1
            }
        }), 200
        
    except Exception as e:
        print(f"Error in get_tailor_jobs: {str(e)}")
        return jsonify({'message': 'Failed to get tailor jobs', 'error': str(e)}), 500

# Job Management Routes
@app.route('/api/jobs', methods=['GET', 'OPTIONS'])
@token_required
def get_jobs(current_user):
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    try:
        # For development when DB is not available
        if jobs_collection is None:
            return jsonify({
                'jobs': [],
                'pagination': {
                    'current_page': 1,
                    'total_pages': 1,
                    'total_jobs': 0,
                    'has_next': False,
                    'has_prev': False
                }
            }), 200
            
        search = request.args.get('search', '')
        status = request.args.get('status', '')
        tailor_id = request.args.get('tailor_id', '')
        page = int(request.args.get('page', 1))
        limit = min(int(request.args.get('limit', 10)), 50)
        skip = (page - 1) * limit
        light = str(request.args.get('light', 'false')).lower() == 'true'
        
        query = {}
        if search:
            query['$or'] = [
                {'title': {'$regex': search, '$options': 'i'}},
                {'description': {'$regex': search, '$options': 'i'}}
            ]
        
        if status:
            query['status'] = status
            
        if tailor_id:
            query['tailor_id'] = ObjectId(tailor_id)
        
        # Use projection for better performance
        projection = {
            'title': 1,
            'description': 1,
            'tailor_id': 1,
            'status': 1,
            'priority': 1,
            'due_date': 1,
            'created_at': 1,
            'updated_at': 1,
            'bill_id': 1,
            # Include workflow fields for UI grouping
            'workflow_stages': 1,
            'current_stage': 1,
            'progress_percentage': 1,
        }
        
        jobs = list(jobs_collection.find(query, projection).skip(skip).limit(limit).sort('created_at', -1))
        total_jobs = jobs_collection.count_documents(query)

        # Fast path for light responses: avoid expensive cross-collection lookups
        if light:
            for job in jobs:
                job['_id'] = str(job['_id'])
                if 'tailor_id' in job and job['tailor_id']:
                    try:
                        job['tailor_id'] = str(job['tailor_id'])
                    except Exception:
                        job['tailor_id'] = str(job.get('tailor_id')) if job.get('tailor_id') is not None else None
                else:
                    job['tailor_id'] = None
                job['bill_id'] = str(job['bill_id']) if job.get('bill_id') else None

                # Ensure workflow defaults without extra calculations
                if 'workflow_stages' not in job or not job.get('workflow_stages'):
                    job['workflow_stages'] = []
                    job['current_stage'] = 'cutting'
                    job['progress_percentage'] = 0

            return jsonify({
                'jobs': jobs,
                'pagination': {
                    'current_page': page,
                    'total_pages': ((total_jobs + limit - 1) // limit) if limit else 1,
                    'total_jobs': total_jobs,
                    'has_next': (page * limit) < total_jobs,
                    'has_prev': page > 1
                }
            }), 200
        
        # Caches for lookups
        bill_cache = {}
        tailor_cache = {}
        
        for job in jobs:
            job['_id'] = str(job['_id'])
            # Tailor may be unassigned on auto-created jobs
            if 'tailor_id' in job and job['tailor_id']:
                try:
                    job['tailor_id'] = str(job['tailor_id'])
                except Exception:
                    job['tailor_id'] = str(job.get('tailor_id')) if job.get('tailor_id') is not None else None
            else:
                job['tailor_id'] = None
            job['bill_id'] = str(job['bill_id']) if job.get('bill_id') else None
            
            # Enrich with bill summary (customer details, items)
            if job['bill_id']:
                bill_key = job['bill_id']
                bill_data = bill_cache.get(bill_key)
                if bill_data is None:
                    try:
                        b = bills_collection.find_one(
                            {'_id': ObjectId(job['bill_id'])},
                            {
                                'bill_no': 1,
                                'bill_no_str': 1,
                                'customer_id': 1,
                                'customer_name': 1,
                                'customer_phone': 1,
                                'customer_address': 1,
                                'items': 1,
                                'special_instructions': 1,
                                'design_images': 1,
                                'drawings': 1,
                                'signature': 1,
                                'subtotal': 1,
                                'discount': 1,
                                'total': 1,
                                'advance': 1,
                                'balance': 1,
                            }
                        )
                        if b is not None:
                            bill_data = {
                                '_id': str(b.get('_id')),
                                'bill_no': b.get('bill_no'),
                                'bill_no_str': b.get('bill_no_str'),
                                'customer_id': str(b.get('customer_id')) if b.get('customer_id') else None,
                                'customer_name': b.get('customer_name'),
                                'customer_phone': b.get('customer_phone'),
                                'customer_address': b.get('customer_address'),
'items': [
                                    {
                                        'type': it.get('type'),
                                        'description': it.get('description'),
                                        'quantity': it.get('quantity'),
                                        'measurements': it.get('measurements', {}),
                                    }
                                    for it in (b.get('items') or [])
                                ],
                                'special_instructions': b.get('special_instructions'),
                                'design_images': b.get('design_images', []),
                                'drawings': b.get('drawings', []),
                                'signature': b.get('signature'),
                                'subtotal': b.get('subtotal'),
                                'discount': b.get('discount'),
                                'total': b.get('total'),
                                'advance': b.get('advance'),
                                'balance': b.get('balance'),
                            }
                        else:
                            bill_data = None
                    except Exception:
                        bill_data = None
                    bill_cache[bill_key] = bill_data
                if bill_data is not None:
                    job['bill'] = bill_data
            
            # Ensure workflow defaults for older jobs
            if 'workflow_stages' not in job or not job.get('workflow_stages'):
                current_time = datetime.now().isoformat()
                job['workflow_stages'] = [
                    {'name': 'cutting', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': current_time},
                    {'name': 'stitching', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': current_time},
                    {'name': 'finishing', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': current_time},
                    {'name': 'packaging', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': current_time},
                ]
                job['current_stage'] = 'cutting'
                job['progress_percentage'] = 0
            else:
                # Backfill current_stage/progress if missing
                if not job.get('current_stage'):
                    # Determine first non-completed stage
                    for s in job['workflow_stages']:
                        if s.get('status') != 'completed':
                            job['current_stage'] = s.get('name', 'cutting')
                            break
                    if not job.get('current_stage'):
                        job['current_stage'] = 'cutting'
                if job.get('progress_percentage') is None:
                    total = len(job['workflow_stages'])
                    completed = sum(1 for s in job['workflow_stages'] if s.get('status') == 'completed')
                    job['progress_percentage'] = (completed / total) * 100 if total else 0
            
            # Enrich stage assigned tailor details
            try:
                for stage in job['workflow_stages']:
                    assn = stage.get('assigned_tailor')
                    if assn and isinstance(assn, str) and ObjectId.is_valid(assn):
                        details = tailor_cache.get(assn)
                        if details is None:
                            tdoc = None
                            try:
                                tdoc = tailors_collection.find_one({'_id': ObjectId(assn)}, {'name': 1, 'phone': 1})
                            except Exception:
                                tdoc = None
                            if tdoc is not None:
                                details = {'name': tdoc.get('name', ''), 'phone': tdoc.get('phone', '')}
                            else:
                                udoc = None
                                try:
                                    udoc = users_collection.find_one({'_id': ObjectId(assn)}, {'username': 1}) if users_collection is not None else None
                                except Exception:
                                    udoc = None
                                if udoc is not None:
                                    details = {'name': udoc.get('username', ''), 'phone': ''}
                                else:
                                    details = None
                            tailor_cache[assn] = details
                        if details is not None:
                            stage['assigned_tailor_name'] = details.get('name')
                            stage['assigned_tailor_phone'] = details.get('phone')
            except Exception:
                pass
            
            # Handle missing timestamp fields safely
            if 'created_at' in job and job['created_at']:
                job['created_at'] = job['created_at'].isoformat()
            else:
                job['created_at'] = datetime.now().isoformat()
                
            if 'updated_at' in job and job['updated_at']:
                job['updated_at'] = job['updated_at'].isoformat()
            else:
                job['updated_at'] = datetime.now().isoformat()
            
            # Attach basic tailor info when available
            if job['tailor_id']:
                try:
                    details = tailor_cache.get(job['tailor_id'])
                    if details is None:
                        tdoc = tailors_collection.find_one({'_id': ObjectId(job['tailor_id'])}, {'name': 1, 'phone': 1})
                        details = {'name': tdoc.get('name', ''), 'phone': tdoc.get('phone', '')} if tdoc else None
                        tailor_cache[job['tailor_id']] = details
                except Exception:
                    details = None
                if details is not None:
                    job['tailor'] = {
                        'name': details.get('name', ''),
                        'phone': details.get('phone', '')
                    }
        
        return jsonify({
            'jobs': jobs,
            'pagination': {
                'current_page': page,
                'total_pages': (total_jobs + limit - 1) // limit,
                'total_jobs': total_jobs,
                'has_next': skip + limit < total_jobs,
                'has_prev': page > 1
            }
        }), 200
        
    except Exception as e:
        return jsonify({'message': 'Failed to get jobs', 'error': str(e)}), 500

@app.route('/api/jobs', methods=['POST', 'OPTIONS'])
@token_required
def create_job(current_user):
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    try:
        # For development when DB is not available
        if jobs_collection is None:
            data = request.get_json()
            return jsonify({
                'message': 'Job created successfully (demo mode)',
                'job': {
                    '_id': str(ObjectId()),
                    'title': data.get('title', 'Demo Job'),
                    'description': data.get('description', 'Demo description'),
                    'tailor_id': data.get('tailor_id', str(ObjectId())),
                    'bill_id': data.get('bill_id', str(ObjectId())),
                    'status': 'assigned',
                    'priority': data.get('priority', 'medium'),
                    'due_date': data.get('due_date', datetime.now().isoformat()),
                    'created_by': str(current_user['_id']),
                    'created_at': datetime.now().isoformat(),
                    'updated_at': datetime.now().isoformat()
                }
            }), 201
            
        data = request.get_json()
        title = data.get('title')
        description = data.get('description')
        tailor_id = data.get('tailor_id')
        bill_id = data.get('bill_id')
        priority = data.get('priority', 'medium')
        due_date = data.get('due_date')
        
        if not title or not tailor_id:
            return jsonify({'message': 'Title and tailor ID are required'}), 400
        
        tailor = tailors_collection.find_one({'_id': ObjectId(tailor_id)})
        if tailor is None:
            return jsonify({'message': 'Tailor not found'}), 404
        
        # Initialize default workflow stages for this job
        current_time = datetime.now().isoformat()
        default_stages = [
            {'name': 'cutting', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': current_time},
            {'name': 'stitching', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': current_time},
            {'name': 'finishing', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': current_time},
            {'name': 'packaging', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': current_time},
        ]

        new_job = {
            'title': title,
            'description': description,
            'tailor_id': ObjectId(tailor_id),
            'bill_id': ObjectId(bill_id) if bill_id else None,
            'status': 'assigned',
            'priority': priority,
            'due_date': datetime.fromisoformat(due_date) if due_date else None,
            'created_by': str(current_user['_id']),
            'created_at': datetime.now(),
            'updated_at': datetime.now(),
            'workflow_stages': default_stages,
            'current_stage': 'cutting',
            'progress_percentage': 0,
        }
        
        result = jobs_collection.insert_one(new_job)
        new_job['_id'] = str(result.inserted_id)
        new_job['tailor_id'] = str(new_job['tailor_id'])
        new_job['bill_id'] = str(new_job['bill_id']) if new_job['bill_id'] else None
        new_job['created_at'] = new_job['created_at'].isoformat()
        new_job['updated_at'] = new_job['updated_at'].isoformat()
        
        return jsonify({
            'message': 'Job created successfully',
            'job': new_job
        }), 201
        
    except Exception as e:
        return jsonify({'message': 'Failed to create job', 'error': str(e)}), 500

@app.route('/api/jobs/<job_id>/status', methods=['PUT', 'OPTIONS'])
@token_required
def update_job_status(current_user, job_id):
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    try:
        # For development when DB is not available
        if jobs_collection is None:
            return jsonify({'message': 'Job status updated successfully (demo mode)'}), 200
            
        data = request.get_json()
        status = data.get('status')
        
        if not status:
            return jsonify({'message': 'Status is required'}), 400
        
        valid_statuses = ['assigned', 'in_progress', 'completed', 'delivered', 'cancelled']
        if status not in valid_statuses:
            return jsonify({'message': 'Invalid status'}), 400
        
        result = jobs_collection.update_one(
            {'_id': ObjectId(job_id)},
            {
                '$set': {
                    'status': status,
                    'updated_at': datetime.now()
                }
            }
        )
        
        if result.matched_count == 0:
            return jsonify({'message': 'Job not found'}), 404
        
        return jsonify({'message': 'Job status updated successfully'}), 200
        
    except Exception as e:
        return jsonify({'message': 'Failed to update job status', 'error': str(e)}), 500

# Delete a job (admin only)
@app.route('/api/jobs/<job_id>', methods=['DELETE', 'OPTIONS'])
@token_required
def delete_job(current_user, job_id):
    if request.method == 'OPTIONS':
        return jsonify(), 200
    try:
        if jobs_collection is None:
            return jsonify({'message': 'Job deleted (demo mode)', 'deleted_job_id': job_id}), 200
        # Only admin can delete jobs
        if current_user.get('role') != 'admin':
            return jsonify({'message': 'Access denied'}), 403
        result = jobs_collection.delete_one({'_id': ObjectId(job_id)})
        if result.deleted_count == 0:
            return jsonify({'message': 'Job not found'}), 404
        return jsonify({'message': 'Job deleted successfully', 'deleted_job_id': job_id}), 200
    except Exception as e:
        return jsonify({'message': 'Failed to delete job', 'error': str(e)}), 500

# Workflow Management Routes
@app.route('/api/jobs/<job_id>/workflow', methods=['GET', 'OPTIONS'])
@token_required
def get_job_workflow(current_user, job_id):
    """Get workflow stages for a specific job"""
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    try:
        if jobs_collection is None:
            return jsonify({'message': 'Database not available', 'workflow_stages': []}), 200
            
        job = jobs_collection.find_one({'_id': ObjectId(job_id)})
        if job is None:
            return jsonify({'message': 'Job not found'}), 404
            
        # Ensure job has workflow_stages
        if 'workflow_stages' not in job:
            # Initialize workflow stages for existing jobs
            default_stages = [
                {'name': 'cutting', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': datetime.now().isoformat()},
                {'name': 'stitching', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': datetime.now().isoformat()},
                {'name': 'finishing', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': datetime.now().isoformat()},
                {'name': 'packaging', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': datetime.now().isoformat()}
            ]
            jobs_collection.update_one(
                {'_id': ObjectId(job_id)},
                {'$set': {'workflow_stages': default_stages, 'current_stage': 'cutting', 'progress_percentage': 0}}
            )
            job['workflow_stages'] = default_stages
            job['current_stage'] = 'cutting'
            job['progress_percentage'] = 0
        
        return jsonify({
            'job_id': str(job['_id']),
            'title': job.get('title', ''),
            'workflow_stages': job['workflow_stages'],
            'current_stage': job.get('current_stage', 'cutting'),
            'progress_percentage': job.get('progress_percentage', 0)
        }), 200
        
    except Exception as e:
        return jsonify({'message': 'Failed to get job workflow', 'error': str(e)}), 500

@app.route('/api/jobs/<job_id>/workflow/<stage_name>', methods=['PUT', 'OPTIONS'])
@token_required
def update_workflow_stage(current_user, job_id, stage_name):
    """Update a specific workflow stage"""
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    try:
        if jobs_collection is None:
            return jsonify({'message': 'Database not available (demo mode)'}), 200
            
        data = request.get_json()
        new_status = data.get('status')
        notes = data.get('notes', '')
        assigned_tailor = data.get('assigned_tailor')
        
        if not new_status:
            return jsonify({'message': 'Status is required'}), 400
            
        valid_statuses = ['pending', 'in_progress', 'completed', 'on_hold']
        if new_status not in valid_statuses:
            return jsonify({'message': 'Invalid status'}), 400
            
        job = jobs_collection.find_one({'_id': ObjectId(job_id)})
        if job is None:
            return jsonify({'message': 'Job not found'}), 404
            
        # Initialize workflow_stages if not present
        if 'workflow_stages' not in job:
            job['workflow_stages'] = [
                {'name': 'cutting', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': datetime.now().isoformat()},
                {'name': 'stitching', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': datetime.now().isoformat()},
                {'name': 'finishing', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': datetime.now().isoformat()},
                {'name': 'packaging', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': datetime.now().isoformat()}
            ]
            
        # Update the specific stage
        workflow_stages = job['workflow_stages']
        stage_found = False
        current_time = datetime.now().isoformat()
        
        for stage in workflow_stages:
            if stage['name'] == stage_name:
                stage_found = True
                old_status = stage['status']
                stage['status'] = new_status
                stage['notes'] = notes
                stage['updated_at'] = current_time
                
                if assigned_tailor:
                    stage['assigned_tailor'] = assigned_tailor
                    
                # Set started_at when moving from pending to in_progress
                if old_status == 'pending' and new_status == 'in_progress':
                    stage['started_at'] = current_time
                    
                # Set completed_at when moving to completed
                if new_status == 'completed':
                    stage['completed_at'] = current_time
                    
                break
                
        if not stage_found:
            return jsonify({'message': 'Invalid stage name'}), 400
            
        # Calculate progress and current stage
        stage_names = ['cutting', 'stitching', 'finishing', 'packaging']
        completed_stages = sum(1 for stage in workflow_stages if stage['status'] == 'completed')
        progress_percentage = (completed_stages / len(stage_names)) * 100
        
        # Determine current stage
        current_stage = 'cutting'
        for i, stage in enumerate(workflow_stages):
            if stage['status'] in ['in_progress', 'on_hold']:
                current_stage = stage['name']
                break
            elif stage['status'] == 'completed' and i < len(stage_names) - 1:
                current_stage = stage_names[i + 1]
                
        # Update overall job status
        if progress_percentage == 100:
            overall_status = 'completed'
        elif progress_percentage > 0:
            overall_status = 'in_progress'
        else:
            overall_status = 'assigned'
            
        # Update the job in database
        jobs_collection.update_one(
            {'_id': ObjectId(job_id)},
            {
                '$set': {
                    'workflow_stages': workflow_stages,
                    'current_stage': current_stage,
                    'progress_percentage': progress_percentage,
                    'status': overall_status,
                    'updated_at': datetime.now()
                }
            }
        )
        
        return jsonify({
            'message': 'Workflow stage updated successfully',
            'stage': stage_name,
            'new_status': new_status,
            'current_stage': current_stage,
            'progress_percentage': progress_percentage
        }), 200
        
    except Exception as e:
        return jsonify({'message': 'Failed to update workflow stage', 'error': str(e)}), 500

@app.route('/api/workflow/dashboard', methods=['GET', 'OPTIONS'])
@token_required
def get_workflow_dashboard(current_user):
    """Get workflow dashboard data for admin"""
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    try:
        if jobs_collection is None:
            return jsonify({
                'stage_stats': {'cutting': 0, 'stitching': 0, 'finishing': 0, 'packaging': 0},
                'recent_updates': [],
                'overdue_jobs': [],
                'total_active_jobs': 0
            }), 200
            
        # Get all active jobs with workflow stages
        active_jobs = list(jobs_collection.find(
            {'status': {'$in': ['assigned', 'in_progress']}},
            {'title': 1, 'current_stage': 1, 'workflow_stages': 1, 'due_date': 1, 'created_at': 1, 'updated_at': 1, 'priority': 1}
        ))
        
        # Calculate stage statistics
        stage_stats = {'cutting': 0, 'stitching': 0, 'finishing': 0, 'packaging': 0}
        recent_updates = []
        overdue_jobs = []
        
        current_time = datetime.now()
        
        for job in active_jobs:
            job['_id'] = str(job['_id'])
            
            # Count jobs by current stage
            current_stage = job.get('current_stage', 'cutting')
            if current_stage in stage_stats:
                stage_stats[current_stage] += 1
                
            # Check for overdue jobs
            if job.get('due_date'):
                due_date = datetime.fromisoformat(job['due_date']) if isinstance(job['due_date'], str) else job['due_date']
                if due_date < current_time:
                    overdue_jobs.append({
                        'id': job['_id'],
                        'title': job['title'],
                        'due_date': due_date.isoformat() if due_date else None,
                        'current_stage': current_stage,
                        'priority': job.get('priority', 'medium')
                    })
                    
            # Recent updates (jobs updated in last 24 hours)
            updated_at = job.get('updated_at', job.get('created_at'))
            if updated_at:
                if isinstance(updated_at, str):
                    updated_at = datetime.fromisoformat(updated_at)
                    
                if (current_time - updated_at).days == 0:  # Updated today
                    recent_updates.append({
                        'id': job['_id'],
                        'title': job['title'],
                        'current_stage': current_stage,
                        'updated_at': updated_at.isoformat()
                    })
                    
        # Sort recent updates by most recent first
        recent_updates.sort(key=lambda x: x['updated_at'], reverse=True)
        recent_updates = recent_updates[:10]  # Limit to 10 most recent
        
        return jsonify({
            'stage_stats': stage_stats,
            'recent_updates': recent_updates,
            'overdue_jobs': overdue_jobs,
            'total_active_jobs': len(active_jobs)
        }), 200
        
    except Exception as e:
        return jsonify({'message': 'Failed to get workflow dashboard', 'error': str(e)}), 500

# Backfill jobs for existing bills (admin only)
@app.route('/api/workflow/backfill', methods=['POST', 'OPTIONS'])
@token_required
def backfill_jobs(current_user):
    if request.method == 'OPTIONS':
        return jsonify(), 200

    try:
        if current_user.get('role') != 'admin':
            return jsonify({'message': 'Access denied'}), 403
        
        if bills_collection is None or jobs_collection is None:
            return jsonify({'message': 'Database not available'}), 500
        
        data = request.get_json(silent=True) or {}
        dry_run = bool(data.get('dry_run', False))
        limit = int(data.get('limit', 1000))
        created = 0
        skipped = 0
        scanned = 0

        # Iterate bills in reverse chronological order for practicality
        cursor = bills_collection.find({}, {'customer_name': 1, 'due_date': 1, 'created_at': 1, 'bill_no': 1}).sort('created_at', -1).limit(limit)
        current_time_iso = datetime.now().isoformat()
        
        for bill in cursor:
            scanned += 1
            bill_id = bill['_id']
            # Check if a job already exists for this bill
            exists = jobs_collection.find_one({'bill_id': bill_id}, {'_id': 1})
            if exists is not None:
                skipped += 1
                continue
            
            if dry_run:
                created += 1
                continue
            
            # Create a job for this bill with default workflow
            default_stages = [
                {'name': 'cutting', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': current_time_iso},
                {'name': 'stitching', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': current_time_iso},
                {'name': 'finishing', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': current_time_iso},
                {'name': 'packaging', 'status': 'pending', 'started_at': None, 'completed_at': None, 'assigned_tailor': None, 'notes': None, 'updated_at': current_time_iso},
            ]
            title_no = bill.get('bill_no')
            title = f"Order {format_bill_no(title_no, 3) if title_no else str(bill_id)[-6:]} - {bill.get('customer_name', 'Customer')}"
            auto_job = {
                'title': title,
                'description': f"Backfilled job for bill {format_bill_no(title_no, 3) if title_no else str(bill_id)}",
                'tailor_id': None,
                'bill_id': bill_id,
                'status': 'assigned',
                'priority': 'medium',
                'due_date': datetime.fromisoformat(bill['due_date']) if isinstance(bill.get('due_date'), str) and bill.get('due_date') else bill.get('due_date'),
                'created_by': str(current_user['_id']),
                'created_at': datetime.now(),
                'updated_at': datetime.now(),
                'workflow_stages': default_stages,
                'current_stage': 'cutting',
                'progress_percentage': 0,
            }
            jobs_collection.insert_one(auto_job)
            created += 1
        
        return jsonify({
            'message': 'Backfill completed',
            'dry_run': dry_run,
            'limit': limit,
            'created': created,
            'skipped_existing': skipped,
            'scanned': scanned
        }), 200
    except Exception as e:
        return jsonify({'message': 'Failed to backfill jobs', 'error': str(e)}), 500

# Dashboard Statistics Route - Optimized with caching
@app.route('/api/dashboard/stats', methods=['GET', 'OPTIONS'])
@token_required
def get_dashboard_stats(current_user):
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    try:
        cache_key = "dashboard_stats"
        cached_stats = cache.get(cache_key)
        
        if cached_stats:
            return jsonify(cached_stats), 200
        
        # For development when DB is not available
        if (customers_collection is None or bills_collection is None or 
            tailors_collection is None or jobs_collection is None):
            stats = {
                'total_customers': 0,
                'total_bills': 0,
                'total_tailors': 0,
                'total_jobs': 0,
                'pending_jobs': 0,
                'today_bills': 0,
                'total_revenue': 0
            }
            cache[cache_key] = stats
            return jsonify(stats), 200
            
        # Use parallel execution for better performance
        def get_count(collection, query=None):
            try:
                return collection.count_documents(query if query else {})
            except:
                return 0
        
        with ThreadPoolExecutor() as executor:
            # Submit all count operations in parallel
            future_total_customers = executor.submit(get_count, customers_collection)
            future_total_bills = executor.submit(get_count, bills_collection)
            future_total_tailors = executor.submit(get_count, tailors_collection)
            future_total_jobs = executor.submit(get_count, jobs_collection)
            future_pending_jobs = executor.submit(get_count, jobs_collection, {'status': {'$in': ['assigned', 'in_progress']}})
            
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            tomorrow = today + timedelta(days=1)
            future_today_bills = executor.submit(get_count, bills_collection, {
                'created_at': {'$gte': today, '$lt': tomorrow}
            })
            
            # Get revenue in separate thread
            def get_revenue():
                try:
                    pipeline = [{'$group': {'_id': None, 'total_revenue': {'$sum': '$total'}}}]
                    revenue_result = list(bills_collection.aggregate(pipeline))
                    return revenue_result[0]['total_revenue'] if revenue_result else 0
                except:
                    return 0
            
            future_total_revenue = executor.submit(get_revenue)
            
            # Wait for all results
            stats = {
                'total_customers': future_total_customers.result(),
                'total_bills': future_total_bills.result(),
                'total_tailors': future_total_tailors.result(),
                'total_jobs': future_total_jobs.result(),
                'pending_jobs': future_pending_jobs.result(),
                'today_bills': future_today_bills.result(),
                'total_revenue': future_total_revenue.result()
            }
        
        cache[cache_key] = stats
        return jsonify(stats), 200
        
    except Exception as e:
        return jsonify({'message': 'Failed to get dashboard stats', 'error': str(e)}), 500

# Reports API
@app.route('/api/reports/revenue', methods=['GET', 'OPTIONS'])
@token_required
def report_revenue(current_user):
    if request.method == 'OPTIONS':
        return jsonify(), 200
    try:
        if bills_collection is None:
            return jsonify({'revenue_data': []}), 200
        # Parse date range
        from_date_str = request.args.get('from_date')
        to_date_str = request.args.get('to_date')
        try:
            start = datetime.fromisoformat(from_date_str) if from_date_str else datetime.now() - timedelta(days=30)
            end = datetime.fromisoformat(to_date_str) + timedelta(days=1) if to_date_str else datetime.now() + timedelta(days=1)
        except Exception:
            start = datetime.now() - timedelta(days=30)
            end = datetime.now() + timedelta(days=1)
        pipeline = [
            { '$match': { 'created_at': { '$gte': start, '$lt': end } } },
            { '$group': { '_id': { '$dateToString': { 'format': '%Y-%m-%d', 'date': '$created_at' } }, 'amount': { '$sum': '$total' }, 'bills_count': { '$sum': 1 } } },
            { '$sort': { '_id': 1 } }
        ]
        data = list(bills_collection.aggregate(pipeline))
        revenue_data = [{ 'date': d['_id'], 'amount': float(d.get('amount', 0)), 'bills_count': int(d.get('bills_count', 0)) } for d in data]
        return jsonify({'revenue_data': revenue_data}), 200
    except Exception as e:
        return jsonify({'message': 'Failed to get revenue report', 'error': str(e)}), 500

@app.route('/api/reports/customers', methods=['GET', 'OPTIONS'])
@token_required
def report_customers(current_user):
    if request.method == 'OPTIONS':
        return jsonify(), 200
    try:
        if customers_collection is None:
            return jsonify({'customer_reports': []}), 200
        # Aggregate totals per customer
        pipeline = [
            { '$lookup': { 'from': 'bills', 'localField': '_id', 'foreignField': 'customer_id', 'as': 'bills' } },
            { '$project': {
                'name': 1, 'phone': 1,
                'total_orders': { '$size': {'$ifNull': [ '$bills', [] ] } },
                'total_spent': { '$sum': { '$map': { 'input': { '$ifNull': [ '$bills', [] ] }, 'as': 'b', 'in': { '$ifNull': ['$$b.total', 0] } } } },
                'outstanding_amount': { '$sum': { '$map': { 'input': { '$filter': { 'input': { '$ifNull': [ '$bills', [] ] }, 'as': 'b', 'cond': { '$and': [ { '$eq': ['$$b.status', 'pending'] }, { '$gt': ['$$b.balance', 0] } ] } } }, 'as': 'p', 'in': { '$ifNull': ['$$p.balance', 0] } } } },
                'last_order_date': { '$max': { '$map': { 'input': { '$ifNull': [ '$bills', [] ] }, 'as': 'b', 'in': '$$b.created_at' } } }
            }},
            { '$sort': { 'total_spent': -1 } },
            { '$limit': 100 }
        ]
        data = list(customers_collection.aggregate(pipeline))
        customer_reports = []
        for c in data:
            last_date = c.get('last_order_date')
            if isinstance(last_date, datetime):
                last_date = last_date.isoformat()
            customer_reports.append({
                'customer_id': str(c.get('_id')),
                'name': c.get('name', ''),
                'phone': c.get('phone', ''),
                'total_orders': int(c.get('total_orders', 0)),
                'total_spent': float(c.get('total_spent', 0)),
                'outstanding_amount': float(c.get('outstanding_amount', 0)),
                'last_order_date': last_date or None,
            })
        return jsonify({'customer_reports': customer_reports}), 200
    except Exception as e:
        return jsonify({'message': 'Failed to get customer reports', 'error': str(e)}), 500

@app.route('/api/reports/tailors', methods=['GET', 'OPTIONS'])
@token_required
def report_tailors(current_user):
    if request.method == 'OPTIONS':
        return jsonify(), 200
    try:
        if tailors_collection is None or jobs_collection is None:
            return jsonify({'tailor_reports': []}), 200
        pipeline = [
            { '$lookup': { 'from': 'jobs', 'localField': '_id', 'foreignField': 'tailor_id', 'as': 'jobs' } },
            { '$project': {
                'name': 1, 'phone': 1,
                'total_jobs': { '$size': { '$ifNull': [ '$jobs', [] ] } },
                'completed_jobs': { '$size': { '$filter': { 'input': { '$ifNull': [ '$jobs', [] ] }, 'as': 'j', 'cond': { '$eq': ['$$j.status', 'completed'] } } } },
                'pending_jobs': { '$size': { '$filter': { 'input': { '$ifNull': [ '$jobs', [] ] }, 'as': 'j', 'cond': { '$in': ['$$j.status', ['assigned','in_progress']] } } } },
                'avg_completion_time': { '$avg': { '$map': { 'input': { '$filter': { 'input': { '$ifNull': [ '$jobs', [] ] }, 'as': 'j', 'cond': { '$eq': ['$$j.status', 'completed'] } } }, 'as': 'j', 'in': { '$subtract': [ {'$toDate': '$$j.updated_at'}, {'$toDate': '$$j.created_at'} ] } } } }
            } },
            { '$addFields': { 'completion_rate': { '$cond': [ { '$gt': ['$total_jobs', 0] }, { '$multiply': [ { '$divide': ['$completed_jobs', '$total_jobs'] }, 100 ] }, 0 ] } } },
            { '$sort': { 'completion_rate': -1 } },
            { '$limit': 100 }
        ]
        data = list(tailors_collection.aggregate(pipeline))
        reports = []
        for t in data:
            reports.append({
                'tailor_id': str(t.get('_id')),
                'name': t.get('name',''),
                'phone': t.get('phone',''),
                'total_jobs': int(t.get('total_jobs',0)),
                'completed_jobs': int(t.get('completed_jobs',0)),
                'pending_jobs': int(t.get('pending_jobs',0)),
                'completion_rate': float(t.get('completion_rate',0)),
                'avg_completion_time': float(t.get('avg_completion_time',0) or 0)
            })
        return jsonify({'tailor_reports': reports}), 200
    except Exception as e:
        return jsonify({'message': 'Failed to get tailor reports', 'error': str(e)}), 500

@app.route('/api/reports/outstanding', methods=['GET', 'OPTIONS'])
@token_required
def report_outstanding(current_user):
    if request.method == 'OPTIONS':
        return jsonify(), 200
    try:
        if bills_collection is None:
            return jsonify({'outstanding_reports': []}), 200
        # Match pending bills with positive balance
        pipeline = [
            { '$match': { 'status': 'pending', 'balance': { '$gt': 0 } } },
            { '$lookup': { 'from': 'customers', 'localField': 'customer_id', 'foreignField': '_id', 'as': 'customer' } },
            { '$unwind': { 'path': '$customer', 'preserveNullAndEmptyArrays': True } },
            { '$project': {
                'customer_id': '$customer._id',
                'customer_name': '$customer.name',
                'phone': '$customer.phone',
                'outstanding_amount': '$balance',
                'due_date': '$due_date'
            } },
            { '$limit': 200 }
        ]
        data = list(bills_collection.aggregate(pipeline))
        reports = []
        now = datetime.now()
        for r in data:
            # Compute overdue days if due_date parseable
            overdue_days = 0
            due = r.get('due_date')
            try:
                if isinstance(due, str) and due:
                    due_dt = datetime.fromisoformat(due)
                    overdue_days = max(0, (now - due_dt).days)
            except Exception:
                overdue_days = 0
            reports.append({
                'customer_id': str(r.get('customer_id')) if r.get('customer_id') else None,
                'customer_name': r.get('customer_name') or 'Unknown',
                'phone': r.get('phone') or '',
                'outstanding_amount': float(r.get('outstanding_amount') or 0),
                'overdue_days': int(overdue_days),
                'last_payment_date': None
            })
        return jsonify({'outstanding_reports': reports}), 200
    except Exception as e:
        return jsonify({'message': 'Failed to get outstanding reports', 'error': str(e)}), 500

# Export API endpoints
@app.route('/api/reports/export/<export_type>/<format_type>', methods=['GET', 'OPTIONS'])
@token_required
def export_reports(current_user, export_type, format_type):
    if request.method == 'OPTIONS':
        return jsonify(), 200
    try:
        # This endpoint is kept for API compatibility but 
        # actual export is now handled client-side for better performance
        # and to avoid large data transfers
        return jsonify({
            'message': 'Export functionality is now handled client-side',
            'export_type': export_type,
            'format': format_type
        }), 200
    except Exception as e:
        return jsonify({'message': 'Export failed', 'error': str(e)}), 500

# Health check route
@app.route('/api/health', methods=['GET', 'OPTIONS'])
def health_check():
    if request.method == 'OPTIONS':
        return jsonify(), 200
        
    db_status = "connected" if client is not None and client.admin.command('ping') else "disconnected"
    return jsonify({
        'status': 'healthy', 
        'message': 'Star Tailors API is running',
        'database': db_status
    }), 200

# Add performance monitoring middleware
@app.after_request
def after_request(response):
    # Add CORS headers
    origin = request.headers.get('Origin')
    if origin in ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With, Accept'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS, PATCH'
    
    # Add performance headers
    if hasattr(request, 'start_time'):
        response.headers['X-Response-Time'] = f"{time.time() - request.start_time:.3f}s"
    return response

# Combined into the handle_preflight_requests function above

# Initialize default user in background
def init_default_user_async():
    time.sleep(2)  # Wait for app to start
    init_default_user()

# Start initialization in background
init_thread = threading.Thread(target=init_default_user_async)
init_thread.daemon = True
init_thread.start()

if __name__ == '__main__':
    port = int(os.getenv('PORT', '5000'))
    debug_mode = os.getenv('DEBUG', 'True').lower() == 'true'
    print(f"🚀 Starting Flask server on port {port}, debug={debug_mode}")
    print(f"🔧 CORS configured for origins: {ALLOWED_ORIGINS}")
    
    # Run with debug mode for local development
    app.run(debug=debug_mode, host='0.0.0.0', port=port, threaded=True)
