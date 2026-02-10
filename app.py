import streamlit as st
import pandas as pd
import geopandas as gpd
import requests
import json
import folium
from streamlit_folium import st_folium
from shapely.geometry import shape, Point
from shapely import wkt
import streamlit.components.v1 as components

# ------------------------------------------------------------------
# CONFIGURATION & CONSTANTS
# ------------------------------------------------------------------
st.set_page_config(page_title="Well Proximity Portal - OK County", layout="wide")

# CRS Constants
# EPSG:4326 = WGS84 (Lat/Lon) - Used for Maps and API queries
# EPSG:2267 = NAD83 / Oklahoma North (ftUS) - Used for accurate distance calc
CRS_MAP = "EPSG:4326"
CRS_CALC = "EPSG:2267"

# Enverus Endpoints
AUTH_URL = "https://api.enverus.com/v2/direct-access/tokens"
WELLS_URL = "https://api.enverus.com/v2/direct-access/wells"

# ------------------------------------------------------------------
# HELPER FUNCTIONS
# ------------------------------------------------------------------

def get_enverus_token():
    """Authenticates with Enverus DirectAccess V2"""
    if "enverus" not in st.secrets:
        st.error("Enverus credentials not found in secrets.toml")
        return None

    creds = st.secrets["enverus"]
    payload = {
        "grantType": "client_credentials",
        "clientId": creds["client_id"],
        "clientSecret": creds["client_secret"],
        "scope": "unrestricted"
    }
    
    try:
        # In a real scenario, cache this token based on expiration
        response = requests.post(AUTH_URL, json=payload)
        response.raise_for_status()
        return response.json().get("token")
    except Exception as e:
        st.error(f"Authentication Failed: {e}")
        return None

def fetch_enverus_wells(token, polygon_wkt):
    """
    Queries Enverus Wells endpoint using a WKT Polygon filter.
    Filters for Active/Producing status.
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    
    # Enverus Filter Logic
    # 1. Spatial filter (Intersects provided property) + Buffer slightly to catch nearby
    # 2. Status filter (Active, Producing)
    
    params = {
        "dataset": "wells",
        "bp": polygon_wkt, # Spatial filter
        "pagesize": 10000
    }
    
    # Note: Complex filtering usually done via Request Body in V2 or specific params
    # We will fetch spatially then filter in Pandas for granular control
    
    try:
        # Check if user is actually connected (Mock logic for safety if no creds)
        if token == "MOCK_TOKEN": 
            return get_mock_data()

        response = requests.get(WELLS_URL, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        return data
    except Exception as e:
        st.error(f"Data Fetch Error: {e}")
        return []

def get_mock_data():
    """Returns dummy data if API fails or for demo purposes"""
    return [
        {"WellName": "OKLAHOMA CITY 1-24", "Operator": "DEVON ENERGY", "API": "3500012345", "WellStatus": "ACTIVE", "Latitude": 35.4676, "Longitude": -97.5164, "TotalDepth": 8500},
        {"WellName": "THUNDERBIRD 4H", "Operator": "CHESAPEAKE", "API": "3500098765", "WellStatus": "PRODUCING", "Latitude": 35.4700, "Longitude": -97.5100, "TotalDepth": 12000},
        {"WellName": "OLD DRY HOLE", "Operator": "UNKNOWN", "API": "3500055555", "WellStatus": "PLUGGED", "Latitude": 35.4650, "Longitude": -97.5200, "TotalDepth": 4000},
    ]

def calculate_distances(gdf_wells, gdf_property):
    """
    Calculates spatial relationships.
    1. Projects to State Plane (Feet).
    2. Determines Point-in-Polygon.
    3. Calculates distance to nearest boundary if outside.
    """
    # Reproject to Feet (Oklahoma North)
    wells_ft = gdf_wells.to_crs(CRS_CALC)
    prop_ft = gdf_property.to_crs(CRS_CALC)
    
    # We assume the property file contains one main polygon (or we take the union)
    property_poly = prop_ft.geometry.unary_union
    
    results = []
    
    for idx, row in wells_ft.iterrows():
        point = row.geometry
        is_inside = property_poly.contains(point)
        
        distance = 0.0
        if not is_inside:
            # Distance from point to nearest edge of polygon
            distance = property_poly.distance(point)
            
        results.append({
            "is_inside": is_inside,
            "dist_ft": round(distance, 2)
        })
        
    return pd.DataFrame(results, index=gdf_wells.index)

def style_dataframe(df):
    """Applies color coding to the result dataframe"""
    def color_rows(row):
        dist = row["Distance (ft)"]
        status = row["Proximity Status"]
        
        if status == "On Property":
            return ['background-color: #d4edda; color: #155724'] * len(row) # Green
        elif dist < 1000:
            return ['background-color: #fff3cd; color: #856404'] * len(row) # Yellow
        else:
            return ['background-color: #f8d7da; color: #721c24'] * len(row) # Red

    return df.style.apply(color_rows, axis=1)

# ------------------------------------------------------------------
# MAIN APP UI
# ------------------------------------------------------------------

def main():
    st.sidebar.title("⚙️ Settings")
    
    # 1. Credentials Check
    if "enverus" in st.secrets:
        st.sidebar.success("Enverus Credentials Loaded")
    else:
        st.sidebar.warning("Enverus Secrets Missing")
        
    # 2. Google Maps Toggle
    use_google = False
    google_key = st.sidebar.text_input("Google Maps API Key (Optional)", type="password")
    if google_key:
        use_google = True
    elif "google" in st.secrets and "api_key" in st.secrets["google"]:
        google_key = st.secrets["google"]["api_key"]
        use_google = True
        
    st.title("🛢️ Oklahoma County Well Proximity Portal")
    st.markdown("Analyze well proximity relative to property boundaries using Enverus DirectAccess.")

    # 3. Property Input (ArcGIS Fallback -> File Upload)
    st.subheader("1. Define Property Boundary")
    
    tab1, tab2 = st.tabs(["📁 File Upload (Shapefile/GeoJSON)", "🌐 ArcGIS Online (Experimental)"])
    
    gdf_property = None
    
    with tab1:
        uploaded_file = st.file_uploader("Upload Property Boundary (GeoJSON or Zipped Shapefile)", type=["geojson", "zip"])
        if uploaded_file:
            try:
                # GeoPandas can read bytes directly for some formats, zip requires specific handling usually
                # but modern gpd.read_file handles zip paths well if saved locally or via fiona.
                # For Streamlit, easiest is to save temp or read directly if geojson.
                gdf_property = gpd.read_file(uploaded_file)
                # Ensure CRS is WGS84 for standardization
                if gdf_property.crs != CRS_MAP:
                    gdf_property = gdf_property.to_crs(CRS_MAP)
                st.success(f"Loaded polygon with {len(gdf_property)} features.")
            except Exception as e:
                st.error(f"Error reading file: {e}")

    with tab2:
        st.info("Oklahoma County Assessor REST endpoints often change. Use File Upload for reliability.")
        # Logic to query ArcGIS REST would go here (requires address geocoding first)

    # 4. Analysis Execution
    if gdf_property is not None:
        
        # Get bounding box WKT for API query (adds a buffer to find nearby wells)
        # We buffer in degrees roughly (0.01 deg ~= 1km) just to fetch enough data
        # Correct approach: buffer in feet then project back, but simple box works for query.
        bbox_poly = gdf_property.to_crs(CRS_CALC).buffer(5000).to_crs(CRS_MAP).unary_union.envelope
        wkt_filter = bbox_poly.wkt

        if st.button("Find Wells"):
            with st.spinner("Authenticating with Enverus..."):
                token = get_enverus_token()
                if not token:
                    st.warning("Using Mock Data (Auth Failed)")
                    token = "MOCK_TOKEN" # Fallback for demo
            
            with st.spinner("Querying Enverus API..."):
                raw_data = fetch_enverus_wells(token, wkt_filter)
                
            if raw_data:
                # Convert to GeoDataFrame
                df_wells = pd.DataFrame(raw_data)
                
                # Check for necessary columns (Handle mock vs real API response differences)
                lat_col = 'Latitude' if 'Latitude' in df_wells.columns else 'SurfaceLatitude'
                lon_col = 'Longitude' if 'Longitude' in df_wells.columns else 'SurfaceLongitude'
                
                if lat_col not in df_wells.columns:
                    st.error("API returned data without coordinates.")
                else:
                    gdf_wells = gpd.GeoDataFrame(
                        df_wells, 
                        geometry=gpd.points_from_xy(df_wells[lon_col], df_wells[lat_col]),
                        crs=CRS_MAP
                    )

                    # Filter for Active/Producing (Example statuses)
                    # Real API statuses might vary
                    status_col = 'WellStatus' if 'WellStatus' in df_wells.columns else 'Status'
                    active_statuses = ['ACTIVE', 'PRODUCING', 'PERMITTED', 'DRILLING']
                    # Normalize text
                    gdf_wells = gdf_wells[gdf_wells[status_col].astype(str).str.upper().isin(active_statuses)]

                    # 5. Spatial Logic
                    spatial_results = calculate_distances(gdf_wells, gdf_property)
                    
                    # Combine Data
                    final_df = gdf_wells.copy()
                    final_df['On Property'] = spatial_results['is_inside']
                    final_df['Distance (ft)'] = spatial_results['dist_ft']
                    final_df['Proximity Status'] = final_df.apply(
                        lambda x: "On Property" if x['On Property'] else ("Nearby (< 1000')" if x['Distance (ft)'] < 1000 else "Far"), 
                        axis=1
                    )
                    
                    # Display Table
                    st.subheader("2. Well Report")
                    display_cols = ['WellName', 'Operator', 'API', status_col, 'Proximity Status', 'Distance (ft)']
                    st.dataframe(style_dataframe(final_df[display_cols]))

                    # 6. Mapping Engine
                    st.subheader("3. Visual Inspection")
                    
                    # Center Map
                    centroid = gdf_property.unary_union.centroid
                    map_center = [centroid.y, centroid.x]

                    if use_google:
                        # GOOGLE MAPS IMPLEMENTATION
                        st.markdown("**Source:** Google Maps Satellite")
                        
                        # Prepare Data for JS
                        prop_geojson = gdf_property.to_json()
                        wells_data = []
                        for _, row in final_df.iterrows():
                            color = "green" if row['On Property'] else ("yellow" if row['Distance (ft)'] < 1000 else "red")
                            wells_data.append({
                                "lat": row.geometry.y,
                                "lng": row.geometry.x,
                                "name": row['WellName'],
                                "color": color
                            })
                        
                        html_code = f"""
                        <!DOCTYPE html>
                        <html>
                          <head>
                            <script src="https://maps.googleapis.com/maps/api/js?key={google_key}&callback=initMap" async defer></script>
                            <style>#map {{height: 500px; width: 100%;}}</style>
                          </head>
                          <body>
                            <div id="map"></div>
                            <script>
                              function initMap() {{
                                var map = new google.maps.Map(document.getElementById('map'), {{
                                  center: {{lat: {map_center[0]}, lng: {map_center[1]}}},
                                  zoom: 15,
                                  mapTypeId: 'satellite'
                                }});

                                // Add Property Polygon
                                var geojson = {prop_geojson};
                                map.data.addGeoJson(geojson);
                                map.data.setStyle({{
                                  fillColor: 'blue',
                                  strokeWeight: 2,
                                  fillOpacity: 0.1
                                }});

                                // Add Wells
                                var wells = {json.dumps(wells_data)};
                                wells.forEach(w => {{
                                    var marker = new google.maps.Marker({{
                                        position: {{lat: w.lat, lng: w.lng}},
                                        map: map,
                                        title: w.name,
                                        icon: 'http://maps.google.com/mapfiles/ms/icons/' + w.color + '-dot.png'
                                    }});
                                }});
                              }}
                            </script>
                          </body>
                        </html>
                        """
                        components.html(html_code, height=500)
                        
                    else:
                        # FOLIUM FALLBACK
                        st.markdown("**Source:** Esri World Imagery (Open Source Fallback)")
                        m = folium.Map(location=map_center, zoom_start=15)
                        
                        # Add Esri Satellite Tile
                        folium.TileLayer(
                            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                            attr='Esri',
                            name='Esri Satellite',
                            overlay=False,
                            control=True
                        ).add_to(m)

                        # Add Property Polygon
                        folium.GeoJson(
                            gdf_property,
                            style_function=lambda x: {'fillColor': 'blue', 'color': 'blue', 'fillOpacity': 0.1}
                        ).add_to(m)

                        # Add Wells
                        for _, row in final_df.iterrows():
                            color = "green" if row['On Property'] else ("orange" if row['Distance (ft)'] < 1000 else "red")
                            folium.CircleMarker(
                                location=[row.geometry.y, row.geometry.x],
                                radius=6,
                                popup=f"{row['WellName']} ({row[status_col]})",
                                color=color,
                                fill=True,
                                fill_color=color
                            ).add_to(m)

                        st_folium(m, width=1000, height=500)

if __name__ == "__main__":
    main()
