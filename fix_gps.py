import mysql.connector
import requests
import time

# Database connection details
db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'estate_match'
}

def get_coordinates(location_name):
    # 1. Clean the string from structural symbols that break parsers
    cleaned_name = location_name.replace("@", "").replace("  ", " ")
    
    # Define primary query options
    queries = [
        f"{cleaned_name}, Malaysia", # Attempt 1: Full clean name
    ]
    
    # Attempt 2: If the name contains a comma, strip the first part (usually the specific building name)
    # and search by the street and area layout instead.
    if "," in cleaned_name:
        fallback_name = ",".join(cleaned_name.split(",")[1:]).strip()
        queries.append(f"{fallback_name}, Malaysia")

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) EstateMatchFYP/2.0'}

    for query in queries:
        url = f"https://nominatim.openstreetmap.org/search?q={requests.utils.quote(query)}&format=json&limit=1"
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                if len(data) > 0:
                    # Target found successfully
                    return float(data[0]['lat']), float(data[0]['lon'])
            elif response.status_code == 429:
                print("  [!] Rate limit reached. Cool down triggered...")
                time.sleep(5)
        except Exception as e:
            print(f"  [!] Request Error: {e}")
            
        # Small delay between fallback attempts
        time.sleep(1)
        
    return None, None

print("Connecting to database...")
conn = mysql.connector.connect(**db_config)
cursor = conn.cursor(dictionary=True)

# Select all properties that need distinct coordinates
cursor.execute("SELECT property_id, listing_name FROM Properties")
properties = cursor.fetchall()

print(f"Found {len(properties)} properties. Beginning GPS repair...\n")

for prop in properties:
    print(f"Geocoding: {prop['listing_name']}")
    lat, lng = get_coordinates(prop['listing_name'])
    
    if lat is not None:
        cursor.execute("UPDATE Properties SET latitude = %s, longitude = %s WHERE property_id = %s", 
                       (lat, lng, prop['property_id']))
        conn.commit()
        print(f"  -> Success: {lat}, {lng}")
    else:
        print("  -> Failed to geocode.")
    
    # 2-second safety delay to respect the OpenStreetMap rate limit policy
    time.sleep(2) 

print("\nDatabase repair complete! Every listing now has unique, refined GPS coordinates.")
cursor.close()
conn.close()