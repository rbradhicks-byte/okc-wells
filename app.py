import streamlit as st
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, box, Polygon
from geopy.geocoders import ArcGIS
import folium
from streamlit_folium import st_folium
from enverus_developer_api import DirectAccessV2
from enverus_developer_api import developer_api_v3 as enverus_v3

# -----------------------------------------------------------------------------
# 1. CONFIGURATION & STYLING
# -----------------------------------------------------------------------------
st.set_page_config(page_title="OK County Well Discovery", layout="wide")

# CSS Injection: Navy Blue Headers and Metrics
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
    Initialize Enverus client with strict 2-key auth constraint.
    """
    try:
        c_id = st.secrets["enverus"]["client_id"]
        c_secret = st.secrets["enverus"]["client_secret"]
        
        # CONSTRAINT: Initialize with empty api_key to force Oauth flow with ID/Secret
        d2 = DirectAccessV2(client_id=c_id, client_secret=c_secret, api_key="")
        return d2
    except Exception as e:
        st.error(f"Authentication Error: {e}")
        return None

@st.cache_data(ttl=3600)
def fetch_ok_wells(_client):
    """
    Fetch well-origins for Oklahoma County, OK.
    Cached for 1 hour to prevent API quota abuse.
    """
    if not _client:
        return pd.DataFrame()

    query = _client.query("well-origins", 
                          filters={"State": "OK", "County": "OKLAHOMA"},
                          headers={"Accept": "application/json"})
    
    # Clean and return DataFrame
    df = pd.DataFrame(query)
    
    # Ensure coordinates exist
    if not df.empty and 'SurfaceLatitude' in df.columns and 'SurfaceLongitude' in df.columns:
        df = df.dropna(subset=['SurfaceLatitude', 'SurfaceLongitude'])
        return df
    return pd.DataFrame()

def get_location_coordinates(address):
    """
    Geocode using ArcGIS (No Google).
    Auto-appends context to search.
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

# Top Search Bar
search_col1, search_col2 = st.columns([3, 1])
with search_col1:
    user_address = st.text_input("Search Location", placeholder="e.g. 320 Robert S Kerr Ave")
with search_col2:
    search_btn = st.button("Analyze Location", type="primary", use_container_width=True)

if search_btn and user_address:
    # A. Geocoding
    with st.spinner("Triangulating location via Esri ArcGIS..."):
        lat, lon = get_location_coordinates(user_address)

    if lat is None:
        st.error("Could not locate address within Oklahoma County.")
    else:
        # B. Define Property Geometry (AOI)
        # Create a search point GeoDataFrame
        search_point = gpd.GeoDataFrame(
            geometry=[Point(lon, lat)], 
            crs="EPSG:4326"
        )

        aoi_gdf = None
        
        if uploaded_file:
            try:
                aoi_gdf = gpd.read_file(uploaded_file)
                # Ensure CRS match
                if aoi_gdf.crs != "EPSG:4326":
                    aoi_gdf = aoi_gdf.to_crs("EPSG:4326")
            except Exception as e:
                st.warning(f"Error reading GeoJSON: {e}. Reverting to fallback.")
                uploaded_file = None

        if aoi_gdf is None:
            # Fallback: Create ~10 acre box around point
            # 10 acres is approx 200m x 200m. 
            # In degrees approx +/- 0.002 deg
            delta = 0.002
            bbox = box(lon - delta, lat - delta, lon + delta, lat + delta)
            aoi_gdf = gpd.GeoDataFrame(geometry=[bbox], crs="EPSG:4326")

        # C. Fetch & Process Data
        client = get_enverus_client()
        with st.spinner("Fetching Enverus Well Data..."):
            wells_df = fetch_ok_wells(client)

        if not wells_df.empty:
            # Convert Wells to GeoDataFrame
            wells_gdf = gpd.GeoDataFrame(
                wells_df,
                geometry=gpd.points_from_xy(wells_df.SurfaceLongitude, wells_df.SurfaceLatitude),
                crs="EPSG:4326"
            )

            # Spatial Filter: Get wells within reasonable view (e.g., 2 miles) of property
            # Project to meters for distance buffering (EPSG:32124 for Oklahoma North)
            projected_aoi = aoi_gdf.to_crs("EPSG:32124")
            aoi_centroid = projected_aoi.geometry.centroid.iloc[0]
            
            projected_wells = wells_gdf.to_crs("EPSG:32124")
            
            # Filter wells within 10,000ft (approx 3km) buffer for display
            buffer_area = projected_aoi.buffer(10000).geometry.iloc[0] # meters
            mask = projected_wells.within(buffer_area)
            nearby_wells = projected_wells[mask].copy()

            # Calculate Distance to Property Boundary
            # distance() in projected CRS returns meters. Convert to feet (x 3.28084)
            nearby_wells['Distance_ft'] = nearby_wells.geometry.apply(
                lambda x: projected_aoi.distance(x)
            ).iloc[:, 0] * 3.28084

            # Convert back to WGS84 for mapping
            display_wells = nearby_wells.to_crs("EPSG:4326")
            
            # Sort by distance
            display_wells = display_wells.sort_values('Distance_ft').head(50) # Top 50

            # ---------------------------------------------------------
            # 5. UI OUTPUTS
            # ---------------------------------------------------------
            
            # Metrics Row
            m1, m2, m3 = st.columns(3)
            m1.metric("Search Context", "Oklahoma County, OK")
            m2.metric("Wells Found (Nearby)", len(display_wells))
            closest_dist = display_wells['Distance_ft'].min() if not display_wells.empty else 0
            m3.metric("Closest Well", f"{closest_dist:,.0f} ft")

            col_map, col_data = st.columns([1, 1])

            # --- MAP GENERATION ---
            with col_map:
                st.subheader("Satellite Reconnaissance")
                
                # Center map on property
                center_lat = aoi_gdf.geometry.centroid.y.mean()
                center_lon = aoi_gdf.geometry.centroid.x.mean()
                
                m = folium.Map(location=[center_lat, center_lon], zoom_start=15)

                # CONSTRAINT: Use Esri World Imagery (No Google)
                folium.TileLayer(
                    tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                    attr='Esri',
                    name='Esri Satellite',
                    overlay=False,
                    control=True
                ).add_to(m)

                # Draw Property Boundary (Yellow)
                folium.GeoJson(
                    aoi_gdf,
                    name="Property Boundary",
                    style_function=lambda x: {'color': '#FFFF00', 'fillColor': '#FFFF00', 'weight': 3, 'fillOpacity': 0.1}
                ).add_to(m)

                # Draw Wells (Cyan)
                for _, row in display_wells.iterrows():
                    folium.CircleMarker(
                        location=[row.geometry.y, row.geometry.x],
                        radius=5,
                        color='#00FFFF',
                        fill=True,
                        fill_color='#000080', # Navy Blue fill
                        popup=folium.Popup(f"<b>{row['WellName']}</b><br>API: {row['API']}<br>Op: {row['OperatorName']}", max_width=250)
                    ).add_to(m)

                st_folium(m, width="100%", height=500)

            # --- DATA TABLE ---
            with col_data:
                st.subheader("Well Inventory")
                
                table_cols = ['WellName', 'OperatorName', 'API', 'TotalDepth', 'Distance_ft']
                # Handle case where TotalDepth might be missing/NaN
                display_df = pd.DataFrame(display_wells)
                
                # Check which columns actually exist in Enverus response
                valid_cols = [c for c in table_cols if c in display_df.columns]
                final_df = display_df[valid_cols].copy()
                
                # Format
                if 'Distance_ft' in final_df.columns:
                    final_df['Distance_ft'] = final_df['Distance_ft'].astype(int)
                
                # Try/Except block for Matplotlib styling constraint
                try:
                    # Apply gradient styling if matplotlib is available
                    st.dataframe(
                        final_df.style.background_gradient(cmap="Blues", subset=['Distance_ft']),
                        use_container_width=True,
                        height=500
                    )
                except Exception:
                    # Fallback if matplotlib/jinja fails
                    st.dataframe(final_df, use_container_width=True, height=500)

        else:
            st.warning("No well data returned from Enverus for this region.")
