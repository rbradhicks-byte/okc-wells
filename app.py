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

# 4. DATA FETCHING (Spatial Bounding Box)
def fetch_nearby_wells(lat, lon):
    try:
        creds = st.secrets["enverus"]
        d2 = DirectAccessV2(
            client_id=creds["client_id"], 
            client_secret=creds["client_secret"], 
            api_key=creds.get("api_key", "NA")
        )
        
        # Define a ~2-mile box (+/- 0.03 degrees)
        # We use the 'btw' (between) function which is native to Enverus V2
        query_generator = d2.query(
            'well-origins',
            SurfaceLatitude=f'btw({lat-0.03}, {lat+0.03})',
            SurfaceLongitude=f'btw({lon-0.03}, {lon+0.03})',
            DeletedDate='null',
            pagesize=1000
        )
        
        results = list(query_generator)
        return pd.DataFrame(results)
    
    except Exception as e:
        st.error(f"Enverus Spatial Query Error: {e}")
        return pd.DataFrame()

# 5. MAIN LOGIC
if submit_button and raw_address:
    with st.spinner("Geocoding address and querying Enverus spatially..."):
        full_address = f"{raw_address}, Oklahoma County, OK"
        geolocator = ArcGIS(user_agent="okc_well_portal")
        location = geolocator.geocode(full_address)

        if location:
            target_lat, target_lon = location.latitude, location.longitude
            
            # Create a fallback 10-acre property polygon
            offset = 0.001
            property_poly = Polygon([
                (target_lon-offset, target_lat-offset),
                (target_lon+offset, target_lat-offset),
                (target_lon+offset, target_lat+offset),
                (target_lon-offset, target_lat+offset)
            ])

            # Fetch Data using coordinates
            df_nearby = fetch_nearby_wells(target_lat, target_lon)

            if not df_nearby.empty:
                # Find column names (API returns PascalCase usually)
                lat_col = next((c for c in df_nearby.columns if 'lat' in c.lower()), None)
                lon_col = next((c for c in df_nearby.columns if 'lon' in c.lower()), None)
                
                if lat_col and lon_col:
                    df_nearby[lat_col] = pd.to_numeric(df_nearby[lat_col], errors='coerce')
                    df_nearby[lon_col] = pd.to_numeric(df_nearby[lon_col], errors='coerce')
                    df_nearby = df_nearby.dropna(subset=[lat_col, lon_col])

                    def calc_dist(row):
                        p = Point(row[lon_col], row[lat_col])
                        if property_poly.contains(p): return 0
                        return round(property_poly.distance(p) * 364000, 0)

                    df_nearby['Dist_to_Prop_ft'] = df_nearby.apply(calc_dist, axis=1)
                    
                    # Metrics
                    m1, m2 = st.columns(2)
                    m1.metric("Wells ON Property", len(df_nearby[df_nearby['Dist_to_Prop_ft'] == 0]))
                    m2.metric("Nearby Wells (2mi)", len(df_nearby))

                    # Map
                    m = folium.Map(location=[target_lat, target_lon], zoom_start=14)
                    folium.TileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', attr='Esri', name='Satellite').add_to(m)
                    
                    folium.GeoJson(property_poly, name="Property", style_function=lambda x: {'color':'blue', 'fillOpacity':0.1}).add_to(m)
                    
                    name_col = next((c for c in df_nearby.columns if 'name' in c.lower()), 'WellName')
                    for _, row in df_nearby.iterrows():
                        color = 'green' if row['Dist_to_Prop_ft'] == 0 else 'orange'
                        folium.CircleMarker(
                            location=[row[lat_col], row[lon_col]],
                            radius=6, color=color, fill=True,
                            popup=f"Well: {row.get(name_col, 'N/A')}"
                        ).add_to(m)
                    
                    folium_static(m)
                    st.subheader("Nearby Well Details")
                    st.dataframe(df_nearby.sort_values('Dist_to_Prop_ft'))
                else:
                    st.error("No coordinate columns found in the API response.")
            else:
                st.warning(f"No wells found within 2 miles of {target_lat}, {target_lon}. This may indicate a dataset permission issue or a lack of wells in this specific area.")
        else:
            st.error("Address not found.")
