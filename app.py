import streamlit as st
import pandas as pd
import folium
from streamlit_folium import folium_static
from geopy.geocoders import ArcGIS
from enverus_developer_api import DirectAccessV2
from shapely.geometry import Point, Polygon
import geopandas as gpd

# 1. PAGE CONFIG & NAVY CSS (Restored UI Polish)
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

# 3. SIDEBAR (Restored Features)
st.sidebar.header("App Settings")
data_source = st.sidebar.radio("Select Data Source:", ["Dummy/Test Data", "Live Enverus API"])
uploaded_file = st.sidebar.file_uploader("Upload Property Boundary (.geojson)", type=['geojson'])

# 4. DATA FETCHING (The "No-Hang" Method)
def fetch_enverus_data():
    try:
        creds = st.secrets["enverus"]
        d2 = DirectAccessV2(
            client_id=creds["client_id"], 
            client_secret=creds["client_secret"], 
            api_key=creds.get("api_key", "NA")
        )
        
        # We use a limited loop instead of list(query) to prevent the infinite hang.
        # This pulls records one by one until it hits 2000 or runs out.
        query = d2.query('well-origins', County='OKLAHOMA', pagesize=1000)
        
        wells = []
        count = 0
        for row in query:
            wells.append(row)
            count += 1
            if count >= 2000:  # Hard cap to prevent timeout
                break
                
        if not wells:
            return pd.DataFrame()
            
        return pd.DataFrame(wells)
    
    except Exception as e:
        st.sidebar.error(f"Enverus API Error: {e}")
        return pd.DataFrame()

def get_dummy_data(lat, lon):
    """Restores the dummy data you confirmed was working previously."""
    data = [
        {"WellName": "Well 1 H", "OperatorName": "Oklahoma Energy Dev", "SurfaceLatitude": lat + 0.002, "SurfaceLongitude": lon + 0.002, "TotalDepth": 12000},
        {"WellName": "Discovery 2-4", "OperatorName": "OKC Resources", "SurfaceLatitude": lat - 0.003, "SurfaceLongitude": lon + 0.001, "TotalDepth": 11500},
        {"WellName": "Wildcat 7", "OperatorName": "Devon Energy", "SurfaceLatitude": lat + 0.005, "SurfaceLongitude": lon - 0.002, "TotalDepth": 13200}
    ]
    return pd.DataFrame(data)

# 5. MAIN LOGIC
if submit_button and raw_address:
    with st.spinner("Analyzing location..."):
        # Geocode the address
        full_address = f"{raw_address}, Oklahoma County, OK"
        geolocator = ArcGIS(user_agent="okc_well_portal")
        location = geolocator.geocode(full_address)

        if location:
            target_lat, target_lon = location.latitude, location.longitude
            
            # Create/Load Property Boundary
            if uploaded_file:
                gdf_boundary = gpd.read_file(uploaded_file)
                property_poly = gdf_boundary.geometry.iloc[0]
            else:
                # 10-acre square fallback
                offset = 0.001
                property_poly = Polygon([
                    (target_lon-offset, target_lat-offset),
                    (target_lon+offset, target_lat-offset),
                    (target_lon+offset, target_lat+offset),
                    (target_lon-offset, target_lat+offset)
                ])

            # Fetch Data
            if data_source == "Live Enverus API":
                df_all = fetch_enverus_data()
            else:
                df_all = get_dummy_data(target_lat, target_lon)

            if not df_all.empty:
                # Identification of columns (Case-insensitive)
                lat_col = next((c for c in df_all.columns if c.lower() in ['surfacelatitude', 'latitude']), None)
                lon_col = next((c for c in df_all.columns if c.lower() in ['surfacelongitude', 'longitude']), None)
                
                if lat_col and lon_col:
                    df_all[lat_col] = pd.to_numeric(df_all[lat_col], errors='coerce')
                    df_all[lon_col] = pd.to_numeric(df_all[lon_col], errors='coerce')
                    df_all = df_all.dropna(subset=[lat_col, lon_col])

                    # Distance Math
                    def calc_dist(row):
                        p = Point(row[lon_col], row[lat_col])
                        if property_poly.contains(p): return 0
                        return round(property_poly.distance(p) * 364000, 0) # Approx feet

                    df_all['Dist_ft'] = df_all.apply(calc_dist, axis=1)
                    # Filter for wells within 2 miles for the display
                    df_nearby = df_all[df_all['Dist_ft'] < 10560].copy()

                    # DISPLAY METRICS
                    on_prop = len(df_nearby[df_nearby['Dist_ft'] == 0])
                    nearby_count = len(df_nearby[df_nearby['Dist_ft'] > 0])
                    
                    c1, c2 = st.columns(2)
                    c1.metric("Wells ON Property", on_prop)
                    c2.metric("Nearby Wells (2mi)", nearby_count)

                    # MAP (Satellite Restored)
                    m = folium.Map(location=[target_lat, target_lon], zoom_start=15)
                    folium.TileLayer(
                        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                        attr='Esri', name='Satellite'
                    ).add_to(m)
                    
                    folium.GeoJson(property_poly, name="Property", style_function=lambda x: {'color':'blue', 'fillOpacity':0.1}).add_to(m)
                    
                    name_col = next((c for c in df_nearby.columns if 'name' in c.lower()), df_nearby.columns[0])
                    for _, row in df_nearby.iterrows():
                        color = 'green' if row['Dist_ft'] == 0 else 'orange'
                        folium.CircleMarker(
                            location=[row[lat_col], row[lon_col]],
                            radius=6, color=color, fill=True,
                            popup=f"Well: {row.get(name_col, 'N/A')}"
                        ).add_to(m)
                    
                    folium_static(m)
                    
                    # TABLE
                    st.subheader("Nearby Well Details")
                    st.dataframe(df_nearby.sort_values('Dist_ft'))
                else:
                    st.error(f"Coordinates not found in data. Found: {list(df_all.columns)}")
            else:
                st.error("No data returned. Check credentials or data source selection.")
        else:
            st.error("Address geocoding failed.")
