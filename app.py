import streamlit as st
import pandas as pd
import folium
from streamlit_folium import folium_static
from geopy.geocoders import ArcGIS
from enverus_developer_api import DirectAccessV2
from shapely.geometry import Point, Polygon
import geopandas as gpd

# 1. PAGE CONFIG & CSS
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

# 4. DATA FETCHING
def fetch_enverus_data():
    try:
        creds = st.secrets["enverus"]
        d2 = DirectAccessV2(
            client_id=creds["client_id"], 
            client_secret=creds["client_secret"], 
            api_key=creds.get("api_key", "NA")
        )
        
        # We use to_dataframe which is more robust for V2. 
        # We try 'OKLAHOMA' first.
        df = d2.to_dataframe(
            dataset='well-origins',
            county='OKLAHOMA',
            deleteddate='null',
            pagesize=10000
        )
        
        # Fallback for case sensitivity in the 'county' value itself
        if df.empty:
            df = d2.to_dataframe(
                dataset='well-origins',
                county='Oklahoma',
                deleteddate='null',
                pagesize=10000
            )
            
        return df
    
    except Exception as e:
        st.error(f"Enverus API Connection Error: {e}")
        return pd.DataFrame()

# 5. MAIN LOGIC
if submit_button and raw_address:
    with st.spinner("Analyzing location and fetching data..."):
        # Geocode the address
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
                # Fallback: 10-acre square (~360ft)
                offset = 0.001
                property_poly = Polygon([
                    (target_lon-offset, target_lat-offset),
                    (target_lon+offset, target_lat-offset),
                    (target_lon+offset, target_lat+offset),
                    (target_lon-offset, target_lat+offset)
                ])

            # Fetch Data
            df_all = fetch_enverus_data()

            if not df_all.empty:
                # Enverus V2 returns column names like 'SurfaceLatitude' or 'Latitude'
                # We identify which one exists and standardize it
                lat_col = next((c for c in ['SurfaceLatitude', 'Latitude', 'surfacelatitude'] if c in df_all.columns), None)
                lon_col = next((c for c in ['SurfaceLongitude', 'Longitude', 'surfacelongitude'] if c in df_all.columns), None)
                
                if lat_col and lon_col:
                    df_all[lat_col] = pd.to_numeric(df_all[lat_col], errors='coerce')
                    df_all[lon_col] = pd.to_numeric(df_all[lon_col], errors='coerce')
                    df_all = df_all.dropna(subset=[lat_col, lon_col])

                    # Filter within ~1.5 miles (0.02 degrees)
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
                        
                        on_prop = len(df_nearby[df_nearby['Dist_to_Prop_ft'] == 0])
                        nearby_count = len(df_nearby[df_nearby['Dist_to_Prop_ft'] > 0])
                        
                        m1, m2 = st.columns(2)
                        m1.metric("Wells ON Property", on_prop)
                        m2.metric("Wells Nearby (1mi)", nearby_count)

                        # Map View
                        m = folium.Map(location=[target_lat, target_lon], zoom_start=15)
                        folium.TileLayer(
                            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                            attr='Esri', name='Satellite'
                        ).add_to(m)
                        
                        folium.GeoJson(property_poly, name="Property", style_function=lambda x: {'color':'blue', 'fillOpacity':0.1}).add_to(m)
                        
                        for _, row in df_nearby.iterrows():
                            color = 'green' if row['Dist_to_Prop_ft'] == 0 else 'orange'
                            # Get the best available name column
                            name_col = next((c for c in ['WellName', 'Well_Name', 'wellname'] if c in df_nearby.columns), 'Unknown')
                            folium.CircleMarker(
                                location=[row[lat_col], row[lon_col]],
                                radius=6, color=color, fill=True,
                                popup=f"Well: {row.get(name_col, 'N/A')}"
                            ).add_to(m)
                        
                        folium_static(m)
                        
                        # Data Table
                        st.subheader("Nearby Well Details")
                        # Display only the columns that actually exist in this dataset version
                        display_cols = [c for c in ['WellName', 'Well_Name', 'OperatorName', 'Operator_Name', 'Dist_to_Prop_ft', 'TotalDepth', 'Total_Depth'] if c in df_nearby.columns]
                        st.dataframe(df_nearby[display_cols].sort_values('Dist_to_Prop_ft'))
                    else:
                        st.warning("No wells found in a 1.5-mile radius.")
                else:
                    st.error(f"Required coordinate columns not found. Found: {list(df_all.columns)}")
            else:
                st.error("The Enverus API returned 0 records for Oklahoma County.")
        else:
            st.error("Address geocoding failed. Please check the address.")
