import streamlit as st
import folium
from streamlit_folium import st_folium
from folium.plugins import MeasureControl
from geopy.geocoders import ArcGIS
from shapely.geometry import shape, Point, box, mapping
import requests
import pandas as pd
import json

# -----------------------------------------------------------------------------
# 1. CONFIGURATION & PAGE SETUP
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="OKC Well Proximity Portal",
    page_icon="🛢️",
    layout="wide"
)

# Custom CSS
st.markdown("""
    <style>
    .stApp { background-color: #f0f2f6; }
    div[data-testid="stMetricValue"] { font-size: 24px; color: #004e8c; }
    </style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 2. GEOSPATIAL UTILITIES
# -----------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def get_lat_long_arcgis(address):
    """
    Geocodes address using Esri ArcGIS.
    More reliable on Cloud IPs than Nominatim.
    """
    try:
        # User-Agent defined as requested
        geolocator = ArcGIS(user_agent="okc_discovery_portal_v1", timeout=5)
        location = geolocator.geocode(address)
        if location:
            return location.latitude, location.longitude
        return None, None
    except Exception as e:
        # Return None to trigger manual fallback in UI
        return None, None

def create_fallback_boundary(lat, lon):
    """Creates a ~10 acre square box around the center point."""
    delta = 0.002
    minx, miny = lon - delta, lat - delta
    maxx, maxy = lon + delta, lat + delta
    return box(minx, miny, maxx, maxy)

def calculate_distance_feet(point_geom, poly_geom):
    """Calculates distance (0 if inside). 1 Degree approx 364k ft."""
    if poly_geom.contains(point_geom):
        return 0.0
    dist_degrees = poly_geom.distance(point_geom)
    feet_per_degree = 364000 
    return dist_degrees * feet_per_degree

# -----------------------------------------------------------------------------
# 3. ENVERUS DATA INTEGRATION
# -----------------------------------------------------------------------------

def get_enverus_token():
    try:
        if "enverus" not in st.secrets:
            return "MOCK"
            
        url = "https://api.enverus.com/v3/direct-access/tokens"
        payload = {
            "grantType": "client_credentials",
            "clientId": st.secrets["enverus"]["client_id"],
            "clientSecret": st.secrets["enverus"]["client_secret"],
            "scope": "limited"
        }
        headers = {"Content-Type": "application/json"}
        response = requests.post(url, json=payload, headers=headers, timeout=5)
        
        if response.status_code == 200:
            return response.json().get('token')
        return "MOCK"
    except:
        return "MOCK"

@st.cache_data(ttl=3600)
def fetch_wells_nearby(lat, lon):
    """Fetches wells from Enverus or generates Mock Data."""
    token = get_enverus_token()
    
    # MOCK DATA (If API fails or no secrets)
    if token == "MOCK" or not token:
        import random
        mock_data = []
        for i in range(5):
            r_lat = lat + random.uniform(-0.01, 0.01)
            r_lon = lon + random.uniform(-0.01, 0.01)
            mock_data.append({
                "WellName": f"MOCK WELL {i+1}H",
                "Operator": "OKLAHOMA ENERGY DEV",
                "API": f"350001234{i}",
                "Latitude": r_lat,
                "Longitude": r_lon,
                "Status": "ACTIVE"
            })
        return pd.DataFrame(mock_data)

    # REAL API CALL
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    offset = 0.02
    query = {
        "dataset": "wells",
        "query": {
            "and": [
                {"greaterThan": ["Latitude", lat - offset]},
                {"lessThan": ["Latitude", lat + offset]},
                {"greaterThan": ["Longitude", lon - offset]},
                {"lessThan": ["Longitude", lon + offset]},
                {"in": ["WellStatus", ["ACTIVE", "COMPLETED", "DRILLING"]]} 
            ]
        },
        "fields": ["API", "WellName", "OperatorAlias", "Latitude", "Longitude", "WellStatus"],
        "pageSize": 100
    }
    
    try:
        url = "https://api.enverus.com/v3/direct-access/query"
        resp = requests.post(url, headers=headers, json=query, timeout=10)
        if resp.status_code == 200:
            df = pd.DataFrame(resp.json())
            return df.rename(columns={"OperatorAlias": "Operator", "WellStatus": "Status"})
        return pd.DataFrame()
    except:
        return pd.DataFrame()

# -----------------------------------------------------------------------------
# 4. MAIN UI LOGIC
# -----------------------------------------------------------------------------

def main():
    st.sidebar.title("🛢️ Discovery Portal")
    
    # --- INPUTS ---
    address_input = st.sidebar.text_input("Target Address:", "2000 N Classen Blvd, Oklahoma City, OK")
    
    # Try Geocoding
    geo_lat, geo_lon = get_lat_long_arcgis(address_input)
    
    final_lat = None
    final_lon = None
    
    # LOGIC: If geocoding worked, use it. If failed, show manual inputs.
    if geo_lat is not None:
        final_lat = geo_lat
        final_lon = geo_lon
    else:
        st.sidebar.error("⚠️ Geocoding failed or address not found.")
        st.sidebar.markdown("**Enter Coordinates Manually:**")
        # Defaults set to OKC Center so map renders somewhere meaningful
        final_lat = st.sidebar.number_input("Latitude", value=35.4676, format="%.5f")
        final_lon = st.sidebar.number_input("Longitude", value=-97.5164, format="%.5f")

    uploaded_file = st.sidebar.file_uploader("Upload Property Boundary (.geojson)", type=["geojson", "json"])
    
    # --- PROCESSING ---
    
    # 1. Define Boundary
    if uploaded_file:
        try:
            geo_data = json.load(uploaded_file)
            features = geo_data.get('features', [])
            if features:
                boundary_poly = shape(features[0]['geometry'])
                boundary_geojson = features[0]['geometry']
            else:
                boundary_poly = create_fallback_boundary(final_lat, final_lon)
                boundary_geojson = mapping(boundary_poly)
        except Exception as e:
            st.error(f"Error reading file: {e}")
            boundary_poly = create_fallback_boundary(final_lat, final_lon)
            boundary_geojson = mapping(boundary_poly)
    else:
        boundary_poly = create_fallback_boundary(final_lat, final_lon)
        boundary_geojson = mapping(boundary_poly)

    # 2. Fetch Data
    df_wells = fetch_wells_nearby(final_lat, final_lon)
    
    # 3. Math
    if not df_wells.empty:
        df_wells['geometry'] = df_wells.apply(lambda x: Point(x['Longitude'], x['Latitude']), axis=1)
        df_wells['Dist_to_Prop_ft'] = df_wells['geometry'].apply(lambda x: calculate_distance_feet(x, boundary_poly)).astype(int)
        df_wells = df_wells.sort_values('Dist_to_Prop_ft')

    # --- MAP VISUALIZATION ---
    
    m = folium.Map(location=[final_lat, final_lon], zoom_start=15, tiles=None)

    # Esri World Imagery (Satellite)
    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attr='Esri',
        name='Esri Satellite',
        overlay=False,
        control=True
    ).add_to(m)

    # Esri Labels (Roads/Reference)
    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
        attr='Esri',
        name='Esri Labels',
        overlay=True,
        control=True
    ).add_to(m)

    # Draw Boundary
    folium.GeoJson(
        boundary_geojson,
        name="Property Boundary",
        style_function=lambda x: {'fillColor': '#ffaf00', 'color': '#ffaf00', 'weight': 2, 'fillOpacity': 0.2}
    ).add_to(m)

    # Draw Center Marker
    folium.Marker(
        [final_lat, final_lon], 
        popup="Target Location",
        icon=folium.Icon(color="red", icon="home")
    ).add_to(m)

    # Draw Wells
    if not df_wells.empty:
        for _, row in df_wells.iterrows():
            color = "green" if row['Dist_to_Prop_ft'] == 0 else "blue"
            folium.CircleMarker(
                location=[row['Latitude'], row['Longitude']],
                radius=6,
                color="white",
                weight=1,
                fill=True,
                fill_color=color,
                fill_opacity=1,
                popup=folium.Popup(f"<b>{row['WellName']}</b><br>Dist: {row['Dist_to_Prop_ft']} ft", max_width=250)
            ).add_to(m)

    folium.LayerControl().add_to(m)
    MeasureControl(position='bottomleft').add_to(m)

    # --- DASHBOARD LAYOUT ---
    st.title("Oklahoma Well Proximity Portal")
    
    col1, col2 = st.columns([3, 1])
    
    with col1:
        st_folium(m, height=600, width=None)
    
    with col2:
        st.subheader("Analysis")
        count_on = len(df_wells[df_wells['Dist_to_Prop_ft'] == 0])
        st.metric("Wells ON Property", count_on)
        st.metric("Wells Nearby (1mi)", len(df_wells))
        
        if not df_wells.empty:
            nearest = df_wells.iloc[0]
            st.info(f"**Nearest Well:**\n\n{nearest['WellName']}\n\n{nearest['Dist_to_Prop_ft']} ft away")

    st.subheader("Well Data Table")
    if not df_wells.empty:
        display_cols = ['WellName', 'Operator', 'API', 'Status', 'Dist_to_Prop_ft']
        st.dataframe(df_wells[display_cols].style.background_gradient(subset=['Dist_to_Prop_ft'], cmap="RdYlGn_r"), use_container_width=True)

if __name__ == "__main__":
    main()
