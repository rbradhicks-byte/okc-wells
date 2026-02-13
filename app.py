import streamlit as st
import pandas as pd
import folium
from streamlit_folium import folium_static
from geopy.geocoders import ArcGIS
from enverus_developer_api import DirectAccessV2
from shapely.geometry import Point, Polygon
import geopandas as gpd

# 1. PAGE CONFIG & CSS STYLING
st.set_page_config(page_title="OKC Well Discovery", layout="wide")

# CSS to force Navy Blue (#000080) on headers and metrics
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
        
        # Pulling without specific 'fields' to avoid "incorrect field name" API errors
        query_generator = d2.query(
            'well-origins',
            County='OKLAHOMA',
            DeletedDate='null',
            pagesize=10000
        )
        
        data_list = list(query_generator)
        if not data_list:
            return pd.DataFrame()
            
        df = pd.DataFrame(data_list)
        
        # Standardize known variations of Enverus column names
        rename_dict = {
            'Well_Name': 'WellName',
            'Operator_Name': 'OperatorName',
            'Total_Depth': 'TotalDepth',
            'Latitude': 'SurfaceLatitude',
            'Longitude': 'SurfaceLongitude'
        }
        df = df.rename(columns=rename_dict)
        return df
    
    except Exception as e:
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
            
            # Boundary Logic (File or Fallback)
            if uploaded_file:
                gdf_boundary = gpd.read_file(uploaded_file)
                property_poly = gdf_boundary.geometry.iloc[0]
            else:
                # Fallback: 10-acre square (~360ft radius)
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
                # Ensure coordinate columns exist and are numeric
                lat_col = 'SurfaceLatitude' if 'SurfaceLatitude' in df_all.columns else 'Latitude'
                lon_col = 'SurfaceLongitude' if 'SurfaceLongitude' in df_all.columns else 'Longitude'
                
                if lat_col in df_all.columns and lon_col in df_all.columns:
                    df_all[lat_col] = pd.to_numeric(df_all[lat_col], errors='coerce')
                    df_all[lon_col] = pd.to_numeric(df_all[lon_col], errors='coerce')
                    df_all = df_all.dropna(subset=[lat_col, lon_col])

                    # Filter within ~1.5 miles (0.02 degrees)
                    df_nearby = df_all[
                        (df_all[lat_col].between(target_lat-0.02, target_lat+0.02)) & 
                        (df_all[lon_col].between(target_lon-0.02, target_lon+0.02))
                    ].copy()

                    # Distance Math
                    def calc_dist(row):
                        p = Point(row[lon_col], row[lat_col])
                        if property_poly.contains(p): return 0
                        return round(property_poly.distance(p) * 364000, 0) # Degrees to Feet

                    if not df_nearby.empty:
                        df_nearby['Dist_to_Prop_ft'] = df_nearby.apply(calc_dist, axis=1)
                        
                        # Metrics
                        on_prop = len(df_nearby[df_nearby['Dist_to_Prop_ft'] == 0])
                        nearby_count = len(df_nearby[df_nearby['Dist_to_Prop_ft'] > 0])
                        
                        m1, m2 = st.columns(2)
                        m1.metric("Wells ON Property", on_prop)
                        m2.metric("Wells Nearby (1mi)", nearby_count)

                        # Map
                        m = folium.Map(location=[target_lat, target_lon], zoom_start=15)
                        folium.TileLayer(
                            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                            attr='Esri', name='Satellite'
                        ).add_to(m)
                        
                        folium.GeoJson(property_poly, name="Property", style_function=lambda x: {'color':'blue', 'fillOpacity':0.1}).add_to(m)
                        
                        for _, row in df_nearby.iterrows():
                            color = 'green' if row['Dist_to_Prop_ft'] == 0 else 'orange'
                            well_label = row.get('WellName', row.get('Well_Name', 'Unknown Well'))
                            folium.CircleMarker(
                                location=[row[lat_col], row[lon_col]],
                                radius=6, color=color, fill=True,
                                popup=f"Well: {well_label}"
                            ).add_to(m)
                        
                        folium_static(m)
                        
                        # Data Table
                        st.subheader("Nearby Well Details")
                        # Displaying only columns that exist
                        cols_to_show = [c for c in ['WellName', 'OperatorName', 'Dist_to_Prop_ft', 'TotalDepth'] if c in df_nearby.columns]
                        st.dataframe(df_nearby[cols_to_show].sort_values('Dist_to_Prop_ft'))
                    else:
                        st.warning("No wells found in a 1.5-mile radius of this location.")
                else:
                    st.error(f"Required coordinate columns missing. Columns found: {list(df_all.columns)}")
            else:
                st.error("No well data could be retrieved for Oklahoma County.")
        else:
            st.error("Address not found. Please try a more specific address or zip code.")
