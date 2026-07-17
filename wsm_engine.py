import pandas as pd
import numpy as np

class WSM_Engine:
    def __init__(self, property_data, client_weights, target_lat=0.0, target_lng=0.0):
        """
        Initializes the engine with raw property data, client preference weights,
        and the target Point of Interest (POI) coordinates.
        """
        self.df = pd.DataFrame(property_data)
        self.df['price'] = self.df['price'].astype(float)
        
        self.df['latitude'] = self.df.get('latitude', 0.0).astype(float)
        self.df['longitude'] = self.df.get('longitude', 0.0).astype(float)
        
        self.weights = client_weights
        self.target_lat = float(target_lat)
        self.target_lng = float(target_lng)

    def calculate_distance(self):
        """
        Vectorized Haversine formula to calculate the distance (in km).
        """
        R = 6371.0

        lat1 = np.radians(self.df['latitude'])
        lon1 = np.radians(self.df['longitude'])
        lat2 = np.radians(self.target_lat)
        lon2 = np.radians(self.target_lng)

        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = np.sin(dlat/2)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon/2)**2
        c = 2 * np.arcsin(np.sqrt(a))
        
        self.df['distance_km'] = R * c

    def normalize_data(self):
        """
        Scales attributes to a 0-1 range.
        Cost criteria (Price, Distance) = Lower is better.
        Benefit criteria (Size, Amenities) = Higher is better.
        """
        self.calculate_distance()

        # 1. Price
        max_price = self.df['price'].max()
        min_price = self.df['price'].min()
        if max_price != min_price:
            self.df['norm_price'] = (max_price - self.df['price']) / (max_price - min_price)
        else:
            self.df['norm_price'] = 1.0

        # 2. Size
        max_size = self.df['size_sqft'].max()
        min_size = self.df['size_sqft'].min()
        if max_size != min_size:
            self.df['norm_size'] = (self.df['size_sqft'] - min_size) / (max_size - min_size)
        else:
            self.df['norm_size'] = 1.0

        # 3. Amenities
        max_amenity = self.df['amenity_score'].max()
        min_amenity = self.df['amenity_score'].min()
        if max_amenity != min_amenity:
            self.df['norm_amenities'] = (self.df['amenity_score'] - min_amenity) / (max_amenity - min_amenity)
        else:
            self.df['norm_amenities'] = 1.0
            
        # 4. Location/Distance
        max_dist = self.df['distance_km'].max()
        min_dist = self.df['distance_km'].min()
        if max_dist != min_dist:
            self.df['norm_location'] = (max_dist - self.df['distance_km']) / (max_dist - min_dist)
        else:
            self.df['norm_location'] = 1.0

    def calculate_scores(self):
        """
        Multiplies the normalized attributes by the normalized client weights.
        """
        self.normalize_data()

        # --- THE FIX: DYNAMIC WEIGHT NORMALIZATION ---
        
        # 1. Sum up all raw weights provided by the sliders
        total_weight = (
            self.weights.get('w_price', 0) +
            self.weights.get('w_size', 0) +
            self.weights.get('w_amenities', 0) +
            self.weights.get('w_location', 0)
        )
        
        # Prevent division by zero if all sliders happen to be dragged to 0
        if total_weight == 0:
            total_weight = 1.0

        # 2. Divide each raw weight by the total so they sum to exactly 1.0
        w_p = self.weights.get('w_price', 0) / total_weight
        w_s = self.weights.get('w_size', 0) / total_weight
        w_a = self.weights.get('w_amenities', 0) / total_weight
        w_l = self.weights.get('w_location', 0) / total_weight

        # 3. Calculate final Suitability Score using the normalized weights
        self.df['suitability_score'] = (
            (self.df['norm_price'] * w_p) +
            (self.df['norm_size'] * w_s) +
            (self.df['norm_amenities'] * w_a) +
            (self.df['norm_location'] * w_l)
        )

        # Convert score to a readable percentage (Max will now safely cap at 100%)
        self.df['match_percentage'] = (self.df['suitability_score'] * 100).round(1)

    def get_top_matches(self, top_n=10):
        """
        Sorts the DataFrame by score and returns the top results.
        """
        self.calculate_scores()
        
        ranked_df = self.df.sort_values(by='match_percentage', ascending=False)
        ranked_df['distance_km'] = ranked_df['distance_km'].round(1)
        
        top_matches = ranked_df.head(top_n)
        top_matches = top_matches.replace({np.nan: None})
        
        return top_matches.to_dict(orient='records')