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

# 4. DATA FETCHING (Multi-Step Fallback)
def fetch_enverus_data():
    try:
        creds = st.secrets["enverus"]
        d2 = DirectAccessV2(
            client_id=creds["client_id"], 
            client_secret=creds["client_secret"], 
            api_key=creds.get("api_key", "NA")
        )
        
        # ATTEMPT 1: State + County (Standard V2 format)
        df = d2.to_dataframe(
            dataset='well-origins',
            StateProvince='OK',
            County='OKLAHOMA',
            DeletedDate='null',
            pagesize=5000
        )
        
        # ATTEMPT 2: Case-insensitive Fallback
        if df is None or df.empty:
            df = d2.to_dataframe(
                dataset='well-origins',
                StateProvince='OK',
                County='Oklahoma',
                DeletedDate='null',
                pagesize=5000
            )
            
        # ATTEMPT 3: Minimalist (Just County)
        if df is None or df.empty:
            df = d2.to_dataframe(
                dataset='well-origins',
                County='OKLAHOMA',
                pagesize=5000
            )

        return df
    
    except Exception as e:
        # Check if it's a specific API error or a connection issue
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
            
            # Boundary Fallback
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

            if df_all is not None and not df_all.empty:
                # Identify Coordinate Columns
                lat_col = next((c for c in ['SurfaceLatitude', 'Latitude', 'surfacelatitude', 'latitude'] if c in df_all.columns), None)
                lon_col = next((c for c in ['SurfaceLongitude', 'Longitude', 'surfacelongitude', 'longitude'] if c in df_all.columns), None)
                
                if lat_col and lon_col:
                    df_all[lat_col] = pd.to_numeric(df_all[lat_col], errors='coerce')
                    df_all[lon_col] = pd.to_numeric(df_all[lon_col], errors='coerce')
                    df_all = df_all.dropna(subset=[lat_col, lon_col])

                    # Local Filter (1.5 miles)
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

                        # Map View
                        m = folium.Map(location=[target_lat, target_lon], zoom_start=15)
                        folium.TileLayer(
                            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                            attr='Esri', name='Satellite'
                        ).add_to(m)
                        
                        folium.GeoJson(property_poly, name="Property", style_function=lambda x: {'color':'blue', 'fillOpacity':0.1}).add_to(m)
                        
                        for _, row in df_nearby.iterrows():
                            color = 'green' if row['Dist_to_Prop_ft'] == 0 else 'orange'
                            name_val = row.get('WellName', row.get('Well_Name', 'Unknown'))
                            folium.CircleMarker(
                                location=[row[lat_col], row[lon_col]],
                                radius=6, color=color, fill=True,
                                popup=f"Well: {name_val}"
                            ).add_to(m)
                        
                        folium_static(m)
                        
                        st.subheader("Nearby Well Details")
                        display_cols = [c for c in ['WellName', 'Well_Name', 'OperatorName', 'Operator_Name', 'Dist_to_Prop_ft', 'TotalDepth'] if c in df_nearby.columns]
                        st.dataframe(df_nearby[display_cols].sort_values('Dist_to_Prop_ft'))
                    else:
                        st.warning("Address localized, but no wells found within 1.5 miles in the Enverus results.")
                else:
                    st.error(f"Data retrieved, but no coordinate columns found. Columns: {list(df_all.columns)}")
            else:
                st.error("The Enverus API successfully connected but returned zero records for Oklahoma County.")
        else:
            st.error("Address not found. Please try again.")
