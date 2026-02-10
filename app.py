import streamlit as st
import folium
from streamlit_folium import st_folium
from folium.plugins import MeasureControl
from geopy.geocoders import Nominatim
from shapely.geometry import shape, Point, box, mapping
from shapely.ops import nearest_points
import requests
import pandas as pd
import json
import math

# -----------------------------------------------------------------------------
# 1. CONFIGURATION & PAGE SETUP
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="OKC Well Proximity Portal",
    page_icon="🛢️",
    layout="wide"
)

# Custom CSS for a cleaner look
st.markdown("""
    <style>
    .stApp { background-color: #f0f2f6; }
    div[data-testid="stMetricValue"] { font-size: 24px; color: #004e8c; }
    </style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 2. UTILITY FUNCTIONS (GEOCODING & MATH)
# -----------------------------------------------------------------------------

@st.cache_data
def get_lat_long(address):
    """Geocodes address using OpenStreetMap (Nominatim)."""
    try:
        geolocator = Nominatim(user_agent="okc_well_explorer_v1")
        location = geolocator.geocode(address)
        if location:
            return location.latitude, location.longitude
        return None, None
    except Exception as e:
        st.error(f"Geocoding Error: {e}")
        return None, None

def create_fallback_boundary(lat, lon):
    """
    Creates a ~10 acre square box around the center point.
    10 acres is approx 40,468 sq meters.
    Sqrt(40468) ~= 201 meters per side.
    Approx 0.001 degrees latitude is ~111 meters.
    We'll do +/- 0.002 degrees for a decent sized visual box.
    """
    delta = 0.002
    minx, miny = lon - delta, lat - delta
    maxx, maxy = lon + delta, lat + delta
    return box(minx, miny, maxx, maxy)

def calculate_distance_feet(point_geom, poly_geom):
    """
    Calculates distance from a point to a polygon.
    Returns 0 if inside.
    Approximation: 1 Degree ~ 364,000 feet (Average for OK latitude).
    """
    if poly_geom.contains(point_geom):
        return 0.0
    
    # Shapely distance returns degrees here
    dist_degrees = poly_geom.distance(point_geom)
    
    # Rough conversion for Oklahoma Latitudes (approx 35.5 N)
    # This avoids heavy pyproj dependencies for this lightweight app
    feet_per_degree = 364000 
    return dist_degrees * feet_per_degree

# -----------------------------------------------------------------------------
# 3. ENVERUS DATA INTEGRATION
# -----------------------------------------------------------------------------

def get_enverus_token():
    """Retrieves Bearer token using secrets."""
    try:
        url = "https://api.enverus.com/v3/direct-access/tokens"
        payload = {
            "grantType": "client_credentials",
            "clientId": st.secrets["enverus"]["client_id"],
            "clientSecret": st.secrets["enverus"]["client_secret"],
            "scope": "limited"
        }
        headers = {"Content-Type": "application/json"}
        response = requests.post(url, json=payload, headers=headers)
        
        if response.status_code == 200:
            return response.json().get('token')
        else:
            st.error(f"Enverus Auth Failed: {response.text}")
            return None
    except Exception as e:
        # Graceful fallback if secrets aren't set up yet
        st.warning("Enverus credentials not found in st.secrets. Using Mock Data.")
        return "MOCK"

@st.cache_data(ttl=3600)
def fetch_wells_nearby(lat, lon, radius_miles=1.0):
    """
    Fetches wells from Enverus DirectAccess. 
    If credentials fail/missing, returns mock data for demonstration.
    """
    token = get_enverus_token()
    
    # MOCK DATA GENERATOR (If API fails or no secrets)
    if token == "MOCK" or not token:
        import random
        mock_data = []
        for i in range(5):
            # Generate random points near the address
            r_lat = lat + random.uniform(-0.01, 0.01)
            r_lon = lon + random.uniform(-0.01, 0.01)
            mock_data.append({
                "WellName": f"MOCK WELL {i+1}H",
                "Operator": "OKLAHOMA ENERGY DEV",
                "API": f"350001234{i}",
                "Latitude": r_lat,
                "Longitude": r_lon,
                "Status": "ACTIVE"
            })
        return pd.DataFrame(mock_data)

    # REAL API CALL
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    
    # Bounding box for API query (approx 1 mile radius buffer)
    # 1 mile ~= 0.0145 degrees
    offset = 0.02
    query = {
        "dataset": "wells",
        "query": {
            "and": [
                {"greaterThan": ["Latitude", lat - offset]},
                {"lessThan": ["Latitude", lat + offset]},
                {"greaterThan": ["Longitude", lon - offset]},
                {"lessThan": ["Longitude", lon + offset]},
                {"in": ["WellStatus", ["ACTIVE", "COMPLETED", "DRILLING"]]} 
            ]
        },
        "fields": ["API", "WellName", "OperatorAlias", "Latitude", "Longitude", "WellStatus"],
        "pageSize": 100
    }
    
    try:
        url = "https://api.enverus.com/v3/direct-access/query"
        resp = requests.post(url, headers=headers, json=query)
        if resp.status_code == 200:
            data = resp.json()
            df = pd.DataFrame(data)
            # Normalize column names
            df = df.rename(columns={"OperatorAlias": "Operator", "WellStatus": "Status"})
            return df
        else:
            st.error(f"API Error: {resp.status_code}")
            return pd.DataFrame()
    except Exception as e:
        st.error(f"Connection Error: {e}")
        return pd.DataFrame()

# -----------------------------------------------------------------------------
# 4. UI & MAIN LOGIC
# -----------------------------------------------------------------------------

def main():
    st.sidebar.title("🛢️ Discovery Portal")
    
    # A. Inputs
    address_input = st.sidebar.text_input("Enter Target Address (OK):", "2000 N Classen Blvd, Oklahoma City, OK")
    
    uploaded_file = st.sidebar.file_uploader("Upload Property Boundary (.geojson)", type=["geojson", "json"])
    
    st.sidebar.markdown("---")
    st.sidebar.info("**Note:** If no file is uploaded, a 10-acre box will be generated around the address.")

    # B. Processing
    if address_input:
        lat, lon = get_lat_long(address_input)
        
        if lat and lon:
            # 1. Define Boundary
            if uploaded_file:
                try:
                    geo_data = json.load(uploaded_file)
                    # Extract the first polygon features geometry
                    # Simplistic extraction for demo purposes
                    features = geo_data.get('features', [])
                    if features:
                        geom_shape = shape(features[0]['geometry'])
                        boundary_poly = geom_shape
                        boundary_geojson = features[0]['geometry']
                    else:
                        st.error("Invalid GeoJSON: No features found.")
                        boundary_poly = create_fallback_boundary(lat, lon)
                        boundary_geojson = mapping(boundary_poly)
                except Exception as e:
                    st.error(f"Error reading file: {e}")
                    boundary_poly = create_fallback_boundary(lat, lon)
                    boundary_geojson = mapping(boundary_poly)
            else:
                boundary_poly = create_fallback_boundary(lat, lon)
                boundary_geojson = mapping(boundary_poly)

            # 2. Fetch Data
            df_wells = fetch_wells_nearby(lat, lon)
            
            # 3. Calculate Distances
            if not df_wells.empty:
                df_wells['geometry'] = df_wells.apply(lambda x: Point(x['Longitude'], x['Latitude']), axis=1)
                df_wells['Dist_to_Prop_ft'] = df_wells['geometry'].apply(lambda x: calculate_distance_feet(x, boundary_poly)).astype(int)
                
                # Sort by proximity
                df_wells = df_wells.sort_values('Dist_to_Prop_ft')

            # -------------------------------------------------------------------------
            # 5. MAP VISUALIZATION (Folium + Esri)
            # -------------------------------------------------------------------------
            
            m = folium.Map(location=[lat, lon], zoom_start=15, tiles=None) # Set tiles=None to start clean

            # Add Esri World Imagery (Satellite)
            folium.TileLayer(
                tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                attr='Esri',
                name='Esri Satellite',
                overlay=False,
                control=True
            ).add_to(m)

            # Add Reference Overlay (Roads/Labels) so users aren't lost in pure satellite
            folium.TileLayer(
                tiles='https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
                attr='Esri',
                name='Esri Labels',
                overlay=True,
                control=True
            ).add_to(m)

            # Draw Property Boundary
            folium.GeoJson(
                boundary_geojson,
                name="Property Boundary",
                style_function=lambda x: {'fillColor': '#ffaf00', 'color': '#ffaf00', 'weight': 2, 'fillOpacity': 0.2}
            ).add_to(m)

            # Draw Address Marker
            folium.Marker(
                [lat, lon], 
                popup="Subject Property",
                icon=folium.Icon(color="red", icon="home")
            ).add_to(m)

            # Draw Wells
            if not df_wells.empty:
                for idx, row in df_wells.iterrows():
                    # Color code based on distance
                    color = "green" if row['Dist_to_Prop_ft'] == 0 else "blue"
                    
                    folium.CircleMarker(
                        location=[row['Latitude'], row['Longitude']],
                        radius=6,
                        color="white",
                        weight=1,
                        fill=True,
                        fill_color=color,
                        fill_opacity=1,
                        popup=folium.Popup(
                            f"<b>{row['WellName']}</b><br>Op: {row['Operator']}<br>Dist: {row['Dist_to_Prop_ft']} ft", 
                            max_width=250
                        )
                    ).add_to(m)

            folium.LayerControl().add_to(m)
            MeasureControl(position='bottomleft').add_to(m)

            # -------------------------------------------------------------------------
            # 6. DASHBOARD LAYOUT
            # -------------------------------------------------------------------------
            st.title("Oklahoma Well Proximity Portal")
            
            col1, col2 = st.columns([3, 1])
            
            with col1:
                st_folium(m, height=600, width=None)
            
            with col2:
                st.subheader("Analysis")
                count_on_prop = len(df_wells[df_wells['Dist_to_Prop_ft'] == 0])
                count_nearby = len(df_wells)
                
                st.metric("Wells ON Property", count_on_prop)
                st.metric("Wells within 1 Mile", count_nearby)
                
                if not df_wells.empty:
                    nearest = df_wells.iloc[0]
                    st.info(f"**Nearest Well:**\n\n{nearest['WellName']}\n\n{nearest['Dist_to_Prop_ft']} ft away")

            # Data Table
            st.subheader("Well Data Table")
            display_cols = ['WellName', 'Operator', 'API', 'Status', 'Dist_to_Prop_ft']
            st.dataframe(
                df_wells[display_cols].style.background_gradient(subset=['Dist_to_Prop_ft'], cmap="RdYlGn_r"),
                use_container_width=True
            )

        else:
            st.error("Address not found. Please try a more specific address (e.g., include Zip Code).")

if __name__ == "__main__":
    main()
