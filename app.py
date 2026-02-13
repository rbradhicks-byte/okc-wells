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

# 4. DATA FETCHING (Interrogative Logic)
def fetch_enverus_data():
    try:
        creds = st.secrets["enverus"]
        d2 = DirectAccessV2(
            client_id=creds["client_id"], 
            client_secret=creds["client_secret"], 
            api_key=creds.get("api_key", "NA")
        )
        
        # Variation 1: All Caps (Standard)
        results = list(d2.query('well-origins', county='OKLAHOMA', pagesize=10000))
        
        # Variation 2: Title Case
        if not results:
            results = list(d2.query('well-origins', county='Oklahoma', pagesize=10000))
            
        # Variation 3: No county filter (Permission Check)
        if not results:
            st.warning("No wells found for Oklahoma County. Testing API permissions...")
            results = list(d2.query('well-origins', pagesize=5))
            if results:
                st.info(f"API is working, but Oklahoma County returned nothing. Available counties in your data include: {results[0].get('County')}")
                return pd.DataFrame()

        return pd.DataFrame(results)
    
    except Exception as e:
        st.error(f"Enverus API Error: {e}")
        return pd.DataFrame()

# 5. MAIN LOGIC
if submit_button and raw_address:
    with st.spinner("Analyzing location and querying Enverus..."):
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

            if not df_all.empty:
                # Coordinate Identification
                lat_col = next((c for c in df_all.columns if c.lower() in ['surfacelatitude', 'latitude']), None)
                lon_col = next((c for c in df_all.columns if c.lower() in ['surfacelongitude', 'longitude']), None)
                
                if lat_col and lon_col:
                    df_all[lat_col] = pd.to_numeric(df_all[lat_col], errors='coerce')
                    df_all[lon_col] = pd.to_numeric(df_all[lon_col], errors='coerce')
                    df_all = df_all.dropna(subset=[lat_col, lon_col])

                    # Local Filter (1.5 miles)
                    df_nearby = df_all[
                        (df_all[lat_col].between(target_lat-0.02, target_lat+0.02)) & 
                        (df_all[lon_col].between(target_lon-0.02, target_lon+0.02))
                    ].copy()

                    if not df_nearby.empty:
                        def calc_dist(row):
                            p = Point(row[lon_col], row[lat_col])
                            if property_poly.contains(p): return 0
                            return round(property_poly.distance(p) * 364000, 0)

                        df_nearby['Dist_to_Prop_ft'] = df_nearby.apply(calc_dist, axis=1)
                        
                        m1, m2 = st.columns(2)
                        m1.metric("Wells ON Property", len(df_nearby[df_nearby['Dist_to_Prop_ft'] == 0]))
                        m2.metric("Nearby Wells", len(df_nearby[df_nearby['Dist_to_Prop_ft'] > 0]))

                        m = folium.Map(location=[target_lat, target_lon], zoom_start=15)
                        folium.TileLayer(
                            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                            attr='Esri', name='Satellite'
                        ).add_to(m)
                        
                        folium.GeoJson(property_poly, name="Property", style_function=lambda x: {'color':'blue', 'fillOpacity':0.1}).add_to(m)
                        
                        name_col = next((c for c in df_nearby.columns if 'name' in c.lower()), None)
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
                        st.warning("No wells found within a 1.5-mile radius.")
                else:
                    st.error(f"Coordinates missing. Columns found: {list(df_all.columns)}")
            else:
                st.error("The API request was successful but returned 0 records. This usually means the 'well-origins' dataset is not populated for Oklahoma County in your account subscription.")
        else:
            st.error("Address not found.")
