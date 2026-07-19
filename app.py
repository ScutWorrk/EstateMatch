import os
from dotenv import load_dotenv
from google import genai
import os
from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from wsm_engine import WSM_Engine
import mysql.connector
from mysql.connector import Error
import csv
import io
import requests
import time
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash
from flask import request, jsonify
import pymysql
from flask import redirect, url_for

# Load the environment variables from the .env file
load_dotenv()

db_password = os.environ.get('DB_PASSWORD')

if not db_password:
    print("CRITICAL ERROR: DB_PASSWORD not found in environment variables!")
    exit(1)
 
connection = pymysql.connect(
    host="mysql-22348d68-estatematch.j.aivencloud.com",
    port=11130,
    user="avnadmin",
    password=db_password,
    database="defaultdb",
    ssl={'ssl': True} # PyMySQL natively handles this if passed as a truthy dict
)

app = Flask(__name__)
app.secret_key = 'super_secret_estate_match_key_2026' 
   
# Configure Gemini with the hidden key
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

def generate_property_insight(property_name, wsm_score, budget, amenities):
    prompt = f"""
    You are an AI assistant for a real estate decision support system. 
    Analyze the following property match and provide a short, professional 2-3 sentence insight 
    for the real estate agent to share with their client.

    Data:
    - Property: {property_name}
    - Match Score (WSM): {wsm_score}%
    - Client Budget: RM {budget}
    - Key Amenities: {amenities}
    
    Focus on why this is a good match based on the score and budget.
    """
    
    try:
        # The new SDK uses client.models.generate_content
        response = client.models.generate_content(
            model='gemini-1.5-flash', 
            contents=prompt
        )
        return response.text
    except Exception as e:
        print(f"AI Generation Error: {e}")
        return "Insight currently unavailable."
    
db_config = {
    'host': 'mysql-22348d68-estatematch.j.aivencloud.com',
    'port': 11130,
    'user': 'avnadmin',
    'password': db_password, # Safely uses the os.environ.get('DB_PASSWORD') you defined at the top
    'database': 'defaultdb'
}

def get_db_connection():
    try:
        conn = mysql.connector.connect(**db_config)
        return conn
    except Error as e:
        print(f"Error connecting to MySQL: {e}")
        return None

# --- NEW: GEOSPATIAL API INTEGRATION ---
def get_coordinates(location_name):
    """
    Uses OpenStreetMap API to convert text locations (like 'Cyberjaya') 
    into exact GPS Latitude and Longitude coordinates.
    """
    default_lat, default_lng = 3.1390, 101.6869 # Default to KL Center if not found
    if not location_name or location_name.lower() == 'any':
        return default_lat, default_lng
    
    try:
        # Append 'Malaysia' to narrow down the search accuracy
        query = f"{location_name}, Malaysia"
        url = f"https://nominatim.openstreetmap.org/search?q={query}&format=json&limit=1"
        headers = {'User-Agent': 'EstateMatchFYP/1.0'} # Required by OpenStreetMap API
        
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            if len(data) > 0:
                return float(data[0]['lat']), float(data[0]['lon'])
    except Exception as e:
        print(f"Geocoding Error: {e}")
        
    return default_lat, default_lng

# --- API ROUTES ---
@app.route('/')
def home():
    # This assumes your login function is named 'login'
    return redirect(url_for('login'))

@app.route('/dashboard', methods=['GET'])
def dashboard():
    if 'user_id' not in session:
        return redirect('/login')
    return render_template('dashboard.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        conn = get_db_connection()
        if conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
            user = cursor.fetchone()
            cursor.close()
            conn.close()

            if user and check_password_hash(user['password_hash'], password):
                session['user_id'] = user['user_id']
                session['username'] = user['username']
                session['role'] = user['role']

                if user['role'] == 'Admin':
                    return redirect('/admin')
                else:
                    return redirect('/dashboard')
            else:
                error = "Invalid username or password."

    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/clients', methods=['GET'])
def clients_page():
    return render_template('clients.html')

@app.route('/api/inventory', methods=['GET'])
def get_inventory():
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM properties WHERE status = 'Active'")
        properties = cursor.fetchall()
        
        return jsonify({
            "status": "success",
            "count": len(properties),
            "data": properties
        })
    except Error as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/api/recommend', methods=['POST'])
def get_recommendation():
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        data = request.get_json()
        weights_data = data.get('weights', {})
        filters_data = data.get('filters', {})

        # 1. Extract WSM Weights
        client_weights = {
            'w_price': float(weights_data.get('w_price', 0.5)),
            'w_size': float(weights_data.get('w_size', 0.3)),
            'w_amenities': float(weights_data.get('w_amenities', 0.2)),
            'w_location': float(weights_data.get('w_location', 0.5))
        }

        # 2. Extract Strict Filters
        max_budget = float(filters_data.get('max_budget', 999999999))
        min_bedrooms = int(filters_data.get('min_bedrooms', 1))
        
        # Extract Maximum Floors (Default to 99 so it ignores the rule if left blank)
        max_floors = int(filters_data.get('max_floors', 99)) 

        # Extract the preferred type ID from frontend dropdown
        preferred_type_id = filters_data.get('preferred_type_id')

        # Grab direct GPS coordinates from the frontend payload mapping
        target_lat = float(filters_data.get('target_lat', 3.1390))
        target_lng = float(filters_data.get('target_lng', 101.6869))

        cursor = conn.cursor(dictionary=True)
        
        # Base query incorporating structural constraints and the Property_Types lookup table
        sql_query = """
            SELECT p.*, pt.display_name AS property_type_name, pt.floors
            FROM properties p
            LEFT JOIN property_types pt ON p.type_id = pt.type_id
            WHERE p.status = 'Active' 
              AND p.price <= %s 
              AND p.bedrooms >= %s
              AND (pt.floors <= %s OR pt.floors IS NULL)
        """
        
        # Initialize query parameter tracking array
        params = [max_budget, min_bedrooms, max_floors]

        # SECURED: Dynamically evaluate and append the structural property type filter
        if preferred_type_id and str(preferred_type_id).strip() != '':
            sql_query += " AND p.type_id = %s"
            params.append(int(preferred_type_id))

        # Execute query passing elements cast as a matching parameterized tuple
        cursor.execute(sql_query, tuple(params))
        properties = cursor.fetchall()

        if not properties:
            return jsonify({
                "status": "error", 
                "message": "No active properties match these strict criteria."
            })

        # Pass coordinates and the dynamically pruned dataset into your core analytical WSM engine
        engine = WSM_Engine(properties, client_weights, target_lat=target_lat, target_lng=target_lng)
        top_matches = engine.get_top_matches(top_n=10)

        return jsonify({
            "status": "success",
            "ranked_matches": top_matches
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/api/clients', methods=['GET', 'POST'])
def manage_clients():
    """Handles saving and retrieving client profiles with precise GPS coordinates."""
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)

        # Ensure Agent ID 1 exists to satisfy Foreign Key constraints
        cursor.execute("SELECT user_id FROM users WHERE user_id = 1")
        if not cursor.fetchone():
            cursor.execute("INSERT INTO users (user_id, username, password_hash, role) VALUES (1, 'agent_sarah', 'hashed123', 'Agent')")
            conn.commit()

        # Handle GET request: Return list of saved clients including coordinates
        if request.method == 'GET':
            cursor.execute("SELECT * FROM client_profiles WHERE agent_id = 1 ORDER BY client_id DESC")
            clients = cursor.fetchall()
            return jsonify({"status": "success", "data": clients})

        # Handle POST request: Save a new client profile with direct coordinate injection
        if request.method == 'POST':
            data = request.get_json()
            
            # ADDED preferred_type_id to the columns and an extra %s to VALUES
            sql = """
                INSERT INTO client_profiles 
                (agent_id, client_name, max_budget, preferred_zone, preferred_type_id, min_bedrooms, 
                 w_price, w_location, w_size, w_amenities, target_lat, target_lng)
                VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            
            # Grab the direct coordinates sent by the map UI layout (default to KL Center if missing)
            lat = float(data.get('target_lat', 3.1390))
            lng = float(data.get('target_lng', 101.6869))
            
            # Safely handle the preferred type if left blank
            pref_type = data.get('preferred_type_id')
            if pref_type == '':
                pref_type = None

            # ADDED pref_type into the values tuple right after preferred_zone
            values = (
                data['client_name'], float(data['max_budget']), data.get('preferred_zone', 'Any'),
                pref_type, int(data['min_bedrooms']), float(data['w_price']), float(data.get('w_location', 0.5)), 
                float(data['w_size']), float(data['w_amenities']), lat, lng
            )
            
            cursor.execute(sql, values)
            conn.commit()
            return jsonify({"status": "success", "message": "Client Profile Saved with direct GPS coordinates!"})

    except Error as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/api/clients/<int:client_id>', methods=['PUT', 'DELETE'])
def modify_client(client_id):
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        if request.method == 'DELETE':
            cursor.execute("DELETE FROM client_profiles WHERE client_id = %s", (client_id,))
            conn.commit()
            return jsonify({"status": "success", "message": "Client deleted successfully!"})

        if request.method == 'PUT':
            data = request.get_json()
            # Safely handle the preferred type if left blank ("Any")
            pref_type = data.get('preferred_type_id')
            if pref_type == '':
                pref_type = None
                
            sql = """
                UPDATE client_profiles 
                SET client_name = %s, max_budget = %s, preferred_zone = %s, 
                    preferred_type_id = %s, min_bedrooms = %s, w_price = %s, 
                    w_size = %s, w_amenities = %s, w_location = %s,
                    target_lat = %s, target_lng = %s
                WHERE client_id = %s AND agent_id = 1
            """

            values = (
                data['client_name'], float(data['max_budget']), data.get('preferred_zone', 'Any'),
                pref_type, int(data['min_bedrooms']), float(data['w_price']), 
                float(data['w_size']), float(data['w_amenities']), float(data.get('w_location', 0.5)),
                float(data.get('target_lat', 3.1390)), float(data.get('target_lng', 101.6869)),
                client_id
            )

            cursor.execute(sql, values)
            conn.commit()
            return jsonify({"status": "success", "message": "Client updated successfully!"})

    except Error as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/admin', methods=['GET'])
def admin_dashboard():
    return render_template('admin.html')

@app.route('/api/admin/metrics', methods=['GET'])
def get_admin_metrics():
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT COUNT(*) as total FROM properties")
        total_listings = cursor.fetchone()['total']

        cursor.execute("SELECT AVG(price) as avg_price FROM properties")
        avg_price_row = cursor.fetchone()
        avg_price = float(avg_price_row['avg_price']) if avg_price_row['avg_price'] else 0.0

        if avg_price == 0.0:
            market_stability = "No Data"
        elif avg_price < 400000:
            market_stability = "Highly Accessible"
        elif avg_price <= 800000:
            market_stability = "Normal / Stable"
        else:
            market_stability = "Premium / Elevated"

        cursor.execute("SELECT COUNT(*) as agent_count FROM users WHERE role = 'Agent'")
        active_agents = cursor.fetchone()['agent_count']

        return jsonify({
            "status": "success",
            "total_listings": total_listings,
            "avg_price": avg_price,
            "stability": market_stability,
            "active_agents": active_agents
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/api/admin/inventory', methods=['GET', 'POST'])
def manage_admin_inventory():
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        
        if request.method == 'GET':
            # Join with Property_Types so the admin panel sees the human-readable display_name
            sql = """
                SELECT p.*, pt.display_name AS property_type_name
                FROM properties p
                LEFT JOIN property_types pt ON p.type_id = pt.type_id
                ORDER BY p.property_id DESC
            """
            cursor.execute(sql)
            properties = cursor.fetchall()
            return jsonify({"status": "success", "data": properties})

        if request.method == 'POST':
            data = request.get_json()
            listing_name = data['listing_name'].strip()
            
            # --- NEW: DUPLICATE PREVENTION ---
            # Check if this exact listing name already exists (case-insensitive)
            cursor.execute("SELECT property_id FROM properties WHERE LOWER(listing_name) = LOWER(%s)", (listing_name,))
            if cursor.fetchone():
                return jsonify({
                    "status": "error", 
                    "error": f"'{listing_name}' already exists in the database!"
                })
            
            # SECURED: Auto-fetch GPS coordinates for the new property
            lat, lng = get_coordinates(listing_name)
            
            sql = """
                INSERT INTO properties 
                (listing_name, price, size_sqft, bedrooms, bathrooms, amenity_score, tenure, status, latitude, longitude) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            type_id = data.get('type_id')
            if type_id == '':
                type_id = None

            values = (
                listing_name, float(data['price']), int(data['size_sqft']),
                int(data['bedrooms']), int(data['bathrooms']), int(data['amenity_score']),
                data.get('tenure', 'Freehold'), data.get('status', 'Active'),
                lat, lng
            )
            
            cursor.execute(sql, values)
            conn.commit()
            return jsonify({"status": "success", "message": "Property added with GPS coordinates!"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()
            
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        
        if request.method == 'GET':
            cursor.execute("SELECT * FROM properties ORDER BY property_id DESC")
            properties = cursor.fetchall()
            return jsonify({"status": "success", "data": properties})

        if request.method == 'POST':
            data = request.get_json()
            
            # SECURED: Auto-fetch GPS coordinates for the new property
            lat, lng = get_coordinates(data['listing_name'])
            
            sql = """
                INSERT INTO properties 
                (listing_name, price, size_sqft, bedrooms, bathrooms, amenity_score, tenure, status, latitude, longitude) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            values = (
                data['listing_name'], float(data['price']), int(data['size_sqft']),
                int(data['bedrooms']), int(data['bathrooms']), int(data['amenity_score']),
                data.get('tenure', 'Freehold'), data.get('status', 'Active'),
                lat, lng
            )
            
            cursor.execute(sql, values)
            conn.commit()
            return jsonify({"status": "success", "message": "Property added with GPS coordinates!"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/api/admin/inventory/upload', methods=['POST'])
@app.route('/api/admin/inventory/upload', methods=['POST'])
def upload_inventory():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file uploaded."}), 400

    file = request.files['file']
    
    if file.filename == '':
        return jsonify({"status": "error", "message": "No file selected."}), 400

    if not file.filename.endswith('.csv'):
        return jsonify({"status": "error", "message": "Only .csv files are allowed."}), 400

    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.DictReader(stream)

        cursor = conn.cursor(dictionary=True)
        inserted_count = 0
        skipped_count = 0

        # --- NEW: DUPLICATE PREVENTION SYSTEM ---
        # Fetch all existing property names and store them in a lowercase set for blazing-fast lookups
        cursor.execute("SELECT listing_name FROM properties")
        existing_properties = {row['listing_name'].lower().strip() for row in cursor.fetchall()}

        sql = """
            INSERT INTO properties 
            (listing_name, price, size_sqft, bedrooms, bathrooms, amenity_score, tenure, status, latitude, longitude) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

        for row in csv_input:
            listing_name = row['listing_name'].strip()
            name_lower = listing_name.lower()
            
            # If the property is already in the database, skip this row completely!
            if name_lower in existing_properties:
                skipped_count += 1
                continue
                
            # Add it to our tracking set so we catch duplicates *inside* the CSV itself
            existing_properties.add(name_lower)

            # SECURED: Auto-geocode during bulk CSV upload
            lat, lng = get_coordinates(listing_name)
            time.sleep(0.5) 
            
            values = (
                listing_name, float(row['price']), int(row['size_sqft']),
                int(row['bedrooms']), int(row['bathrooms']), int(row['amenity_score']),
                row.get('tenure', 'Freehold'), row.get('status', 'Active'),
                lat, lng
            )
            cursor.execute(sql, values)
            inserted_count += 1

        conn.commit()
        
        # Prepare a detailed success message
        final_message = f"Successfully imported {inserted_count} properties!"
        if skipped_count > 0:
            final_message += f" ({skipped_count} duplicates were safely skipped)."
            
        return jsonify({"status": "success", "message": final_message})

    except Exception as e:
        return jsonify({"status": "error", "message": f"Error parsing CSV: {str(e)}"}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/api/admin/inventory/<int:property_id>', methods=['PUT', 'DELETE'])
def modify_inventory_item(property_id):
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        if request.method == 'DELETE':
            cursor.execute("DELETE FROM properties WHERE property_id = %s", (property_id,))
            conn.commit()
            return jsonify({"status": "success", "message": "Property deleted successfully!"})

        if request.method == 'PUT':
            data = request.get_json()
            sql = """
                UPDATE properties 
                SET listing_name = %s, price = %s, status = %s 
                WHERE property_id = %s
            """
            values = (data['listing_name'], data['price'], data['status'], property_id)
            cursor.execute(sql, values)
            conn.commit()
            return jsonify({"status": "success", "message": "Property updated successfully!"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/api/admin/users', methods=['GET', 'POST'])
def manage_admin_users():
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor(dictionary=True)
        
        if request.method == 'GET':
            cursor.execute("SELECT user_id, username, role FROM users ORDER BY user_id DESC")
            users = cursor.fetchall()
            return jsonify({"status": "success", "data": users})

        if request.method == 'POST':
            data = request.get_json()
            
            raw_password = data.get('password')
            
            if not raw_password:
                raw_password = 'password123'
                
            hashed_password = generate_password_hash(raw_password)
            
            sql = "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)"
            values = (data['username'], hashed_password, data['role'])
            cursor.execute(sql, values)
            conn.commit()
            return jsonify({"status": "success", "message": "User added securely!"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/api/admin/users/<int:user_id>', methods=['PUT', 'DELETE'])
def modify_admin_user(user_id):
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        if request.method == 'DELETE':
            cursor.execute("DELETE FROM users WHERE user_id = %s", (user_id,))
            conn.commit()
            return jsonify({"status": "success", "message": "User deleted successfully!"})

        if request.method == 'PUT':
            data = request.get_json()
            
            new_password = data.get('password')
            
            if new_password:
                hashed_password = generate_password_hash(new_password)
                sql = "UPDATE users SET username = %s, role = %s, password_hash = %s WHERE user_id = %s"
                values = (data['username'], data['role'], hashed_password, user_id)
            else:
                sql = "UPDATE users SET username = %s, role = %s WHERE user_id = %s"
                values = (data['username'], data['role'], user_id)
                
            cursor.execute(sql, values)
            conn.commit()
            return jsonify({"status": "success", "message": "User updated successfully!"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/api/admin/property_types', methods=['GET'])
def get_property_types():
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Database connection failed"}), 500
    try:
        cursor = conn.cursor(dictionary=True)
        
        # ADDED 'floors' to the SELECT statement
        cursor.execute("SELECT type_id, display_name, floors FROM property_types ORDER BY type_id ASC")
        types = cursor.fetchall()
        
        return jsonify({"status": "success", "data": types})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/api/profile/update', methods=['POST'])
def update_profile():
    user_id = session.get('user_id')
    username = request.form.get('username')
    ren_license = request.form.get('ren_license')
    agency_name = request.form.get('agency_name')
    password = request.form.get('password')
    
    # Handle Profile Picture
    file = request.files.get('profile_pic')
    filename = None
    if file and file.filename != '':
        filename = secure_filename(file.filename)
        file.save(os.path.join('static/uploads', filename))
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # If the user typed a new password, hash it before saving!
    if password:
        hashed_pw = generate_password_hash(password)
        cursor.execute("UPDATE users SET username=%s, ren_license=%s, agency_name=%s, profile_pic=%s, password_hash=%s WHERE user_id=%s", 
                       (username, ren_license, agency_name, filename, hashed_pw, user_id))
    else:
        cursor.execute("UPDATE users SET username=%s, ren_license=%s, agency_name=%s, profile_pic=%s WHERE user_id=%s", 
                       (username, ren_license, agency_name, filename, user_id))
    
    conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/profile', methods=['GET'])
def get_profile():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"status": "error", "message": "Not logged in"}), 401
        
    conn = get_db_connection()
    # Use dictionary=True so we can access columns by name (e.g., user['username'])
    cursor = conn.cursor(dictionary=True) 
    
    cursor.execute("SELECT username, ren_license, agency_name, profile_pic FROM users WHERE user_id = %s", (user_id,))
    user = cursor.fetchone()
    
    conn.close()
    
    if user:
        return jsonify({"status": "success", "data": user})
    return jsonify({"status": "error", "message": "User not found"}), 404

@app.route('/api/generate-insight', methods=['POST'])
def api_generate_insight():
    # Get the analysis data sent from the frontend
    data = request.json
    
    property_name = data.get('property_name')
    wsm_score = data.get('wsm_score')
    budget = data.get('budget')
    amenities = data.get('amenities')
    
    # Generate the insight
    insight_text = generate_property_insight(property_name, wsm_score, budget, amenities)
    
    return jsonify({
        "status": "success",
        "insight": insight_text
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000)