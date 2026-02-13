def fetch_enverus_data():
    try:
        creds = st.secrets["enverus"]
        # Ensure the keys match your Streamlit Secrets exactly
        d2 = DirectAccessV2(
            client_id=creds["client_id"], 
            client_secret=creds["client_secret"], 
            api_key=creds.get("api_key", "NA")
        )
        
        # CORRECTED FIELD NAMES FOR V2 'well-origins'
        # These names are the most common valid keys for the V2 endpoint
        well_data = d2.query(
            'well-origins',
            County='OKLAHOMA',
            DeletedDate='null',
            # We use these exact PascalCase names with underscores where required
            fields='Well_Name,Operator_Name,API_UWI_14,Total_Depth,Latitude,Longitude',
            pagesize=10000
        )
        
        data_list = list(well_data)
        if not data_list:
            return pd.DataFrame()
            
        df = pd.DataFrame(data_list)
        
        # Standardize the column names for the rest of your app logic
        # This maps the API's messy names to the clean ones your map/table expect
        rename_map = {
            'Well_Name': 'WellName',
            'Operator_Name': 'OperatorName',
            'Total_Depth': 'TotalDepth',
            'Latitude': 'SurfaceLatitude',
            'Longitude': 'SurfaceLongitude'
        }
        return df.rename(columns=rename_map)
    
    except Exception as e:
        st.error(f"Enverus API Error: {e}")
        return pd.DataFrame()
