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

# CSS Injection: Navy Blue Headers (#000080)
NAVY_BLUE = "#000080"
st.markdown(
    f"""
    <style>
    /* Force Headers to Navy Blue */
    h1, h2, h3, h4, h5, h6 {{
        color: {NAVY_BLUE} !important;
    }}
    
    /* Force Metric Labels to Navy Blue */
    div[data-testid="stMetricLabel"] label, 
    div[data-testid="stMetricLabel"] p,
    div[data-testid="stMetricLabel"] div {{
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
        return None

@st.cache_data(ttl=3600)
def fetch_ok_county_raw(_client):
    """
    STRATEGY: The 'Safe' Fetch.
    1. Filter ONLY by County and DeletedDate (API always accepts this).
    2. Select specific columns to reduce payload size.
    3. Cache this result heavily (1 hour) so subsequent searches are instant.
    """
    if not _client:
        return pd.DataFrame()

    try:
        # Requesting 10k pagesize for efficiency
        # We request standard fields. 
        # Note: API might return them as 'Latitude'/'Longitude' depending on version defaults.
        query_generator = _client.query(
            "well-origins", 
            County='OKLAHOMA', 
            DeletedDate='null',
            fields='WellName,OperatorName,API_UWI_14,TotalDepth,SurfaceLatitude,SurfaceLongitude',
            pagesize=10000
        )
        
        df = pd.DataFrame(list(query_generator))
        
        if df.empty:
            return df

        # NORMALIZE COLUMNS: Ensure we have SurfaceLatitude/SurfaceLongitude
        # Sometimes V2 returns 'Latitude' instead of 'SurfaceLatitude' in the dict keys
        rename_map = {}
        if 'Latitude' in df.columns: rename_map['Latitude'] = 'SurfaceLatitude'
        if 'Longitude' in df.columns: rename_map['Longitude'] = 'SurfaceLongitude'
        if rename_map:
            df = df.rename(columns=rename_map)

        # Validate and Typecast
        if 'SurfaceLatitude' in df.columns and 'SurfaceLongitude' in df.columns:
            df = df.dropna(subset=['SurfaceLatitude', 'SurfaceLongitude'])
            df['SurfaceLatitude'] = pd.to_numeric(df['SurfaceLatitude'], errors='coerce')
            df['SurfaceLongitude'] = pd.to_numeric(df['SurfaceLongitude'], errors='coerce')
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
client = get_enverus_client()

with st.sidebar:
    st.header("System Status")
    if client:
        st.success("✅ Enverus API: Connected")
    else:
        st.error("❌ Enverus API: Disconnected (Check Secrets)")

    st.divider()
    
    st.header("Property Definition")
    uploaded_file = st.file_uploader("Upload Property Boundary (.geojson)", type=["geojson", "json"])
    
    st.info("ℹ️ If no file is uploaded, a 10-acre square will be generated around the search point.")

# -----------------------------------------------------------------------------
# 4. MAIN LAYOUT
# -----------------------------------------------------------------------------

# Search Form Wrapper (Enter Key Support)
user_address_input = ""
submit_btn = False

with st.form("search_form"):
    search_col1, search_col2 = st.columns([3, 1])
    with search_col1:
        user_address_input = st.text_input("Search Location", placeholder="e.g. 320 Robert S Kerr Ave")
    with search_col2:
        submit_btn = st.form_submit_button("Analyze Location", type="primary", use_container_width=True)

if submit_btn and user_address_input:
    # A. Geocoding
    lat, lon = None, None
    with st.spinner("Triangulating location via Esri ArcGIS..."):
        lat, lon = get_location_coordinates(user_address_input)

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
        if client:
            with st.spinner("Fetching County Data & Filtering Spatially..."):
                # 1. Fetch RAW county data (Cached)
                full_county_df = fetch_ok_county_raw(client)
            
            if not full_county_df.empty:
                # 2. PYTHON FILTERING (The Safe Method)
                # Filter to +/- 0.03 degrees (~2 miles)
                # This prevents "Invalid Column" API errors
                offset = 0.03
                wells_df = full_county_df[
                    (full_county_df['SurfaceLatitude'].between(lat - offset, lat + offset)) & 
                    (full_county_df['SurfaceLongitude'].between(lon - offset, lon + offset))
                ].copy()

                if not wells_df.empty:
                    # Create GeoDataFrame
                    wells_gdf = gpd.GeoDataFrame(
                        wells_df,
                        geometry=gpd.points_from_xy(wells_df.SurfaceLongitude, wells_df.SurfaceLatitude),
                        crs="EPSG:4326"
                    )

                    # Spatial calculations in meters (EPSG:32124 Oklahoma North)
                    projected_aoi = aoi_gdf.to_crs("EPSG:32124")
                    projected_wells = wells_gdf.to_crs("EPSG:32124")
                    
                    # Calculate distance from property boundary
                    projected_wells['Distance_ft'] = projected_wells.geometry.apply(
                        lambda x: projected_aoi.distance(x)
                    ) * 3.28084 # Convert Meters to Feet

                    # Back to WGS84 for display
                    display_wells = projected_wells.to_crs("EPSG:4326")
                    
                    # Categorize: ON Property vs Nearby
                    on_property = display_wells[display_wells['Distance_ft'] == 0]
                    nearby = display_wells[display_wells['Distance_ft'] > 0].sort_values('Distance_ft').head(50)

                    # Combine for map display
                    all_display = pd.concat([on_property, nearby])

                    # ---------------------------------------------------------
                    # 5. UI OUTPUTS
                    # ---------------------------------------------------------
                    
                    # Metrics
                    m1, m2, m3 = st.columns(3)
                    m1.metric("Search Context", f"{lat:.4f}, {lon:.4f}")
                    m2.metric("Wells ON Property", len(on_property))
                    
                    closest_val = nearby['Distance_ft'].min() if not nearby.empty else 0
                    m3.metric("Closest Offset Well", f"{closest_val:,.0f} ft")

                    col_map, col_data = st.columns([1, 1])

                    # --- MAP GENERATION ---
                    with col_map:
                        st.subheader("Satellite Reconnaissance")
                        
                        center_lat = aoi_gdf.geometry.centroid.y.mean()
                        center_lon = aoi_gdf.geometry.centroid.x.mean()
                        
                        m = folium.Map(location=[center_lat, center_lon], zoom_start=15)

                        # Esri Satellite Tiles (No Google)
                        folium.TileLayer(
                            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                            attr='Esri',
                            name='Esri Satellite',
                            overlay=False,
                            control=True
                        ).add_to(m)

                        # Property Boundary (Yellow)
                        folium.GeoJson(
                            aoi_gdf,
                            name="Property Boundary",
                            style_function=lambda x: {'color': '#FFFF00', 'fillColor': '#FFFF00', 'weight': 3, 'fillOpacity': 0.1}
                        ).add_to(m)

                        # Wells (Cyan with Navy Fill)
                        for _, row in all_display.iterrows():
                            # Handle potential missing fields gracefully for popup
                            wn = row.get('WellName', 'Unknown')
                            api = row.get('API_UWI_14', 'N/A')
                            op = row.get('OperatorName', 'N/A')
                            
                            # Distinguish color slightly for On-Property vs Off
                            color = '#FFFF00' if row['Distance_ft'] == 0 else '#00FFFF' 
                            
                            folium.CircleMarker(
                                location=[row.geometry.y, row.geometry.x],
                                radius=5,
                                color=color,
                                fill=True,
                                fill_color=NAVY_BLUE, 
                                popup=folium.Popup(f"<b>{wn}</b><br>API: {api}<br>Op: {op}", max_width=250)
                            ).add_to(m)

                        st_folium(m, width="100%", height=500)

                    # --- DATA TABLE ---
                    with col_data:
                        st.subheader("Wells Nearby (1mi)")
                        
                        # REQUIRED COLUMN NAMES
                        target_cols = ['WellName', 'OperatorName', 'API_UWI_14', 'TotalDepth', 'Distance_ft']
                        
                        display_df = pd.DataFrame(all_display)
                        # Filter for columns that actually exist in the response
                        existing_cols = [c for c in target_cols if c in display_df.columns]
                        final_df = display_df[existing_cols].copy()
                        
                        if 'Distance_ft' in final_df.columns:
                            final_df['Distance_ft'] = final_df['Distance_ft'].astype(int)
                            final_df = final_df.sort_values('Distance_ft')
                        
                        try:
                            st.dataframe(
                                final_df.style.background_gradient(cmap="Blues", subset=['Distance_ft']),
                                use_container_width=True,
                                height=500
                            )
                        except Exception:
                            st.dataframe(final_df, use_container_width=True, height=500)
                else:
                    st.warning(f"No wells found within +/- 0.03 degrees of this location.")
            else:
                st.error("Could not fetch data from Enverus (Empty response for Oklahoma County).")
