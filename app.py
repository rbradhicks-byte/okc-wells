import streamlit as st
import pandas as pd
import folium
from streamlit_folium import folium_static
from geopy.geocoders import ArcGIS
from enverus_developer_api import DirectAccessV2
from shapely.geometry import Point, Polygon
import geopandas as gpd

# 1. PAGE CONFIG & NAVY CSS
st.set_page_config(page_title="OKC Well Discovery", layout="wide")

st.markdown("""
    <style>
    h1, h2, h3, h4, [data-testid="stMetricLabel"] p {
        color: #000080 !important;
        font-weight: bold !important;
    }
    [data-testid="stMetricValue"] div {
        color: #000080 !important;
    }
    .stButton>button {
        background-color: #000080;
        color: white;
    }
    </style>
    """, unsafe_allow_html=True)

st.title("🛢️ Oklahoma County Well Discovery")

# 2. SEARCH INTERFACE
with st.form("search_form"):
    raw_address = st.text_input("Enter Address in Oklahoma County:", placeholder="e.g. 2000 N Classen Blvd")
    submit_button = st.form_submit_button("Analyze Location")

# 3. SIDEBAR
st.sidebar.header("Property Settings")
uploaded_file = st.sidebar.file_uploader("Upload Property Boundary (.geojson)", type=['geojson'])

# 4. DATA FETCHING (Minimum Viable Query)
def fetch_enverus_data():
    try:
        creds = st.secrets["enverus"]
        d2 = DirectAccessV2(
            client_id=creds["client_id"], 
            client_secret=creds["client_secret"], 
            api_key=creds.get("api_key", "NA")
        )
        
        # Reduced to only two parameters. 
        # Note: County is PascalCase, StateProvince is PascalCase.
        # We removed DeletedDate to eliminate any potential column name conflict.
        query_generator = d2.query(
            'well-origins',
            County='OKLAHOMA',
            StateProvince='OK',
            pagesize=10000
        )
        
        results = list(query_generator)
        if not results:
            return pd.DataFrame()
            
        return pd.DataFrame(results)
    
    except Exception as e:
        st.error(f"Enverus API Error: {e}")
        return pd.DataFrame()

# 5. MAIN LOGIC
if submit_button and raw_address:
    with st.spinner("Analyzing location and fetching data..."):
        full_address = f"{raw_address}, Oklahoma County, OK"
        geolocator = ArcGIS(user_agent="okc_well_portal")
        location = geolocator.geocode(full_address)

        if location:
            target_lat, target_lon = location.latitude, location.longitude
            
            # Boundary Logic
            if uploaded_file:
                gdf_boundary = gpd.read_file(uploaded_file)
                property_poly = gdf_boundary.geometry.iloc[0]
            else:
                offset = 0.001
                property_poly = Polygon([
                    (target_lon-offset, target_lat-offset),
                    (target_lon+offset, target_lat-offset),
                    (target_lon+offset, target_lat+offset),
                    (target_lon-offset, target_lat+offset)
                ])

            df_all = fetch_enverus_data()

            if not df_all.empty:
                # Coordinate Identification
                lat_col = next((c for c in df_all.columns if c.lower() in ['surfacelatitude', 'latitude']), None)
                lon_col = next((c for c in df_all.columns if c.lower() in ['surfacelongitude', 'longitude']), None)
                
                if lat_col and lon_col:
                    df_all[lat_col] = pd.to_numeric(df_all[lat_col], errors='coerce')
                    df_all[lon_col] = pd.to_numeric(df_all[lon_col], errors='coerce')
                    df_all = df_all.dropna(subset=[lat_col, lon_col])

                    # Local Filter (Approx 1.5 miles)
                    df_nearby = df_all[
                        (df_all[lat_col].between(target_lat-0.02, target_lat+0.02)) & 
                        (df_all[lon_col].between(target_lon-0.02, target_lon+0.02))
                    ].copy()

                    def calc_dist(row):
                        p = Point(row[lon_col], row[lat_col])
                        if property_poly.contains(p): return 0
                        return round(property_poly.distance(p) * 364000, 0)

                    if not df_nearby.empty:
                        df_nearby['Dist_to_Prop_ft'] = df_nearby.apply(calc_dist, axis=1)
                        
                        m1, m2 = st.columns(2)
                        m1.metric("Wells ON Property", len(df_nearby[df_nearby['Dist_to_Prop_ft'] == 0]))
                        m2.metric("Wells Nearby (1mi)", len(df_nearby[df_nearby['Dist_to_Prop_ft'] > 0]))

                        m = folium.Map(location
