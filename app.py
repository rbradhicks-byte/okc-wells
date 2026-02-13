import streamlit as st
import pandas as pd
import folium
from streamlit_folium import folium_static
from geopy.geocoders import ArcGIS
from enverus_developer_api import DirectAccessV2
from shapely.geometry import Point, Polygon
import geopandas as gpd

# 1. PAGE CONFIG & AGGRESSIVE NAVY STYLING
st.set_page_config(page_title="OKC Well Discovery", layout="wide")

st.markdown("""
    <style>
    /* Force Navy Blue on all headers and metric labels */
    h1, h2, h3, h4, h5, h6, [data-testid="stMetricLabel"] p {
        color: #000080 !important;
        font-weight: bold !important;
    }
    /* Fix for white text on metrics in dark mode */
    [data-testid="stMetricValue"] {
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

# 4. DATA FETCHING (The "Bulletproof" Version)
def fetch_enverus_data():
    try:
        creds = st.secrets["enverus"]
        client_id = creds["client_id"]
        client_secret = creds["client_secret"]
        api_key = creds.get("api_key", "NA")

        d2 = DirectAccessV2(client_id=client_id, client_secret=client_secret, api_key=api_key)
        
        # We use PascalCase for fields. If these fail, the API will tell us which specific one.
        # These are the standard keys for 'well-origins' V2.
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
        # If the API complains about specific field names, we capture that here.
        st.error(f"Enverus API Connection Error: {e}")
        return pd.DataFrame()

# 5. MAIN APP LOGIC
if submit_button and raw_address:
    with st.spinner("Analyzing location and fetching Enverus data..."):
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
                # 10-acre fallback (approx square)
                offset = 0.001
                property_poly = Polygon([
                    (target_lon-offset, target_lat-offset),
                    (target_lon+offset, target_lat-offset),
                    (target_lon+offset, target_lat+offset),
                    (target_lon-offset, target_lat+offset)
                ])

            # Fetch Well Data
            df_all = fetch_enverus_data()

            if not df_all.empty:
                # Ensure coordinate columns are numeric
                df_all['SurfaceLatitude'] = pd.to_numeric(df_all['SurfaceLatitude'], errors='coerce')
                df_all['SurfaceLongitude'] = pd.to_numeric(df_all['SurfaceLongitude'], errors='coerce')
                df_all = df_all.dropna(subset=['SurfaceLatitude', 'SurfaceLongitude'])

                # Spatial Filter in Python: Keep wells within ~1.5 miles (0.02 degrees)
                df_nearby = df_all[
                    (df_all['SurfaceLatitude'].between(target_lat-0.02, target_lat+0.02)) & 
                    (df_all['SurfaceLongitude'].between(target_lon-0.02, target_lon+0.02))
                ].copy()

                # Calculate Distance to Property
                def calculate_feet(row):
                    p = Point(row['SurfaceLongitude'], row['SurfaceLatitude'])
                    if property_poly.contains(p):
                        return 0
                    # Quick conversion: 1 degree approx 364,000 feet
                    return round(property_poly.distance(p) * 364000, 0)

                if not df_nearby.empty:
                    df_nearby['Dist_to_Prop_ft'] = df_nearby.apply(calculate_feet, axis=1)
                    
                    # Display Navy Metrics
                    on_prop = len(df_nearby[df_nearby['Dist_to_Prop_ft'] == 0])
                    nearby = len(df_nearby[df_nearby['Dist_to_Prop_ft'] > 0])
                    
                    m1, m2 = st.columns(2)
                    m1.metric("Wells ON Property", on_prop)
                    m2.metric("Wells Nearby (1mi)", nearby)

                    # Map View
                    m = folium.Map(location=[target_lat, target_lon], zoom_start=15)
                    folium.TileLayer(
                        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                        attr='Esri', name='Satellite', overlay=False
                    ).add_to(m)
                    
                    # Add Property Boundary to Map
                    folium.GeoJson(property_poly, name="Property Boundary", style_function=lambda x: {'color': 'blue', 'fillOpacity': 0.1}).add_to(m)
                    
                    # Add Wells to Map
                    for _, row in df_nearby.iterrows():
                        color = 'green' if row['Dist_to_Prop_ft'] == 0 else 'orange'
                        folium.CircleMarker(
                            location=[row['SurfaceLatitude'], row['SurfaceLongitude']],
                            radius=6, color=color, fill=True,
                            popup=f"<b>Well:</b> {row['WellName']}<br><b>Operator:</b> {row['OperatorName']}"
                        ).add_to(m)
                    
                    folium_static(m)
                    
                    # Data Table
                    st.subheader("Nearby Well Details")
                    st.dataframe(df_nearby[['WellName', 'OperatorName', 'Dist_to_Prop_ft', 'TotalDepth']].sort_values('Dist_to_Prop_ft'))
                else:
                    st.warning("No wells found within the search radius.")
            else:
                st.info("The Enverus API returned no data for Oklahoma County. Check your API credentials in Streamlit Secrets.")
        else:
            st.error("Address not found. Try adding 'Oklahoma City' or a Zip Code.")
