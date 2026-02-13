import streamlit as st
import pandas as pd
import folium
from streamlit_folium import folium_static
from geopy.geocoders import ArcGIS
from enverus_developer_api import DirectAccessV2
from shapely.geometry import Point, Polygon
import geopandas as gpd
import itertools

# 1. PAGE CONFIG
st.set_page_config(page_title="OKC Well Discovery", layout="wide")

st.markdown("""
    <style>
    h1, h2, h3, [data-testid="stMetricLabel"] p { color: #000080 !important; font-weight: bold !important; }
    [data-testid="stMetricValue"] div { color: #000080 !important; }
    </style>
    """, unsafe_allow_html=True)

st.title("🛢️ Oklahoma County Well Discovery")

# 2. SEARCH INTERFACE
with st.form("search_form"):
    raw_address = st.text_input("Enter Address:", placeholder="e.g. 2000 N Classen Blvd")
    submit_button = st.form_submit_button("Analyze Location")

# 3. SIDEBAR
st.sidebar.header("Settings")
data_source = st.sidebar.radio("Data Source:", ["Live Enverus API", "Dummy/Test Data"])

# 4. DATA FETCHING (Limited & Logged)
def fetch_live_enverus():
    status_text = st.empty() # Placeholder for real-time status
    try:
        creds = st.secrets["enverus"]
        status_text.text("Connecting to Enverus...")
        d2 = DirectAccessV2(
            client_id=creds["client_id"], 
            client_secret=creds["client_secret"], 
            api_key=creds.get("api_key", "NA")
        )
        
        status_text.text("Requesting Wells (Max 1000)...")
        # We use islice to stop the generator from running forever
        query = d2.query('well-origins', County='OKLAHOMA', pagesize=1000)
        
        # Pull only the first 1000 items to prevent a hang
        results = list(itertools.islice(query, 1000))
        
        status_text.text(f"Received {len(results)} records.")
        return pd.DataFrame(results)
    except Exception as e:
        st.error(f"API Error: {e}")
        return pd.DataFrame()

# 5. MAIN LOGIC
if submit_button and raw_address:
    # Use a status container to see progress
    status_container = st.container()
    
    with st.spinner("Processing..."):
        # STEP 1: GEOCODE
        full_address = f"{raw_address}, Oklahoma County, OK"
        geolocator = ArcGIS(user_agent="okc_well_portal")
        location = geolocator.geocode(full_address)

        if location:
            t_lat, t_lon = location.latitude, location.longitude
            status_container.success(f"Location Found: {t_lat}, {t_lon}")
            
            # Boundary
            offset = 0.001
            property_poly = Polygon([(t_lon-offset, t_lat-offset), (t_lon+offset, t_lat-offset), (t_lon+offset, t_lat+offset), (t_lon-offset, t_lat+offset)])

            # STEP 2: FETCH
            if data_source == "Live Enverus API":
                df_all = fetch_live_enverus()
            else:
                # Minimal dummy data for speed
                df_all = pd.DataFrame([{"WellName": "Test Well", "Latitude": t_lat+0.002, "Longitude": t_lon+0.002}])

            # STEP 3: PROCESS & MAP
            if not df_all.empty:
                # Find Lat/Lon cols
                lat_col = next((c for c in df_all.columns if c.lower() in ['latitude', 'surfacelatitude', 'lat']), None)
                lon_col = next((c for c in df_all.columns if c.lower() in ['longitude', 'surfacelongitude', 'lon']), None)

                if lat_col and lon_col:
                    df_all[lat_col] = pd.to_numeric(df_all[lat_col], errors='coerce')
                    df_all[lon_col] = pd.to_numeric(df_all[lon_col], errors='coerce')
                    df_all = df_all.dropna(subset=[lat_col, lon_col])

                    # Distance Math
                    def calc_dist(row):
                        p = Point(row[lon_col], row[lat_col])
                        if property_poly.contains(p): return 0
                        return round(property_poly.distance(p) * 364000, 0)

                    df_all['Dist_ft'] = df_all.apply(calc_dist, axis=1)
                    # Show only wells within 3 miles
                    df_nearby = df_all[df_all['Dist_ft'] < 15000].copy()

                    # DISPLAY
                    st.metric("Nearby Wells", len(df_nearby))
                    
                    m = folium.Map(location=[t_lat, t_lon], zoom_start=14)
                    folium.TileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', attr='Esri', name='Satellite').add_to(m)
                    folium.GeoJson(property_poly, name="Property").add_to(m)
                    
                    for _, row in df_nearby.iterrows():
                        folium.CircleMarker(location=[row[lat_col], row[lon_col]], radius=5, color='orange', fill=True).add_to(m)
                    
                    folium_static(m)
                    st.dataframe(df_nearby)
                else:
                    st.error(f"Columns not found. Available: {list(df_all.columns)}")
            else:
                st.error("No data returned from Enverus.")
        else:
            st.error("Could not find that address.")
