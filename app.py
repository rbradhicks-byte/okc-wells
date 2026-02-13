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

# Force Navy Blue Styling for Headers and Metrics
st.markdown("""
    <style>
    h1, h2, h3, [data-testid="stMetricLabel"] p {
        color: #000080 !important;
        font-weight: bold !important;
    }
    .stButton>button {
        background-color: #000080;
        color: white;
    }
    </style>
    """, unsafe_allow_html=True)

st.title("🛢️ Oklahoma County Well Discovery")

# 2. SEARCH INTERFACE (TOP OF PAGE)
with st.form("search_form"):
    raw_address = st.text_input("Enter Address in Oklahoma County:", placeholder="e.g. 2000 N Classen Blvd")
    submit_button = st.form_submit_button("Analyze Location")

# 3. SIDEBAR (BOUNDARY UPLOAD)
st.sidebar.header("Property Settings")
uploaded_file = st.sidebar.file_uploader("Upload Property Boundary (.geojson)", type=['geojson'])

# 4. DATA FETCHING FUNCTION
def fetch_enverus_data():
    try:
        # Pull from Streamlit Secrets
        creds = st.secrets["enverus"]
        client_id = creds["client_id"]
        client_secret = creds["client_secret"]
        # API Key is often required by the library but ignored by the V2 OAuth flow
        api_key = creds.get("api_key", "NA")

        d2 = DirectAccessV2(client_id=client_id, client_secret=client_secret, api_key=api_key)
        
        # FIXED: Enverus V2 Field Names (PascalCase is required for both keys and values)
        # We fetch the whole county to avoid spatial query errors
        query_generator = d2.query(
            'well-origins',
            County='OKLAHOMA',
            DeletedDate='null',
            fields='WellName,OperatorName,API_UWI_14,TotalDepth,SurfaceLatitude,SurfaceLongitude',
            pagesize=10000
        )
        
        # Convert generator to list of dicts, then to DataFrame
        data_list = list(query_generator)
        if not data_list:
            return pd.DataFrame()
            
        return pd.DataFrame(data_list)
    
    except Exception as e:
        st.error(f"Enverus API Error: {e}")
        return pd.DataFrame()

# 5. MAIN LOGIC
if submit_button and raw_address:
    with st.spinner("Searching and Analyzing..."):
        # Geocoding
        full_address = f"{raw_address}, Oklahoma County, OK"
        geolocator = ArcGIS(user_agent="okc_well_portal")
        location = geolocator.geocode(full_address)

        if location:
            target_lat, target_lon = location.latitude, location.longitude
            
            # Create Property Boundary (Uploaded or 10-acre box fallback)
            if uploaded_file:
                gdf_boundary = gpd.read_file(uploaded_file)
                property_poly = gdf_boundary.geometry.iloc[0]
            else:
                # Fallback: Approx 10-acre square (0.001 degrees is ~360ft)
                offset = 0.001
                property_poly = Polygon([
                    (target_lon-offset, target_lat-offset),
                    (target_lon+offset, target_lat-offset),
                    (target_lon+offset, target_lat+offset),
                    (target_lon-offset, target_lat+offset)
                ])

            # Fetch Data
            df_all_wells = fetch_enverus_data()

            if not df_all_wells.empty:
                # Spatial Filtering in Python (The "Sniper" Logic)
                # Keep wells within ~1.5 miles (0.02 degrees)
                df_all_wells['SurfaceLatitude'] = pd.to_numeric(df_all_wells['SurfaceLatitude'])
                df_all_wells['SurfaceLongitude'] = pd.to_numeric(df_all_wells['SurfaceLongitude'])
                
                mask = (df_all_wells['SurfaceLatitude'].between(target_lat-0.02, target_lat+0.02)) & \
                       (df_all_wells['SurfaceLongitude'].between(target_lon-0.02, target_lon+0.02))
                df_nearby = df_all_wells[mask].copy()

                # Calculate Distances
                def get_dist(row):
                    p = Point(row['SurfaceLongitude'], row['SurfaceLatitude'])
                    if property_poly.contains(p):
                        return 0
                    # Convert degrees to feet (approx)
                    return round(property_poly.distance(p) * 364000, 0)

                if not df_nearby.empty:
                    df_nearby['Dist_to_Prop_ft'] = df_nearby.apply(get_dist, axis=1)
                    
                    # Metrics
                    on_prop = len(df_nearby[df_nearby['Dist_to_Prop_ft'] == 0])
                    nearby = len(df_nearby[df_nearby['Dist_to_Prop_ft'] > 0])
                    
                    col1, col2 = st.columns(2)
                    col1.metric("Wells ON Property", on_prop)
                    col2.metric("Wells Nearby (1mi)", nearby)

                    # Map
                    m = folium.Map(location=[target_lat, target_lon], zoom_start=15)
                    folium.TileLayer(
                        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                        attr='Esri', name='Satellite', overlay=False
                    ).add_to(m)
                    
                    # Draw Property
                    folium.GeoJson(property_poly, name="Property").add_to(m)
                    
                    # Draw Wells
                    for _, row in df_nearby.iterrows():
                        color = 'green' if row['Dist_to_Prop_ft'] == 0 else 'orange'
                        folium.CircleMarker(
                            location=[row['SurfaceLatitude'], row['SurfaceLongitude']],
                            radius=5, color=color, fill=True,
                            popup=f"{row['WellName']} ({row['OperatorName']})"
                        ).add_to(m)
                    
                    folium_static(m)
                    
                    # Table
                    st.subheader("Well Data Table")
                    st.dataframe(df_nearby[['WellName', 'OperatorName', 'Dist_to_Prop_ft', 'TotalDepth']])
                else:
                    st.warning("No wells found within 1.5 miles of this address.")
            else:
                st.error("No well data returned from Enverus for Oklahoma County.")
        else:
            st.error("Could not find address. Please be more specific.")
