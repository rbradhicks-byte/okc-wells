import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from geopy.geocoders import ArcGIS
from shapely.geometry import Point, shape, Polygon, box
from shapely.ops import nearest_points
import json
from directaccess import DirectAccessV2

# -------------------------------------------------------------------------
# 1. UI Configuration & CSS Injection
# -------------------------------------------------------------------------
st.set_page_config(
    page_title="OKC Well Discovery Portal",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for Navy Blue headers and high contrast
custom_css = """
<style>
    h1, h2, h3, h4, .stMetricLabel {
        color: #000080 !important; /* Navy Blue */
    }
    .stHeader {
        color: #1E90FF !important; /* Light Blue accent fallback */
    }
    /* Enhance table header visibility */
    th {
        background-color: #000080 !important;
        color: white !important;
    }
</style>
"""
st.markdown(custom_css, unsafe_allow_html=True)

# -------------------------------------------------------------------------
# 2. Helper Functions: Data & Geocoding
# -------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def fetch_enverus_data():
    """
    Connects to Enverus DirectAccessV2 and fetches Oklahoma County wells.
    Uses st.secrets for credentials.
    """
    try:
        # Credential Retrieval
        creds = st.secrets["enverus"]
        
        d2 = DirectAccessV2(
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
            api_key=creds["api_key"]
        )

        # Dataset: well-origins (or 'wells' depending on subscription)
        # Filter: Oklahoma County, OK to optimize performance
        # Fields: Limit fields to reduce payload size
        query_params = {
            'dataset': 'well-origins',
            'query': "County = 'OKLAHOMA' AND State = 'OK'",
            'fields': 'API,WellName,OperatorName,Latitude,Longitude,TotalDepth',
            'pagesize': 10000  # Adjust based on needs
        }

        # Fetch data (returns a generator, convert to list of dicts)
        records = []
        for row in d2.query(**query_params):
            records.append(row)
            # Safety break for demo if dataset is massive
            if len(records) >= 5000: 
                break
        
        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)
        
        # Ensure numeric types
        df['Latitude'] = pd.to_numeric(df['Latitude'], errors='coerce')
        df['Longitude'] = pd.to_numeric(df['Longitude'], errors='coerce')
        df['TotalDepth'] = pd.to_numeric(df['TotalDepth'], errors='coerce')
        
        # Drop rows without valid coordinates
        df = df.dropna(subset=['Latitude', 'Longitude'])
        
        return df

    except Exception as e:
        st.error(f"Enverus Connection Error: {str(e)}")
        return pd.DataFrame()

def get_location_coordinates(address):
    """
    Geocodes address using ArcGIS (Free, No API Key).
    """
    geolocator = ArcGIS()
    try:
        location = geolocator.geocode(address)
        if location:
            return location.latitude, location.longitude
        return None, None
    except Exception:
        return None, None

def calculate_distance_feet(row, target_geom):
    """
    Calculates approx distance in feet from a Well Point to a Target Geometry 
    (Point or Polygon).
    """
    well_point = Point(row['Longitude'], row['Latitude'])
    
    # Shapely distance returns degrees in WGS84
    # We find the nearest point on the target geometry to the well
    p1, p2 = nearest_points(well_point, target_geom)
    
    # Calculate simple Euclidean distance in degrees
    degree_dist = p1.distance(p2)
    
    # Approx conversion: 1 degree lat ~= 364,000 feet
    # This is an approximation suitable for local county scale
    feet_dist = degree_dist * 364000
    
    return int(feet_dist)

# -------------------------------------------------------------------------
# 3. Main Application Logic
# -------------------------------------------------------------------------

st.title("Oklahoma County Well Discovery Portal")

# -- 3a. Search Bar (Top of Main Page) --
search_query = st.text_input("Search Location (Section, Township, or Address)", placeholder="e.g. 123 Main St")

# -- 3b. Sidebar --
st.sidebar.header("Property Configuration")
uploaded_file = st.sidebar.file_uploader("Upload Property Boundary (.geojson)", type=["geojson", "json"])

# -------------------------------------------------------------------------
# 4. Processing & Visualization
# -------------------------------------------------------------------------

if search_query:
    # Append context automatically
    full_address = f"{search_query}, Oklahoma County, OK"
    
    with st.spinner(f"Geocoding '{full_address}'..."):
        lat, lon = get_location_coordinates(full_address)

    if lat and lon:
        st.success(f"Located: {lat:.4f}, {lon:.4f}")
        
        # -- Prepare Target Geometry --
        target_geometry = None
        target_style = None

        if uploaded_file:
            try:
                geo_data = json.load(uploaded_file)
                # Assuming the first feature is the boundary
                features = geo_data.get('features', [])
                if features:
                    target_geometry = shape(features[0]['geometry'])
                    target_style = "polygon"
                else:
                    st.sidebar.warning("Invalid GeoJSON: No features found.")
            except Exception as e:
                st.sidebar.error(f"Error parsing GeoJSON: {e}")
        
        # Fallback: Create a 10-acre box approx (approx 660x660 ft) around center if no polygon
        if not target_geometry:
            # 0.0018 degrees is roughly 660ft
            offset = 0.0009 
            target_geometry = box(lon - offset, lat - offset, lon + offset, lat + offset)
            target_style = "box"
            st.info("Using 10-acre fallback boundary (No GeoJSON uploaded).")

        # -- Fetch Enverus Data --
        with st.spinner("Fetching Real Enverus Data..."):
            df_wells = fetch_enverus_data()

        if not df_wells.empty:
            # -- Distance Logic --
            # Calculate distance from every well to the target geometry
            df_wells['Distance_ft'] = df_wells.apply(lambda row: calculate_distance_feet(row, target_geometry), axis=1)
            
            # Filter: Show wells within reasonable range (e.g., 2 miles = ~10560 ft) for relevance
            # or just sort by distance
            df_display = df_wells.sort_values(by='Distance_ft').head(50).copy()

            # -- Map Construction --
            m = folium.Map(
                location=[lat, lon], 
                zoom_start=15,
                tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                attr='Esri World Imagery'
            )

            # Draw Target Boundary
            if target_style == "polygon" or target_style == "box":
                # Convert shapely geom to geojson for folium
                import shapely.geometry
                gjson = shapely.geometry.mapping(target_geometry)
                
                folium.GeoJson(
                    gjson,
                    name="Property Boundary",
                    style_function=lambda x: {'fillColor': '#000080', 'color': '#1E90FF', 'weight': 2, 'fillOpacity': 0.2}
                ).add_to(m)

            # Draw Search Center Marker
            folium.Marker(
                [lat, lon],
                popup="Search Center",
                icon=folium.Icon(color="red", icon="info-sign")
            ).add_to(m)

            # Draw Wells
            for _, row in df_display.iterrows():
                # Color code based on distance (Green close, Red far)
                color = 'green' if row['Distance_ft'] < 1000 else 'orange' if row['Distance_ft'] < 5000 else 'blue'
                
                folium.CircleMarker(
                    location=[row['Latitude'], row['Longitude']],
                    radius=5,
                    popup=f"<b>{row['WellName']}</b><br>Op: {row['OperatorName']}<br>Dist: {row['Distance_ft']} ft",
                    color=color,
                    fill=True,
                    fill_opacity=0.7
                ).add_to(m)

            # Render Map
            st_folium(m, width="100%", height=500)

            # -- Data Table --
            st.subheader("Nearby Wells (Enverus Data)")
            
            # Formatting for display
            display_cols = ['WellName', 'OperatorName', 'API', 'TotalDepth', 'Distance_ft']
            
            # Gradient styling for Distance column
            st.dataframe(
                df_display[display_cols].style.background_gradient(subset=['Distance_ft'], cmap="Blues"),
                use_container_width=True
            )

        else:
            st.warning("No Wells found in Oklahoma County via Enverus Connection.")

    else:
        st.error("Could not geocode location. Please try a different query.")

else:
    # Initial State
    st.info("Enter a location above to begin discovery.")
