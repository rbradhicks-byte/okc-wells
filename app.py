import streamlit as st
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, box
from geopy.geocoders import ArcGIS
import folium
from streamlit_folium import st_folium
from enverus_developer_api import DirectAccessV2

# -----------------------------------------------------------------------------
# 1. CONFIGURATION & STYLING
# -----------------------------------------------------------------------------
st.set_page_config(page_title="OK County Well Discovery", layout="wide")

# CSS Injection: Navy Blue Headers and Metrics (#000080)
NAVY_BLUE = "#000080"
st.markdown(
    f"""
    <style>
    h1, h2, h3, h4, h5, h6 {{
        color: {NAVY_BLUE} !important;
    }}
    div[data-testid="stMetricLabel"] {{
        color: {NAVY_BLUE} !important;
        font-weight: bold;
    }}
    /* Remove padding from form container to align button */
    div[data-testid="stForm"] {{
        border: none;
        padding: 0;
    }}
    .block-container {{
        padding-top: 2rem;
    }}
    </style>
    """,
    unsafe_allow_html=True
)

st.title("🛢️ Oklahoma County Well Discovery Portal")

# -----------------------------------------------------------------------------
# 2. HELPER FUNCTIONS & AUTH
# -----------------------------------------------------------------------------

@st.cache_resource
def get_enverus_client():
    """
    Initialize Enverus client using DirectAccessV2.
    Uses a dummy API key to bypass library validation while strictly using
    OAuth Client Credentials (ID + Secret) for the actual connection.
    """
    try:
        creds = st.secrets["enverus"]
        c_id = creds.get("client_id")
        c_secret = creds.get("client_secret")
        
        # Pass dummy key to satisfy constructor; library will use ID/Secret for token
        c_key = creds.get("api_key", "NOT_REQUIRED")
        
        d2 = DirectAccessV2(client_id=c_id, client_secret=c_secret, api_key=c_key)
        return d2
    except Exception as e:
        st.error(f"Authentication Config Error: {e}")
        return None

@st.cache_data(ttl=3600)
def fetch_nearby_wells(_client, lat, lon, radius_deg=0.05):
    """
    Fetch well-origins using a Spatial Bounding Box (btw) query.
    
    Args:
        lat (float): Latitude of search center.
        lon (float): Longitude of search center.
        radius_deg (float): Approx 0.05 deg is ~3.5 miles (buffer).
    """
    if not _client:
        return pd.DataFrame()

    try:
        # Calculate Bounding Box
        min_lat = lat - radius_deg
        max_lat = lat + radius_deg
        min_lon = lon - radius_deg
        max_lon = lon + radius_deg

        # UPDATED QUERY LOGIC: Spatial Bounding Box
        # We use 'Latitude' and 'Longitude' with the 'btw()' operator.
        # We use 'DeletedDate' (PascalCase) = 'null' to exclude deleted records.
        query_generator = _client.query(
            "well-origins", 
            Latitude=f"btw({min_lat},{max_lat})",
            Longitude=f"btw({min_lon},{max_lon})",
            DeletedDate='null'
        )
        
        # Generator to DataFrame
        df = pd.DataFrame(list(query_generator))
        
        # Ensure we have the coordinate columns
        if not df.empty and 'Latitude' in df.columns and 'Longitude' in df.columns:
            df = df.dropna(subset=['Latitude', 'Longitude'])
            return df
            
        return pd.DataFrame()
    except Exception as e:
        st.error(f"Enverus API Error: {e}")
        return pd.DataFrame()

def get_location_coordinates(address):
    """
    Geocode using ArcGIS (No Google).
    """
    geolocator = ArcGIS()
    full_query = f"{address}, Oklahoma County, OK"
    try:
        location = geolocator.geocode(full_query)
        if location:
            return location.latitude, location.longitude
        return None, None
    except Exception as e:
        st.error(f"Geocoding service error: {e}")
        return None, None

# -----------------------------------------------------------------------------
# 3. SIDEBAR & INPUTS
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("Property Definition")
    uploaded_file = st.file_uploader("Upload Property Boundary (.geojson)", type=["geojson", "json"])
    
    st.info("ℹ️ If no file is uploaded, a 10-acre square will be generated around the search point.")

# -----------------------------------------------------------------------------
# 4. MAIN LAYOUT
# -----------------------------------------------------------------------------

# Search Form Wrapper (Enter Key Support)
# We define the variables outside the form context to access them later
user_address = ""
submit_btn = False

with st.form("search_form"):
    search_col1, search_col2 = st.columns([3, 1])
    with search_col1:
        user_address = st.text_input("Search Location", placeholder="e.g. 320 Robert S Kerr Ave")
    with search_col2:
        submit_btn = st.form_submit_button("Analyze Location", type="primary", use_container_width=True)

if submit_btn and user_address:
    # A. Geocoding
    with st.spinner("Triangulating location via Esri ArcGIS..."):
        lat, lon = get_location_coordinates(user_address)

    if lat is None:
        st.error("Could not locate address within Oklahoma County.")
    else:
        # B. Define Property Geometry (AOI)
        search_point = gpd.GeoDataFrame(
            geometry=[Point(lon, lat)], 
            crs="EPSG:4326"
        )

        aoi_gdf = None
        
        if uploaded_file:
            try:
                aoi_gdf = gpd.read_file(uploaded_file)
                if aoi_gdf.crs != "EPSG:4326":
                    aoi_gdf = aoi_gdf.to_crs("EPSG:4326")
            except Exception as e:
                st.warning(f"Error reading GeoJSON: {e}. Reverting to fallback.")
                uploaded_file = None

        if aoi_gdf is None:
            # Fallback: ~10 acre box (approx +/- 0.002 deg)
            delta = 0.002
            bbox = box(lon - delta, lat - delta, lon + delta, lat + delta)
            aoi_gdf = gpd.GeoDataFrame(geometry=[bbox], crs="EPSG:4326")

        # C. Fetch & Process Data
        client = get_enverus_client()
        
        if client:
            with st.spinner("Fetching Spatial Data from Enverus..."):
                # Pass lat/lon to query function for Bounding Box logic
                wells_df = fetch_nearby_wells(client, lat, lon)

            if not wells_df.empty:
                # UPDATED GEOMETRY: Using 'Longitude' and 'Latitude' columns from result
                wells_gdf = gpd.GeoDataFrame(
                    wells_df,
                    geometry=gpd.points_from_xy(wells_df.Longitude, wells_df.Latitude),
                    crs="EPSG:4326"
                )

                # Spatial calculations in meters (EP
