import streamlit as st
import folium
from streamlit_folium import st_folium
from folium.plugins import MeasureControl
from geopy.geocoders import ArcGIS
from shapely.geometry import shape, Point, box, mapping
import pandas as pd
import json

# Import Enverus Library
try:
    from enverus_developer_api import DirectAccess
except ImportError:
    st.error("Missing Library: Please run `pip install enverus-developer-api`")
    st.stop()

# -----------------------------------------------------------------------------
# 1. CONFIGURATION & THEME
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="OKC Well Discovery Portal",
    page_icon="🛢️",
    layout="wide"
)

# PRODUCTION CSS: Navy (#000080) & Sky Blue (#1E90FF) Theme
st.markdown("""
    <style>
    /* Global Background */
    .stApp { background-color: #f4f6f9; }
    
    /* Headers - Force Navy Blue */
    h1, h2, h3, h4, h5 {
        color: #000080 !important;
        font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
        font-weight: 800 !important;
    }
    
    /* Metrics - The Label (e.g., 'Wells ON Property') */
    div[data-testid="stMetricLabel"] {
        color: #000080 !important; 
        font-size: 16px !important;
        font-weight: 700 !important;
    }
    
    /* Metrics - The Value (e.g., '12') */
    div[data-testid="stMetricValue"] {
        color: #1E90FF !important;
        font-size: 32px !important;
        font-weight: 700 !important;
    }
    
    /* Sidebar Styling */
    section[data-testid="stSidebar"] {
        background-color: #e6eaee;
    }
    
    /* Buttons */
    div.stButton > button {
        background-color: #000080;
        color: white;
        border-radius: 8px;
    }
    </style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 2. GEOSPATIAL UTILITIES
# -----------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def get_lat_long_arcgis(address_str):
    """
    Geocodes using Esri ArcGIS.
    Automatically handles errors to prevent app crashes.
    """
    try:
        geolocator = ArcGIS(user_agent="okc_discovery_portal_prod", timeout=5)
        location = geolocator.geocode(address_str)
        if location:
            return location.latitude, location.longitude
        return None, None
    except Exception:
        return None, None

def create_fallback_boundary(lat, lon):
    """Creates a ~10 acre box (approx 200m x 200m) around the point."""
    delta = 0.002
    minx, miny = lon - delta, lat - delta
    maxx, maxy = lon + delta, lat + delta
    return box(minx, miny, maxx, maxy)

def calculate_distance_feet(point_geom, poly_geom):
    """Calculates distance in feet (0 if inside)."""
    if poly_geom.contains(point_geom):
        return 0.0
    # Approx conversion at OK latitudes (35N)
    # 1 deg lat ~= 364,000 ft
    return poly_geom.distance(point_geom) * 364000 

# -----------------------------------------------------------------------------
# 3. ENVERUS DATA INTEGRATION (REAL API)
# -----------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def fetch_wells_enverus(center_lat, center_lon, radius_miles=2):
    """
    Queries Enverus DirectAccess (well-origins).
    Filters: State=OK, County=OKLAHOMA.
    Optimization: Adds a Bounding Box filter to prevent fetching 50k wells.
    """
    # 1. Check Credentials
    if "enverus" not in st.secrets:
        st.error("Enverus credentials missing in .streamlit/secrets.toml")
        return pd.DataFrame()

    try:
        # 2. Initialize DirectAccess Library
        da = DirectAccess(
            client_id=st.secrets["enverus"]["client_id"],
            client_secret=st.secrets["enverus"]["client_secret"]
        )

        # 3. Create Spatial Filter (Bounding Box) for Performance
        # 1 mile is approx 0.0145 degrees
        offset = radius_miles * 0.0145
        min_lat, max_lat = center_lat - offset, center_lat + offset
        min_lon, max_lon = center_lon - offset, center_lon + offset

        # 4. Construct Query
        # We look for wells in Oklahoma County AND within our visual box
        query = {
            "and": [
                {"eq": ["StateProvince", "OK"]},
                {"eq": ["County", "OKLAHOMA"]},
                {"between": ["Latitude", min_lat, max_lat]},
                {"between": ["Longitude", min_lon, max_lon]}
            ]
        }
        
        # 5. Execute Query
        # Using 'well-origins' dataset as requested
        records = da.query(
            dataset="well-origins",
            query=query,
            fields=["WellName", "OperatorName", "ApiNumber", "TotalDepth", "Latitude", "Longitude"]
        )
        
        if not records:
            return pd.DataFrame()

        # 6. Convert to DataFrame
        df = pd.DataFrame(records)
        
        # Normalize Column Names for UI
        df = df.rename(columns={
            "ApiNumber": "API",
            "OperatorName": "Operator"
        })
        
        # Ensure Numeric Types
        df['Latitude'] = pd.to_numeric(df['Latitude'], errors='coerce')
        df['Longitude'] = pd.to_numeric(df['Longitude'], errors='coerce')
        df = df.dropna(subset=['Latitude', 'Longitude'])
        
        return df

    except Exception as e:
        st.error(f"Enverus API Error: {str(e)}")
        return pd.DataFrame()

# -----------------------------------------------------------------------------
# 4. MAIN APP LOGIC
# -----------------------------------------------------------------------------

def main():
    # --- TOP NAV: SEARCH ---
    st.title("🛢️ Oklahoma Well Discovery Portal")

    col_search, _ = st.columns([2, 1])
    with col_search:
        raw_address = st.text_input("📍 Search Address (e.g. '2000 N Classen Blvd'):", "2000 N Classen Blvd")
        
        # SMART SEARCH: Auto-append Context
        search_string = f"{raw_address}, Oklahoma County, OK"
        st.caption(f"Searching: *{search_string}*")

    # --- SIDEBAR: CONTROLS ---
    st.sidebar.header("⚙️ Settings")
    
    uploaded_file = st.sidebar.file_uploader("Upload Boundary (.geojson)", type=["geojson", "json"])
    
    # --- PROCESSING ---
    
    # 1. Geocoding
    geo_lat, geo_lon = get_lat_long_arcgis(search_string)
    
    final_lat, final_lon = None, None
    
    if geo_lat:
        final_lat, final_lon = geo_lat, geo_lon
    else:
        st.warning(f"Could not find address: '{search_string}'. Using manual fallback.")
        st.sidebar.markdown("---")
        st.sidebar.warning("⚠️ Manual Coordinates Enabled")
        final_lat = st.sidebar.number_input("Latitude", 35.4676, format="%.5f")
        final_lon = st.sidebar.number_input("Longitude", -97.5164, format="%.5f")

    # 2. Boundary Logic
    if uploaded_file:
        try:
            data = json.load(uploaded_file)
            feats = data.get('features', [])
            if feats:
                boundary_poly = shape(feats[0]['geometry'])
                boundary_geojson = feats[0]['geometry']
            else:
                boundary_poly = create_fallback_boundary(final_lat, final_lon)
                boundary_geojson = mapping(boundary_poly)
        except:
            st.sidebar.error("Invalid GeoJSON file.")
            boundary_poly = create_fallback_boundary(final_lat, final_lon)
            boundary_geojson = mapping(boundary_poly)
    else:
        boundary_poly = create_fallback_boundary(final_lat, final_lon)
        boundary_geojson = mapping(boundary_poly)

    # 3. Fetch Real Data
    with st.spinner("Querying Enverus DirectAccess..."):
        df_wells = fetch_wells_enverus(final_lat, final_lon)

    # 4. Calculate Distances
    if not df_wells.empty:
        # Create geometry column for calculation
        df_wells['geometry'] = df_wells.apply(lambda x: Point(x['Longitude'], x['Latitude']), axis=1)
        # Calculate distance
        df_wells['Dist_ft'] = df_wells['geometry'].apply(lambda x: calculate_distance_feet(x, boundary_poly)).astype(int)
        # Sort
        df_wells = df_wells.sort_values('Dist_ft')

    # --- VISUALIZATION ---
    st.markdown("---")
    
    # Layout: Map (Left) vs Stats (Right)
    col_map, col_stats = st.columns([3, 1])
    
    with col_map:
        m = folium.Map(location=[final_lat, final_lon], zoom_start=15, tiles=None)
        
        # Esri Satellite
        folium.TileLayer(
            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            attr='Esri', name='Esri Satellite', overlay=False, control=True
        ).add_to(m)
        
        # Esri Roads Overlay (Essential for context)
        folium.TileLayer(
            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
            attr='Esri', name='Labels', overlay=True, control=True
        ).add_to(m)

        # Property Boundary
        folium.GeoJson(
            boundary_geojson,
            name="Property Boundary",
            style_function=lambda x: {'fillColor': '#ffaf00', 'color': '#ffaf00', 'weight': 3, 'fillOpacity': 0.15}
        ).add_to(m)

        # Wells
        if not df_wells.empty:
            for _, row in df_wells.iterrows():
                # Green if inside (dist=0), Blue if outside
                color = "#00ff00" if row['Dist_ft'] == 0 else "#1E90FF"
                
                folium.CircleMarker(
                    location=[row['Latitude'], row['Longitude']],
                    radius=6,
                    color="white", weight=1,
                    fill=True, fill_color=color, fill_opacity=0.9,
                    popup=folium.Popup(f"<b>{row['WellName']}</b><br>Op: {row['Operator']}<br>Dist: {row['Dist_ft']} ft", max_width=250)
                ).add_to(m)

        folium.LayerControl().add_to(m)
        MeasureControl(position='bottomleft').add_to(m)
        st_folium(m, height=600, width=None)

    with col_stats:
        st.subheader("Analysis Results")
        
        count_on = 0
        count_near = 0
        nearest_txt = "N/A"
        nearest_dist = 0
        
        if not df_wells.empty:
            count_on = len(df_wells[df_wells['Dist_ft'] == 0])
            count_near = len(df_wells)
            nearest_row = df_wells.iloc[0]
            nearest_txt = nearest_row['WellName']
            nearest_dist = nearest_row['Dist_ft']

        st.metric("Wells ON Property", count_on)
        st.metric("Wells Nearby (View)", count_near)
        
        st.markdown("---")
        st.markdown(f"**Nearest Well:**")
        st.info(f"{nearest_txt}")
        st.markdown(f"**Distance:** {nearest_dist} ft")

    # --- DATA TABLE ---
    st.subheader("Detailed Well List")
    
    if not df_wells.empty:
        display_cols = ['WellName', 'Operator', 'API', 'TotalDepth', 'Dist_ft']
        
        # Robust Styling (Try/Except)
        try:
            st.dataframe(
                df_wells[display_cols].style.background_gradient(subset=['Dist_ft'], cmap="Blues"),
                use_container_width=True
            )
        except Exception:
            st.dataframe(df_wells[display_cols], use_container_width=True)
    else:
        st.info("No wells found in this area (Enverus Data).")

if __name__ == "__main__":
    main()
