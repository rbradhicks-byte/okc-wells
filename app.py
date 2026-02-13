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
st.sidebar.header("Settings")
data_source = st.sidebar.radio("Data Source:", ["Live Enverus API", "Dummy/Test Data"])
uploaded_file = st.sidebar.file_uploader("Upload Property Boundary (.geojson)", type=['geojson'])

# 4. DATA LOGIC
def get_dummy_data(lat, lon):
    data = [
        {"WellName": "Test Well 1H", "OperatorName": "Oklahoma Energy Dev", "SurfaceLatitude": lat + 0.005, "SurfaceLongitude": lon + 0.005, "TotalDepth": 12500},
        {"WellName": "Sample 2-24R", "OperatorName": "Pioneer Natural", "SurfaceLatitude": lat - 0.008, "SurfaceLongitude": lon + 0.002, "TotalDepth": 11800},
        {"WellName": "Wildcat 9", "OperatorName": "Wildcat Exploration", "SurfaceLatitude": lat + 0.002, "SurfaceLongitude": lon - 0.004, "TotalDepth": 9500}
    ]
    return pd.DataFrame(data)

def fetch_live_enverus():
    try:
        creds = st.secrets["enverus"]
        d2 = DirectAccessV2(client_id=creds["client_id"], client_secret=creds["client_secret"], api_key=creds.get("api_key", "NA"))
        
        # Attempt 1: Standard V2 Query
        # We use the most common case-sensitive keywords for Oklahoma
        query = d2.query('well-origins', County='OKLAHOMA', StateProvince='OK', pagesize=1000)
        df = pd.DataFrame(list(query))
        
        # Attempt 2: Fallback to title-case if All-Caps fails
        if df.empty:
            query = d2.query('well-origins', County='Oklahoma', StateProvince='OK', pagesize=1000)
            df = pd.DataFrame(list(query))

        # Diagnostic: If still empty, pull ANY 1 record to see valid column names
        if df.empty:
            st.warning("No wells found for Oklahoma County. Running Diagnostic...")
            diag = list(d2.query('well-origins', pagesize=1))
            if diag:
                st.write("Diagnostic - Valid Columns found in your account:", list(diag[0].keys()))
                st.write("Diagnostic - Sample Data for first record:", diag[0])
            else:
                st.error("Diagnostic failed: Even a broad query returned no data. Check dataset permissions.")
        
        return df
    except Exception as e:
        st.sidebar.error(f"API Connection Error: {e}")
        return pd.DataFrame()

# 5. MAIN LOGIC
if submit_button and raw_address:
    with st.spinner("Analyzing location..."):
        full_address = f"{raw_address}, Oklahoma County, OK"
        geolocator = ArcGIS(user_agent="okc_well_portal")
        location = geolocator.geocode(full_address)

        if location:
            t_lat, t_lon = location.latitude, location.longitude
            offset = 0.001
            property_poly = Polygon([(t_lon-offset, t_lat-offset), (t_lon+offset, t_lat-offset), (t_lon+offset, t_lat+offset), (t_lon-offset, t_lat+offset)])

            if data_source == "Live Enverus API":
                df_all = fetch_live_enverus()
            else:
                df_all = get_dummy_data(t_lat, t_lon)

            if not df_all.empty:
                # Find Column Names Dynamically
                lat_col = next((c for c in df_all.columns if c.lower() in ['surfacelatitude', 'latitude']), None)
                lon_col = next((c for c in df_all.columns if c.lower() in ['surfacelongitude', 'longitude']), None)
                
                if lat_col and lon_col:
                    df_all[lat_col] = pd.to_numeric(df_all[lat_col], errors='coerce')
                    df_all[lon_col] = pd.to_numeric(df_all[lon_col], errors='coerce')
                    df_all = df_all.dropna(subset=[lat_col, lon_col])

                    def calc_dist(row):
                        p = Point(row[lon_col], row[lat_col])
                        if property_poly.contains(p): return 0
                        return round(property_poly.distance(p) * 364000, 0)

                    df_all['Dist_to_Prop_ft'] = df_all.apply(calc_dist, axis=1)
                    # Filter for 2-mile radius
                    df_nearby = df_all[
                        (df_all[lat_col].between(t_lat-0.03, t_lat+0.03)) & 
                        (df_all[lon_col].between(t_lon-0.03, t_lon+0.03))
                    ].copy()

                    c1, c2 = st.columns(2)
                    c1.metric("Wells ON Property", len(df_nearby[df_nearby['Dist_to_Prop_ft'] == 0]))
                    c2.metric("Nearby Wells", len(df_nearby))

                    m = folium.Map(location=[t_lat, t_lon], zoom_start=15)
                    folium.TileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', attr='Esri', name='Satellite').add_to(m)
                    folium.GeoJson(property_poly, name="Property", style_function=lambda x: {'color':'blue', 'fillOpacity':0.1}).add_to(m)
                    
                    name_col = next((c for c in df_nearby.columns if 'name' in c.lower()), df_nearby.columns[0])
                    for _, row in df_nearby.iterrows():
                        color = 'green' if row['Dist_to_Prop_ft'] == 0 else 'orange'
                        folium.CircleMarker(location=[row[lat_col], row[lon_col]], radius=6, color=color, fill=True, popup=f"Well: {row.get(name_col, 'N/A')}").add_to(m)
                    
                    folium_static(m)
                    st.subheader("Well Details")
                    st.dataframe(df_nearby.sort_values('Dist_to_Prop_ft'))
                else:
                    st.error(f"Required coordinates missing. Found columns: {list(df_all.columns)}")
            else:
                st.error("No data returned. If using Live API, look at the Diagnostic warnings above.")
        else:
            st.error("Address not found.")
