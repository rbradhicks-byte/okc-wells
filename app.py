import streamlit as st
import pandas as pd
import folium
from streamlit_folium import folium_static
from geopy.geocoders import ArcGIS
from enverus_developer_api import DirectAccessV2
from shapely.geometry import Point, Polygon
import geopandas as gpd

# 1. PAGE CONFIG & STYLING
st.set_page_config(page_title="OKC Well Discovery", layout="wide")

st.markdown("""
    <style>
    /* Aggressive Navy Blue Force */
    h1, h2, h3, h4, [data-testid="stMetricLabel"] p {
        color: #000080 !important;
        font-weight: bold !important;
    }
    [data-testid="stMetricValue"] div {
        color: #000080 !important;
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

# 4. DATA FETCHING (The "Bulletproof" Casing)
def fetch_enverus_data():
    try:
        creds = st.secrets["enverus"]
        d2 = DirectAccessV2(
            client_id=creds["client_id"], 
            client_secret=creds["client_secret"], 
            api_key=creds.get("api_key", "NA")
        )
        
        # We fetch using the most common PascalCase headers for the 'well-origins' V2 dataset.
        # Note: 'County' and 'DeletedDate' MUST be capitalized.
        query_generator = d2.query(
            'well-origins',
            County='OKLAHOMA',
            DeletedDate='null',
            fields='WellName,OperatorName,API_UWI_14,TotalDepth,SurfaceLatitude,SurfaceLongitude',
            pagesize=10000
        )
        
        data_list = list(query_generator)
        if not data_list:
            return pd.DataFrame()
            
        return pd.DataFrame(data_list)
    
    except Exception as e:
        # DIAGNOSTIC MODE: If the PascalCase fails, we try a generic "No-Fields" pull to see valid headers
        st.warning(f"Initial pull failed: {e}. Attempting diagnostic pull...")
        try:
            d2_diag = DirectAccessV2(client_id=creds["client_id"], client_secret=creds["client_secret"], api_key=creds.get("api_key", "NA"))
            diag_gen = d2_diag.query('well-origins', County='OKLAHOMA', DeletedDate='null', pagesize=1)
            first_row = next(diag_gen)
            st.write("Diagnostic: Valid Column Names for your account are:", list(first_row.keys()))
        except:
            pass
        st.error(f"Enverus API Connection Error: {e}")
        return pd.DataFrame()

# 5. MAIN LOGIC
if submit_button and raw_address:
    with st.spinner("Analyzing and Fetching Data..."):
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
                # 10-acre fallback
                offset = 0.001
                property_poly = Polygon([
                    (target_lon-offset, target_lat-offset),
                    (target_lon+offset, target_lat-offset),
                    (target_lon+offset, target_lat+offset),
                    (target_lon-offset, target_lat+offset)
                ])

            # Fetch and Clean Data
            df_all = fetch_enverus_data()

            if not df_all.empty:
                # Standardize Column names (in case they came back as strings)
                df_all['SurfaceLatitude'] = pd.to_numeric(df_all['SurfaceLatitude'], errors='coerce')
                df_all['SurfaceLongitude'] = pd.to_numeric(df_all['SurfaceLongitude'], errors='coerce')
                df_all = df_all.dropna(subset=['SurfaceLatitude', 'SurfaceLongitude'])

                # Filter within ~1.5 miles
                df_nearby = df_all[
                    (df_all['SurfaceLatitude'].between(target_lat-0.02, target_lat+0.02)) & 
                    (df_all['SurfaceLongitude'].between(target_lon-0.02, target_lon+0.02))
                ].copy()

                def calc_dist(row):
                    p = Point(row['SurfaceLongitude'], row['SurfaceLatitude'])
                    if property_poly.contains(p): return 0
                    return round(property_poly.distance(p) * 364000, 0) # Approx ft

                if not df_nearby.empty:
                    df_nearby['Dist_to_Prop_ft'] = df_nearby.apply(calc_dist, axis=1)
                    
                    # Navy Metrics
                    col1, col2 = st.columns(2)
                    col1.metric("Wells ON Property", len(df_nearby[df_nearby['Dist_to_Prop_ft'] == 0]))
                    col2.metric("Wells Nearby (1mi)", len(df_nearby[df_nearby['Dist_to_Prop_ft'] > 0]))

                    # Map
                    m = folium.Map(location=[target_lat, target_lon], zoom_start=15)
                    folium.TileLayer(
                        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                        attr='Esri', name='Satellite'
                    ).add_to(m)
                    
                    folium.GeoJson(property_poly, name="Property", style_function=lambda x: {'color':'blue', 'fillOpacity':0.1}).add_to(m)
                    
                    for _, row in df_nearby.iterrows():
                        color = 'green' if row['Dist_to_Prop_ft'] == 0 else 'orange'
                        folium.CircleMarker(
                            location=[row['SurfaceLatitude'], row['SurfaceLongitude']],
                            radius=6, color=color, fill=True,
                            popup=f"{row['WellName']} ({row['OperatorName']})"
                        ).add_to(m)
                    
                    folium_static(m)
                    
                    # Sortable Table
                    st.subheader("Well List")
                    st.dataframe(df_nearby[['WellName', '
