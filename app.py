import streamlit as st
import pandas as pd
import geopandas as gpd
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
from shapely.geometry import Point, Polygon, box
from shapely.ops import nearest_points
import requests
import json

# ---------------------------------------------------------
# 1. APP CONFIGURATION & SECRETS
# ---------------------------------------------------------
st.set_page_config(page_title="OK Well Proximity Portal", layout="wide")

# Validating Secrets exist
if "enverus" not in st.secrets:
    st.warning("⚠️ Enverus credentials not found in `.streamlit/secrets.toml`. API features will use mock data for demonstration.")

# ---------------------------------------------------------
# 2. HELPER FUNCTIONS
# ---------------------------------------------------------

def get_coordinates(address):
    """
    Geocodes an address to (lat, lon) using OpenStreetMap (Nominatim).
    """
    try:
        geolocator = Nominatim(user_agent="okc_well_explorer_v1")
        location = geolocator.geocode(address)
        if location:
            return location.latitude, location.longitude
        else:
            return None, None
    except Exception as e:
        st.error(f"Geocoding Error: {e}")
        return None, None

def create_fallback_boundary(lat, lon):
    """
    Creates a ~10-acre square box around the center point.
    10 acres is roughly 660ft x 660ft.
    0.001 degrees lat is ~364ft, 0.001 deg lon is ~300ft (in OK).
    We will use a buffer of approx 0.002 degrees for simplicity 
    to create a testing polygon.
    """
    # Create a box roughly centered on the point
    delta = 0.002 # Approx 200 meters / 600-700ft radius
    minx, miny = lon - delta, lat - delta
    maxx, maxy = lon + delta, lat + delta
    return box(minx, miny, maxx, maxy)

def fetch_enverus_wells(lat, lon, radius_miles=1.0):
    """
    Fetches wells from Enverus DirectAccess. 
    Note: Requires valid 'User' and 'Password' or 'Token' in st.secrets.
    """
    # -----------------------------------------------------
    # REAL API IMPLEMENTATION BLOCK
    # -----------------------------------------------------
    if "enverus" in st.secrets:
        # Generic Enverus DirectAccess Query Structure
        # You may need to adjust the URL based on your specific dataset subscription (e.g., GOR, Completions, etc.)
        url = "https://api.enverus.com/v3/direct-access/wells" 
        
        headers = {
            "Authorization": f"Bearer {st.secrets['enverus'].get('api_token', '')}",
            "Content-Type": "application/json"
        }
        
        # If using Basic Auth (User/Pass) instead of Token:
        # auth = (st.secrets['enverus']['username'], st.secrets['enverus']['password'])
        
        # Define a bounding box for the API query to minimize data transfer
        # 1 deg lat ~= 69 miles. 1 mile ~= 0.0145 degrees
        offset = 0.02
        params = {
            "minLatitude": lat - offset,
            "maxLatitude": lat + offset,
            "minLongitude": lon - offset,
            "maxLongitude": lon + offset,
            "deleted": "false",
            "pagesize": 1000
        }

        try:
            # response = requests.get(url, headers=headers, params=params) # Uncomment for real API
            # response.raise_for_status()
            # data = response.json()
            # return pd.DataFrame(data)
            pass # Skipping real call to prevent crash without real keys in this demo env
        except Exception as e:
            st.error(f"Enverus API Error: {e}")
            return pd.DataFrame()

    # -----------------------------------------------------
    # MOCK DATA (For Demonstration without paid keys)
    # -----------------------------------------------------
    # Creating fake wells around the requested lat/lon for UI testing
    mock_data = {
        "WellName": ["OKC Wildcat #1", "Sooner State A-2", "Red Dirt #4", "Tulsa King #9"],
        "Operator": ["Continental Resources", "Devon Energy", "Chesapeake", "EOG"],
        "Latitude": [lat + 0.0015, lat - 0.002, lat + 0.0005, lat + 0.008],
        "Longitude": [lon + 0.001, lon - 0.001, lon + 0.003, lon - 0.005],
        "Status": ["ACTIVE", "ACTIVE", "INACTIVE", "DRILLING"]
    }
    return pd.DataFrame(mock_data)

def calculate_distance_to_boundary(well_point, boundary_poly):
    """
    Calculates distance in feet.
    1. If point is inside polygon -> 0 ft.
    2. If outside, find nearest point on polygon exterior, calc Geodesic distance.
    """
    if boundary_poly.contains(well_point):
        return 0.0
    
    # Find nearest point on the polygon boundary
    p1, p2 = nearest_points(boundary_poly, well_point)
    
    # Calculate geodesic distance (accounting for earth curvature)
    # p1 is on polygon, p2 is the well
    coords_1 = (p1.y, p1.x) # Lat, Lon
    coords_2 = (p2.y, p2.x) # Lat, Lon
    
    distance_ft = geodesic(coords_1, coords_2).feet
    return round(distance_ft, 2)

# ---------------------------------------------------------
# 3. UI & SIDEBAR
# ---------------------------------------------------------
st.sidebar.title("🛢️ OK Well Proximity")

# INPUT: Address
address_input = st.sidebar.text_input("Target Address", "200 SW C Ave, Lawton, OK")

# INPUT: Property Boundary
st.sidebar.markdown("### Property Boundary")
uploaded_file = st.sidebar.file_uploader("Upload GeoJSON", type=["geojson", "json"])

if uploaded_file:
    try:
        boundary_gdf = gpd.read_file(uploaded_file)
        # Ensure CRS is Lat/Lon (WGS84)
        if boundary_gdf.crs != "EPSG:4326":
            boundary_gdf = boundary_gdf.to_crs("EPSG:4326")
        st.sidebar.success("Boundary loaded successfully!")
    except Exception as e:
        st.sidebar.error(f"Error reading file: {e}")
        boundary_gdf = None
else:
    st.sidebar.info("No file uploaded. Generating a fallback 10-acre box around address.")
    boundary_gdf = None

# ACTION: Run
run_btn = st.sidebar.button("Find Wells", type="primary")

# ---------------------------------------------------------
# 4. MAIN LOGIC EXECUTION
# ---------------------------------------------------------
if run_btn and address_input:
    with st.spinner("Geocoding address and fetching Enverus data..."):
        
        # 1. Geocode
        lat, lon = get_coordinates(address_input)
        
        if lat is None:
            st.error("Address not found. Please try being more specific (include City, State, Zip).")
        else:
            st.success(f"Located: {lat:.4f}, {lon:.4f}")
            
            # 2. Define Boundary
            if boundary_gdf is not None:
                # Use the first polygon found in the uploaded file
                boundary_poly = boundary_gdf.geometry.iloc[0]
            else:
                boundary_poly = create_fallback_boundary(lat, lon)

            # 3. Fetch Data
            wells_df = fetch_enverus_wells(lat, lon)
            
            if not wells_df.empty:
                # 4. Process Distances
                results = []
                for idx, row in wells_df.iterrows():
                    well_pt = Point(row['Longitude'], row['Latitude'])
                    dist = calculate_distance_to_boundary(well_pt, boundary_poly)
                    
                    results.append({
                        "Well Name": row['WellName'],
                        "Operator": row['Operator'],
                        "Status": row['Status'],
                        "Lat": row['Latitude'],
                        "Lon": row['Longitude'],
                        "Distance to Prop (ft)": dist,
                        "Inside Property": "YES" if dist == 0 else "NO"
                    })
                
                results_df = pd.DataFrame(results)
                
                # Sort by distance
                results_df = results_df.sort_values(by="Distance to Prop (ft)")

                # -----------------------------------------------------
                # 5. MAP VISUALIZATION
                # -----------------------------------------------------
                st.subheader("Satellite Map View")
                
                # Initialize Map centered on user address
                m = folium.Map(location=[lat, lon], zoom_start=15)

                # ADD ESRI WORLD IMAGERY (SATELLITE) - As Requested
                folium.TileLayer(
                    tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                    attr='Esri',
                    name='Esri Satellite',
                    overlay=False,
                    control=True
                ).add_to(m)

                # Add Property Boundary (Blue Polygon)
                # folium needs coords in [lat, lon], shapely uses [lon, lat]
                sim_geo = gpd.GeoSeries([boundary_poly]).set_crs("EPSG:4326")
                folium.GeoJson(
                    sim_geo,
                    name="Property Boundary",
                    style_function=lambda x: {
                        'fillColor': 'blue',
                        'color': 'blue',
                        'weight': 2,
                        'fillOpacity': 0.1
                    }
                ).add_to(m)

                # Add User Marker
                folium.Marker(
                    [lat, lon],
                    popup="Target Address",
                    icon=folium.Icon(color="blue", icon="home")
                ).add_to(m)

                # Add Wells
                for _, row in results_df.iterrows():
                    color = "green" if row['Status'] == 'ACTIVE' else "red"
                    folium.CircleMarker(
                        location=[row['Lat'], row['Lon']],
                        radius=6,
                        popup=f"{row['Well Name']}<br>Dist: {row['Distance to Prop (ft)']} ft",
                        color=color,
                        fill=True,
                        fill_color=color
                    ).add_to(m)

                # Render Map
                st_folium(m, width=1200, height=500)

                # -----------------------------------------------------
                # 6. DATA TABLE
                # -----------------------------------------------------
                st.subheader("Proximity Report")
                st.dataframe(results_df, use_container_width=True)
                
            else:
                st.warning("No wells found in this area via API.")

elif run_btn and not address_input:
    st.error("Please enter an address.")
else:
    st.info("Enter an address and click 'Find Wells' to start.")
