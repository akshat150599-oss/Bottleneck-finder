# --- Compatibility + imports (fixes NameError for st.divider) ---
try:
    import streamlit as st
except Exception as e:
    raise RuntimeError("Streamlit is required. Try: pip install streamlit") from e

# Backfill st.divider() for older Streamlit
if not hasattr(st, "divider"):
    def _divider():
        st.markdown("---")
    st.divider = _divider

import pandas as pd
import numpy as np
import requests
from io import BytesIO, StringIO
from pandas.tseries.offsets import BDay

# =============================================================
# Online Port Master (authoritative) + Fallbacks
# =============================================================

WPI_CSV_URL = "https://msi.nga.mil/api/publications/world-port-index?output=csv"
UNLOCODE_URL = "https://datahub.io/core/un-locode/r/code-list.csv"

# Very small seed list (only used if both online sources fail)
EMBEDDED_PORT_MASTER_CSV = """Port,Country,Latitude,Longitude
Nhava Sheva (JNPT),IN,18.95,72.95
Mundra,IN,22.75,69.70
Pipavav,IN,20.86,71.50
Hazira,IN,21.12,72.65
Chennai,IN,13.09,80.30
Singapore,SG,1.26,103.84
Port Klang,MY,2.99,101.39
Colombo,LK,6.95,79.84
Jebel Ali,AE,25.02,55.06
Rotterdam,NL,51.95,4.13
Los Angeles,US,33.75,-118.26
Long Beach,US,33.76,-118.21
"""

# -----------------------
# Helpers for online datasets
# -----------------------

def _http_get_csv(url: str, timeout=30):
    """GET a CSV over HTTP and return a pandas DataFrame. Raise on failure."""
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    data = r.text
    if not data or len(data) < 10:
        raise ValueError("Empty/invalid response")
    return pd.read_csv(StringIO(data))

@st.cache_data(ttl=60*60*24)
def fetch_wpi_ports() -> pd.DataFrame:
    df = _http_get_csv(WPI_CSV_URL)
    cols = {c.lower(): c for c in df.columns}

    def pick(*opts):
        for o in opts:
            if o.lower() in cols:
                return cols[o.lower()]
        return None

    c_port = pick('portname', 'port name', 'port', 'name', 'main_port_name')
    c_ctry = pick('countryname', 'country', 'country name')
    c_lat  = pick('latitude', 'lat', 'y')
    c_lon  = pick('longitude', 'lon', 'x')

    if not (c_port and c_ctry and c_lat and c_lon):
        if 'PORT_NAME' in df.columns: c_port = 'PORT_NAME'
        if 'COUNTRY' in df.columns:   c_ctry = 'COUNTRY'
        if 'LATITUDE' in df.columns:  c_lat  = 'LATITUDE'
        if 'LONGITUDE' in df.columns: c_lon  = 'LONGITUDE'
    if not (c_port and c_ctry and c_lat and c_lon):
        raise ValueError("WPI CSV missing expected columns")

    out = pd.DataFrame({
        'Port': df[c_port].astype(str).str.strip(),
        'Country': df[c_ctry].astype(str).str.strip(),
        'Latitude': pd.to_numeric(df[c_lat], errors='coerce'),
        'Longitude': pd.to_numeric(df[c_lon], errors='coerce'),
    })
    out = out.dropna(subset=['Latitude', 'Longitude']).drop_duplicates()
    return out

def _dm_to_decimal(dm: str):
    dm = str(dm).strip()
    if not dm or dm == 'nan':
        return np.nan, np.nan
    try:
        parts = dm.split()
        if len(parts) != 2:
            return np.nan, np.nan
        latp, lonp = parts
        # LAT
        deg = int(latp[:-3]); minutes = int(latp[-3:-1]); hemi = latp[-1]
        lat = deg + minutes/60.0
        if hemi in ('S', 's'):
            lat = -lat
        # LON
        deg = int(lonp[:-3]); minutes = int(lonp[-3:-1]); hemi = lonp[-1]
        lon = deg + minutes/60.0
        if hemi in ('W', 'w'):
            lon = -lon
        return lat, lon
    except Exception:
        return np.nan, np.nan

@st.cache_data(ttl=60*60*24)
def fetch_unlocode_ports() -> pd.DataFrame:
    df = _http_get_csv(UNLOCODE_URL)
    cols = {c.lower(): c for c in df.columns}
    c_name = cols.get('name') or cols.get('namewodiacritics')
    c_ctry = cols.get('country')
    c_coords = cols.get('coordinates')
    c_func = cols.get('function')
    if not (c_name and c_ctry and c_coords):
        raise ValueError('UN/LOCODE CSV missing expected columns')
    if c_func and c_func in df.columns:
        df = df[df[c_func].astype(str).str.contains('1', na=False)]
    latlons = df[c_coords].apply(_dm_to_decimal)
    lat = latlons.apply(lambda t: t[0])
    lon = latlons.apply(lambda t: t[1])
    out = pd.DataFrame({
        'Port': df[c_name].astype(str).str.strip(),
        'Country': df[c_ctry].astype(str).str.strip(),
        'Latitude': lat,
        'Longitude': lon,
    })
    out = out.dropna(subset=['Latitude', 'Longitude']).drop_duplicates()
    return out

@st.cache_data(ttl=60*60*24)
def get_port_master(prefer: str = 'WPI') -> pd.DataFrame:
    try:
        if prefer == 'WPI':
            return fetch_wpi_ports()
        else:
            return fetch_unlocode_ports()
    except Exception:
        try:
            if prefer == 'WPI':
                return fetch_unlocode_ports()
            else:
                return fetch_wpi_ports()
        except Exception:
            return pd.read_csv(StringIO(EMBEDDED_PORT_MASTER_CSV))

# =============================================================
# Core App
# =============================================================

st.set_page_config(page_title="Ocean Bottleneck Analyzer", layout="wide")
st.title("📦 Ocean Bottleneck Analyzer")
st.caption("Identify bottlenecks, free-day risk (LFD/OFD), and inspect D&D hours")

# Optional dependency checks (Excel engines)

def has_openpyxl() -> bool:
    try:
        import openpyxl  # noqa: F401
        return True
    except Exception:
        return False

def has_xlrd_12() -> bool:
    try:
        import xlrd  # noqa: F401
        import pkg_resources
        ver = pkg_resources.get_distribution("xlrd").version
        return ver.startswith("1.2")
    except Exception:
        return False

HAS_OPENPYXL = has_openpyxl()
HAS_XLRD12 = has_xlrd_12()

support_msg = []
support_msg.append("✅ .xlsx (openpyxl)" if HAS_OPENPYXL else "❌ .xlsx (install `openpyxl`)")
support_msg.append("✅ .xls (xlrd==1.2.0)" if HAS_XLRD12 else "❌ .xls (install `xlrd==1.2.0` if needed)")
st.info("File support in this environment: " + " | ".join(support_msg))

allowed_types = ["csv"]
if HAS_OPENPYXL: allowed_types.append("xlsx")
if HAS_XLRD12:  allowed_types.append("xls")

st.markdown(
    """
### What it computes
**Durations**
- **POL:** Gate In → Container Loaded  
- **POD:** Discharge → Gate Out, Gate Out → Empty Return  

**Free Day windows**
- **Estimated LFD (POD)** = Discharge + **Free Days (POD)**  
- **Estimated OFD (POL)** = Gate In + **Free Days (POL)**  
- Slack vs LFD/OFD shows how far you were from using up free days.  
"""
)

# -----------------------
# File upload
# -----------------------
uploaded = st.file_uploader(
    "Upload your movement/export file",
    type=allowed_types,
)
if not uploaded:
    st.stop()
name = uploaded.name.lower()

# -----------------------
# Loaders
# -----------------------
@st.cache_data
def load_csv(file):
    return pd.read_csv(file)

@st.cache_data
def load_excel_bytes(uploaded_file) -> bytes:
    return uploaded_file.getvalue()

def excel_file(bytes_data: bytes, is_xlsx: bool):
    engine = "openpyxl" if is_xlsx else "xlrd"
    try:
        return pd.ExcelFile(BytesIO(bytes_data), engine=engine), engine
    except ImportError:
        if is_xlsx:
            st.error("`.xlsx` needs **openpyxl**. Add to requirements.txt or run: `pip install openpyxl`")
        else:
            st.error("`.xls` needs **xlrd==1.2.0**. Add to requirements.txt or run: `pip install xlrd==1.2.0`")
        st.stop()
    except Exception as e:
        st.error(f"Failed to open Excel file: {e}")
        st.stop()

def read_sheet(bytes_data: bytes, sheet_name: str, engine: str) -> pd.DataFrame:
    try:
        return pd.read_excel(BytesIO(bytes_data), sheet_name=sheet_name, engine=engine)
    except Exception as e:
        st.error(f"Failed to read sheet '{sheet_name}': {e}")
        st.stop()

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df

def find_col(df: pd.DataFrame, name_variants):
    cand = {c.lower(): c for c in df.columns}
    for v in name_variants:
        if v.lower() in cand:
            return cand[v.lower()]
    return None

def default_index(colname, cols):
    try:
        return cols.get_loc(colname) if colname in cols else 0
    except Exception:
        return 0

def to_datetime(series: pd.Series, dayfirst: bool = True) -> pd.Series:
    return pd.to_datetime(series, errors='coerce', dayfirst=dayfirst)

def compute_duration_hours(start: pd.Series, end: pd.Series) -> pd.Series:
    return (end - start).dt.total_seconds() / 3600.0

def summarize(series: pd.Series, mode_round: int = 1) -> pd.Series:
    s = pd.to_numeric(series, errors='coerce').dropna()
    if len(s) == 0:
        return pd.Series({'count':0,'avg_hours':np.nan,'mean_hours':np.nan,'median_hours':np.nan,'mode_hours':np.nan})
    avg_val = s.mean(); median_val = s.median(); mode_vals = s.round(mode_round).mode()
    mode_val = mode_vals.iloc[0] if len(mode_vals) else np.nan
    return pd.Series({'count':len(s),'avg_hours':avg_val,'mean_hours':avg_val,'median_hours':median_val,'mode_hours':mode_val})

def build_summary(df: pd.DataFrame, group_cols, value_col: str, label: str, mode_round: int = 1) -> pd.DataFrame:
    g = (df.groupby(group_cols)[value_col]
          .apply(lambda s: summarize(s, mode_round=mode_round))
          .reset_index()
          .rename(columns={value_col:'value','level_2':'measure'})
          .pivot_table(index=group_cols, columns='measure', values='value', aggfunc='first')
          .reset_index()
          .assign(Metric=label))
    for need in ['count','avg_hours','mean_hours','median_hours','mode_hours']:
        if need not in g.columns: g[need] = np.nan
    return g

# -----------------------
# Read file → DataFrame
# -----------------------
if name.endswith('.csv'):
    df_raw = load_csv(uploaded); sheet_name = None
else:
    is_xlsx = name.endswith('.xlsx')
    if is_xlsx and not HAS_OPENPYXL:
        st.error("This environment can't read `.xlsx` yet. Install **openpyxl** or upload a CSV."); st.stop()
    if (not is_xlsx) and (not HAS_XLRD12):
        st.error("This environment can't read legacy `.xls`. Install **xlrd==1.2.0** or upload a CSV."); st.stop()
    excel_bytes = load_excel_bytes(uploaded)
    xls, engine = excel_file(excel_bytes, is_xlsx=is_xlsx)
    sheet_name = st.selectbox("Choose sheet", xls.sheet_names, index=0)
    df_raw = read_sheet(excel_bytes, sheet_name, engine=engine)

df_raw = normalize_columns(df_raw)

# -----------------------
# Column mapping
# -----------------------
default_cols = {
    'carrier': find_col(df_raw, ['Carrier Name','carrier','Carrier']),
    'gate_in': find_col(df_raw, ['2-Gate In Timestamp','Gate In Timestamp','2 - Gate In Timestamp']),
    'container_loaded': find_col(df_raw, ['3-Container Loaded Timestamp','Container Loaded Timestamp','3 - Container Loaded Timestamp']),
    'discharge': find_col(df_raw, ['6-Container Discharge Timestamp','Container Discharge Timestamp','6 - Container Discharge Timestamp']),
    'gate_out': find_col(df_raw, ['7-Gate Out Timestamp','Gate Out Timestamp','7 - Gate Out Timestamp']),
    'empty_return': find_col(df_raw, ['8-Empty Return Timestamp','Empty Return Timestamp','8 - Empty Return Timestamp']),
    'pol': find_col(df_raw, ['POL Port','POL','Port of Loading','POL Name','Origin Port']),
    'pod': find_col(df_raw, ['POD Port','POD','Port of Discharge','POD Name','Destination Port']),
}

with st.expander("Column Mapping", expanded=True):
    c1, c2 = st.columns(2)
    with c1:
        carrier_col = st.selectbox("Carrier column", df_raw.columns, index=default_index(default_cols['carrier'], df_raw.columns))
        pol_col = st.selectbox("POL (Port of Loading) column", df_raw.columns, index=default_index(default_cols['pol'], df_raw.columns))
        gate_in_col = st.selectbox("2-Gate In Timestamp", df_raw.columns, index=default_index(default_cols['gate_in'], df_raw.columns))
        container_loaded_col = st.selectbox("3-Container Loaded Timestamp", df_raw.columns, index=default_index(default_cols['container_loaded'], df_raw.columns))
    with c2:
        pod_col = st.selectbox("POD (Port of Discharge) column", df_raw.columns, index=default_index(default_cols['pod'], df_raw.columns))
        discharge_col = st.selectbox("6-Container Discharge Timestamp", df_raw.columns, index=default_index(default_cols['discharge'], df_raw.columns))
        gate_out_col = st.selectbox("7-Gate Out Timestamp", df_raw.columns, index=default_index(default_cols['gate_out'], df_raw.columns))
        empty_return_col = st.selectbox("8-Empty Return Timestamp", df_raw.columns, index=default_index(default_cols['empty_return'], df_raw.columns))

# -----------------------
# Settings
# -----------------------
st.divider(); st.subheader("Settings")
dayfirst = st.checkbox("Dates are day-first (DD/MM/YYYY)", value=True)
unit = st.selectbox("Units", ["hours","minutes"], index=0)
mode_round = st.slider("Mode rounding (units)", 0, 3, 1)
neg_policy = st.selectbox(
    "Milestone durations: negatives",
    ["Keep (could be data issue)","Treat as NaN (drop from stats)"],
    index=1,
)
neg_tol = st.slider(
    "Slack tolerance (clip small negatives to 0)", 0.0, 6.0, 2.0, 0.5,
)

# -----------------------
# Compute milestone durations
# -----------------------

gate_in_dt = to_datetime(df_raw[gate_in_col], dayfirst=dayfirst)
container_loaded_dt = to_datetime(df_raw[container_loaded_col], dayfirst=dayfirst)
discharge_dt = to_datetime(df_raw[discharge_col], dayfirst=dayfirst)
gate_out_dt = to_datetime(df_raw[gate_out_col], dayfirst=dayfirst)
empty_return_dt = to_datetime(df_raw[empty_return_col], dayfirst=dayfirst)

pol_gap = compute_duration_hours(gate_in_dt, container_loaded_dt)
pod_dg_gap = compute_duration_hours(discharge_dt, gate_out_dt)
pod_ge_gap = compute_duration_hours(gate_out_dt, empty_return_dt)

if neg_policy == "Treat as NaN (drop from stats)":
    pol_gap = pol_gap.where(pol_gap >= 0)
    pod_dg_gap = pod_dg_gap.where(pod_dg_gap >= 0)
    pod_ge_gap = pod_ge_gap.where(pod_ge_gap >= 0)

df = df_raw.copy()
df["_Carrier"]   = df[carrier_col].astype(str).str.strip()
df["_POL Port"]  = df[pol_col].astype(str).str.strip() if pol_col else "UNKNOWN_POL"
df["_POD Port"]  = df[pod_col].astype(str).str.strip() if pod_col else "UNKNOWN_POD"
df["_POL_duration"]    = (pol_gap * (60 if unit == 'minutes' else 1))
df["_POD_dg_duration"] = (pod_dg_gap * (60 if unit == 'minutes' else 1))
df["_POD_ge_duration"] = (pod_ge_gap * (60 if unit == 'minutes' else 1))

# -----------------------
# Free-Day windows (LFD/OFD)
# -----------------------

def end_of_day(ts: pd.Timestamp) -> pd.Timestamp:
    if pd.isna(ts): return pd.NaT
    return ts.normalize() + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)

def add_days_eod(start_ts: pd.Timestamp, n_days: int, business_days: bool):
    if pd.isna(start_ts) or pd.isna(n_days): return pd.NaT
    n = int(n_days)
    if business_days:
        target = (start_ts.normalize() + BDay(n)).to_pydatetime()
        target = pd.Timestamp(target)
    else:
        target = start_ts.normalize() + pd.to_timedelta(n, unit="D")
    return end_of_day(target)

def norm_key(s): return (str(s).strip().lower()) if pd.notna(s) else ""

def build_free_days_mapper(df_map: pd.DataFrame, side: str):
    cols = {c.lower(): c for c in df_map.columns}
    def pick(*opts):
        for o in opts:
            if o.lower() in cols: return cols[o.lower()]
        return None
    if side == "POD":
        port_col = pick("POD Port","POD","Port of Discharge")
    else:
        port_col = pick("POL Port","POL","Port of Loading")
    car_col  = pick("Carrier Name","Carrier")
    days_col = pick("Free Days","FreeDays","free_days","Days","Demurrage Free Days")
    if not days_col:
        return None
    cp, p_only, c_only = {}, {}, {}
    for _, r in df_map.iterrows():
        try: days = int(r.get(days_col, np.nan))
        except Exception: continue
        if pd.isna(days): continue
        port = norm_key(r.get(port_col, "")) if port_col else ""
        car  = norm_key(r.get(car_col, "")) if car_col  else ""
        if car and port: cp[(car, port)] = days
        elif port:       p_only[port] = days
        elif car:        c_only[car]  = days
    return cp, p_only, c_only

def apply_free_days(df_in: pd.DataFrame, car_col: str, port_col: str,
                    default_days: int, mapping_tuple, side_label: str):
    car_key = df_in[car_col].map(norm_key)
    port_key = df_in[port_col].map(norm_key)
    days = pd.Series(default_days, index=df_in.index, dtype="float")
    source = pd.Series("default", index=df_in.index, dtype="object")
    if mapping_tuple:
        cp, p_only, c_only = mapping_tuple
        if cp:
            combo = (car_key + "||" + port_key).map(lambda k: cp.get(tuple(k.split("||",1)), np.nan))
            mask = ~pd.isna(combo)
            days = np.where(mask, combo, days)
            source = np.where(mask, "carrier+port", source)
        if p_only:
            pod_map = port_key.map(lambda k: p_only.get(k, np.nan))
            mask = ~pd.isna(pod_map)
            days = np.where(mask, pod_map, days)
            source = np.where(mask, f"{side_label}-only", source)
        if c_only:
            car_map = car_key.map(lambda k: c_only.get(k, np.nan))
            mask = ~pd.isna(car_map)
            days = np.where(mask, car_map, days)
            source = np.where(mask, "carrier-only", source)
    return pd.to_numeric(days, errors="coerce"), pd.Series(source, index=df_in.index)

def clip_small_negatives_to_zero(series: pd.Series, tol_hours: float) -> pd.Series:
    return series.mask((series < 0) & (series >= -tol_hours), 0)

# --- Estimated LFD (POD)
st.divider(); st.subheader("Estimated LFD (POD)")
c1, c2, c3 = st.columns([1,1,2])
with c1:
    default_free_days_pod = st.number_input("Default POD Free Days", 0, 60, 5, 1)
with c2:
    business_days_pod = st.checkbox("POD counts Business Days (skip Sat/Sun)?", value=False)
with c3:
    fd_map_pod_file = st.file_uploader(
        "Optional POD Free-Days mapping CSV (POD Port, Carrier Name, Free Days)", type=["csv"], key="podmap"
    )

pod_mapper = None
if fd_map_pod_file is not None:
    try:
        df_pod_map = pd.read_csv(fd_map_pod_file)
        pod_mapper = build_free_days_mapper(df_pod_map, side="POD")
        st.success("Loaded POD Free-Days mapping.")
    except Exception as e:
        st.warning(f"Could not read POD mapping CSV: {e}")

df["_FreeDays_POD"], df["_FD_POD_source"] = apply_free_days(
    df, "_Carrier", "_POD Port", default_free_days_pod, pod_mapper, "POD"
)
df["_Estimated_LFD"] = [add_days_eod(d, n, business_days_pod) for d, n in zip(discharge_dt, df["_FreeDays_POD"])]
df["_LFD_Slack_hours"] = (df["_Estimated_LFD"] - gate_out_dt).dt.total_seconds() / 3600.0
df["_LFD_Slack_hours"] = clip_small_negatives_to_zero(df["_LFD_Slack_hours"], neg_tol)

pod_cov = df["_FD_POD_source"].value_counts(dropna=False).to_dict()
st.caption(f"POD Free Days source breakdown: {pod_cov}")

# --- Estimated OFD (POL)
st.divider(); st.subheader("Estimated OFD (POL)")
c1, c2, c3 = st.columns([1,1,2])
with c1:
    default_free_days_pol = st.number_input("Default POL Free Days (Origin)", 0, 60, 3, 1)
with c2:
    business_days_pol = st.checkbox("POL counts Business Days (skip Sat/Sun)?", value=False)
with c3:
    fd_map_pol_file = st.file_uploader(
        "Optional POL Free-Days mapping CSV (POL Port, Carrier Name, Free Days)", type=["csv"], key="polmap"
    )

pol_mapper = None
if fd_map_pol_file is not None:
    try:
        df_pol_map = pd.read_csv(fd_map_pol_file)
        pol_mapper = build_free_days_mapper(df_pol_map, side="POL")
        st.success("Loaded POL Free-Days mapping.")
    except Exception as e:
        st.warning(f"Could not read POL mapping CSV: {e}")

df["_FreeDays_POL"], df["_FD_POL_source"] = apply_free_days(
    df, "_Carrier", "_POL Port", default_free_days_pol, pol_mapper, "POL"
)
df["_Estimated_OFD"] = [add_days_eod(d, n, business_days_pol) for d, n in zip(gate_in_dt, df["_FreeDays_POL"])]
df["_OFD_Slack_hours"] = (df["_Estimated_OFD"] - container_loaded_dt).dt.total_seconds() / 3600.0
df["_OFD_Slack_hours"] = clip_small_negatives_to_zero(df["_OFD_Slack_hours"], neg_tol)

pol_cov = df["_FD_POL_source"].value_counts(dropna=False).to_dict()
st.caption(f"POL Free Days source breakdown: {pol_cov}")

# -----------------------
# Filters
# -----------------------
st.subheader("Filters")
carriers = st.multiselect(
    "Carriers",
    sorted(df["_Carrier"].dropna().astype(str).unique().tolist()),
    default=None,
)
if carriers:
    df = df[df["_Carrier"].isin(carriers)]

# -----------------------
# Milestone Gap Results
# -----------------------
pol_summary = build_summary(df, ["_Carrier","_POL Port"], "_POL_duration", "GateIn→ContainerLoaded (POL)")
pod_dg_summary = build_summary(df, ["_Carrier","_POD Port"], "_POD_dg_duration", "Discharge→GateOut (POD)")
pod_ge_summary = build_summary(df, ["_Carrier","_POD Port"], "_POD_ge_duration", "GateOut→EmptyReturn (POD)")

pol_summary = pol_summary.rename(columns={"_Carrier":"Carrier","_POL Port":"POL Port"})
pod_dg_summary = pod_dg_summary.rename(columns={"_Carrier":"Carrier","_POD Port":"POD Port"})
pod_ge_summary = pod_ge_summary.rename(columns={"_Carrier":"Carrier","_POD Port":"POD Port"})
pol_summary["POD Port"] = np.nan; pod_dg_summary["POL Port"] = np.nan; pod_ge_summary["POL Port"] = np.nan

results = pd.concat([
    pol_summary[["Carrier","Metric","POL Port","POD Port","count","avg_hours","mean_hours","median_hours","mode_hours"]],
    pod_dg_summary[["Carrier","Metric","POL Port","POD Port","count","avg_hours","mean_hours","median_hours","mode_hours"]],
    pod_ge_summary[["Carrier","Metric","POL Port","POD Port","count","avg_hours","mean_hours","median_hours","mode_hours"]],
], ignore_index=True)

unit_suffix = " (mins)" if unit == "minutes" else " (hrs)"
pretty = results.rename(columns={
    "avg_hours":"Average"+unit_suffix,
    "mean_hours":"Mean"+unit_suffix,
    "median_hours":"Median"+unit_suffix,
    "mode_hours":"Mode"+unit_suffix,
})
for c in ["Average"+unit_suffix,"Mean"+unit_suffix,"Median"+unit_suffix,"Mode"+unit_suffix]:
    pretty[c] = pd.to_numeric(pretty[c], errors='coerce').round(2)

st.subheader("Milestone Gap Results")
st.dataframe(pretty, use_container_width=True)
st.download_button(
    "Download milestone-gap results (CSV)",
    data=pretty.to_csv(index=False).encode('utf-8'),
    file_name="carrier_port_bottleneck_summary.csv",
    mime="text/csv"
)

# -----------------------
# Risk Summaries (LFD/OFD)
# -----------------------

def slack_group_stats(g: pd.Series) -> pd.Series:
    s = pd.to_numeric(g, errors='coerce').dropna()
    if len(s)==0:
        return pd.Series({"shipments":0,"late_count":0,"late_rate_%":np.nan,"median_slack_hours":np.nan,"avg_slack_hours":np.nan})
    late_count = (s < 0).sum()
    return pd.Series({
        "shipments": len(s),
        "late_count": late_count,
        "late_rate_%": round(100.0 * late_count / len(s), 2),
        "median_slack_hours": round(s.median(), 2),
        "avg_slack_hours": round(s.mean(), 2)
    })

st.subheader("Estimated LFD Risk (Carrier → POD)")
lfd_summary = (
    df.groupby(["_Carrier","_POD Port"])["_LFD_Slack_hours"]
      .apply(slack_group_stats)
      .reset_index()
      .rename(columns={"_Carrier":"Carrier","_POD Port":"POD Port"})
)
st.dataframe(lfd_summary, use_container_width=True)
st.download_button(
    "Download LFD summary (CSV)",
    data=lfd_summary.to_csv(index=False).encode('utf-8'),
    file_name="lfd_summary_by_carrier_pod.csv",
    mime="text/csv"
)

st.subheader("Estimated OFD Risk (Carrier → POL)")
ofd_summary = (
    df.groupby(["_Carrier","_POL Port"])["_OFD_Slack_hours"]
      .apply(slack_group_stats)
      .reset_index()
      .rename(columns={"_Carrier":"Carrier","_POL Port":"POL Port"})
)
st.dataframe(ofd_summary, use_container_width=True)
st.download_button(
    "Download OFD summary (CSV)",
    data=ofd_summary.to_csv(index=False).encode('utf-8'),
    file_name="ofd_summary_by_carrier_pol.csv",
    mime="text/csv"
)

# -----------------------
# Diagnostics (row-level export)
# -----------------------
with st.expander("Diagnostics (row-level export)"):
    diag_cols = {
        "Carrier": df["_Carrier"],
        "POL Port": df["_POL Port"],
        "POD Port": df["_POD Port"],
        "Gate In": gate_in_dt,
        "Container Loaded": container_loaded_dt,
        "Discharge": discharge_dt,
        "Gate Out": gate_out_dt,
        "Free Days (POD)": df["_FreeDays_POD"],
        "LFD (Estimated)": df["_Estimated_LFD"],
        "Slack vs LFD (hrs)": df["_LFD_Slack_hours"],
        "FD Source (POD)": df["_FD_POD_source"],
        "Free Days (POL)": df["_FreeDays_POL"],
        "OFD (Estimated)": df["_Estimated_OFD"],
        "Slack vs OFD (hrs)": df["_OFD_Slack_hours"],
        "FD Source (POL)": df["_FD_POL_source"],
    }
    diag = pd.DataFrame(diag_cols)
    neg_only = st.checkbox("Only rows with negative slack (POD or POL)", value=True)
    if neg_only:
        mask = (diag["Slack vs LFD (hrs)"] < 0) | (diag["Slack vs OFD (hrs)"] < 0)
        diag_view = diag.loc[mask].copy()
    else:
        diag_view = diag.copy()
    st.dataframe(diag_view, use_container_width=True)
    st.download_button(
        "Download diagnostics (CSV)",
        data=diag_view.to_csv(index=False).encode('utf-8'),
        file_name="slack_diagnostics_rows.csv",
        mime="text/csv"
    )

# ============================================
# D&D HOURS UI – COMPLETED SHIPMENTS ONLY
# ============================================

st.divider()
st.header("⏱ Demurrage & Detention Hours – Completed Shipments")

# Over free days = slack < 0 (LFD at POD)
dem_over_mask = df["_LFD_Slack_hours"] < 0
dem_within_mask = ~dem_over_mask

hours_over_dem = (-df.loc[dem_over_mask, "_LFD_Slack_hours"]).clip(lower=0)

total_ship = len(df)
count_over = dem_over_mask.sum()
count_within = dem_within_mask.sum()
pct_over = 100.0 * count_over / total_ship if total_ship else 0.0

total_over_hours = hours_over_dem.sum()
avg_over_hours = hours_over_dem.mean() if len(hours_over_dem) else 0.0

dem_unit = unit
det_unit = unit
total_det_hours = df["_POD_ge_duration"].sum()
avg_det_hours = df["_POD_ge_duration"].mean()

# --- OVERVIEW KPIs ---
st.subheader("Overview – Within vs Over Free Days (Demurrage at POD)")

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Total Completed Shipments", f"{total_ship:,}")
with col2:
    st.metric("Shipments Over Free Days (Demurrage)", f"{count_over:,}")
with col3:
    st.metric("% Shipments Over Free Days", f"{pct_over:.1f}%")
with col4:
    st.metric(
        "Avg Hours Over (overage only)",
        f"{avg_over_hours:,.1f} hrs" if count_over else "0.0 hrs"
    )

st.caption(
    "Over Free Days is determined using slack vs LFD (Last Free Day) at POD. "
    "This view assumes all shipments are completed."
)

colA, colB = st.columns(2)
with colA:
    total_dem_hours = df["_POD_dg_duration"].sum()
    avg_dem_hours = df["_POD_dg_duration"].mean()
    st.metric(f"Total Demurrage ({dem_unit})", f"{total_dem_hours:,.0f}")
    st.metric(
        f"Avg Demurrage per Shipment ({dem_unit})",
        f"{avg_dem_hours:,.1f}" if not np.isnan(avg_dem_hours) else "–"
    )
with colB:
    st.metric(f"Total Detention ({det_unit})", f"{total_det_hours:,.0f}")
    st.metric(
        f"Avg Detention per Shipment ({det_unit})",
        f"{avg_det_hours:,.1f}" if not np.isnan(avg_det_hours) else "–"
    )

st.divider()

# --- Tabs for detailed D&D views ---
tab_overview, tab_port_carrier, tab_lane, tab_shipments = st.tabs(
    ["Charts", "By Port & Carrier", "By Lane (POL → POD)", "Shipment Explorer"]
)

# 1) CHARTS TAB
with tab_overview:
    st.subheader("D&D Hours Distribution")

    dem_by_pod = (
        df.groupby("_POD Port")["_POD_dg_duration"]
          .sum()
          .sort_values(ascending=False)
          .head(10)
          .rename("Total Demurrage")
          .reset_index()
    )

    if not dem_by_pod.empty:
        st.markdown(f"**Top 10 POD Ports by Demurrage ({dem_unit})**")
        st.bar_chart(dem_by_pod.set_index("_POD Port")["Total Demurrage"])
    else:
        st.info("No demurrage data available to plot by POD Port.")

    st.markdown("---")

    det_by_pod = (
        df.groupby("_POD Port")["_POD_ge_duration"]
          .sum()
          .sort_values(ascending=False)
          .head(10)
          .rename("Total Detention")
          .reset_index()
    )

    if not det_by_pod.empty:
        st.markdown(f"**Top 10 POD Ports by Detention ({det_unit})**")
        st.bar_chart(det_by_pod.set_index("_POD Port")["Total Detention"])
    else:
        st.info("No detention data available to plot by POD Port.")

# 2) BY PORT & CARRIER TAB
with tab_port_carrier:
    st.subheader("Demurrage – Over vs Within Free Days (Carrier → POD)")

    def agg_demurrage_group(g: pd.DataFrame) -> pd.Series:
        shipments = len(g)
        over = (g["_LFD_Slack_hours"] < 0).sum()
        pct_over = 100.0 * over / shipments if shipments else np.nan
        hours_over = (-g.loc[g["_LFD_Slack_hours"] < 0, "_LFD_Slack_hours"]).clip(lower=0)
        avg_hours_over = hours_over.mean() if len(hours_over) else 0.0
        avg_dem = g["_POD_dg_duration"].mean()
        med_dem = g["_POD_dg_duration"].median()
        return pd.Series({
            "Shipments": shipments,
            "Shipments Over Free Days": over,
            "% Over Free Days": pct_over,
            f"Avg Demurrage ({dem_unit})": avg_dem,
            f"Median Demurrage ({dem_unit})": med_dem,
            "Avg Hours Over (demurrage)": avg_hours_over,
        })

    dem_group = (
        df.groupby(["_POD Port", "_Carrier"])
          .apply(agg_demurrage_group)
          .reset_index()
          .rename(columns={"_POD Port": "POD Port", "_Carrier": "Carrier"})
    )

    num_cols = [
        "% Over Free Days",
        f"Avg Demurrage ({dem_unit})",
        f"Median Demurrage ({dem_unit})",
        "Avg Hours Over (demurrage)",
    ]
    for c in num_cols:
        dem_group[c] = pd.to_numeric(dem_group[c], errors="coerce").round(2)

    if dem_group.empty:
        st.info("No data to show for Demurrage by POD Port & Carrier.")
    else:
        dem_group = dem_group.sort_values("% Over Free Days", ascending=False)
        st.dataframe(dem_group, use_container_width=True)

    st.download_button(
        "Download: Demurrage by POD Port & Carrier (CSV)",
        data=dem_group.to_csv(index=False).encode("utf-8"),
        file_name="demurrage_by_pod_carrier.csv",
        mime="text/csv",
    )

    st.markdown("---")
    st.subheader("Detention – Summary by POD Port & Carrier")

    def agg_detention_group(g: pd.DataFrame) -> pd.Series:
        shipments = len(g)
        avg_det = g["_POD_ge_duration"].mean()
        med_det = g["_POD_ge_duration"].median()
        max_det = g["_POD_ge_duration"].max()
        return pd.Series({
            "Shipments": shipments,
            f"Avg Detention ({det_unit})": avg_det,
            f"Median Detention ({det_unit})": med_det,
            f"Max Detention ({det_unit})": max_det,
        })

    det_group = (
        df.groupby(["_POD Port", "_Carrier"])
          .apply(agg_detention_group)
          .reset_index()
          .rename(columns={"_POD Port": "POD Port", "_Carrier": "Carrier"})
    )

    for c in [f"Avg Detention ({det_unit})", f"Median Detention ({det_unit})", f"Max Detention ({det_unit})"]:
        det_group[c] = pd.to_numeric(det_group[c], errors="coerce").round(2)

    if det_group.empty:
        st.info("No data to show for Detention by POD Port & Carrier.")
    else:
        det_group = det_group.sort_values(f"Avg Detention ({det_unit})", ascending=False)
        st.dataframe(det_group, use_container_width=True)

    st.download_button(
        "Download: Detention by POD Port & Carrier (CSV)",
        data=det_group.to_csv(index=False).encode("utf-8"),
        file_name="detention_by_pod_carrier.csv",
        mime="text/csv",
    )

# 3) BY LANE TAB
with tab_lane:
    st.subheader("D&D Hours by Lane (POL → POD) – Completed Shipments")

    def agg_lane_group(g: pd.DataFrame) -> pd.Series:
        shipments = len(g)
        over = (g["_LFD_Slack_hours"] < 0).sum()
        pct_over = 100.0 * over / shipments if shipments else np.nan
        hours_over = (-g.loc[g["_LFD_Slack_hours"] < 0, "_LFD_Slack_hours"]).clip(lower=0)
        avg_hours_over = hours_over.mean() if len(hours_over) else 0.0
        avg_dem = g["_POD_dg_duration"].mean()
        avg_det = g["_POD_ge_duration"].mean()
        return pd.Series({
            "Shipments": shipments,
            "Shipments Over Free Days": over,
            "% Over Free Days": pct_over,
            f"Avg Demurrage ({dem_unit})": avg_dem,
            f"Avg Detention ({det_unit})": avg_det,
            "Avg Hours Over (demurrage)": avg_hours_over,
        })

    lane_group = (
        df.groupby(["_POL Port", "_POD Port"])
          .apply(agg_lane_group)
          .reset_index()
          .rename(columns={"_POL Port": "POL Port", "_POD Port": "POD Port"})
    )

    for c in [
        "% Over Free Days",
        f"Avg Demurrage ({dem_unit})",
        f"Avg Detention ({det_unit})",
        "Avg Hours Over (demurrage)",
    ]:
        lane_group[c] = pd.to_numeric(lane_group[c], errors="coerce").round(2)

    if lane_group.empty:
        st.info("No data to show by lane.")
    else:
        lane_group = lane_group.sort_values("% Over Free Days", ascending=False)
        st.dataframe(lane_group, use_container_width=True)

    st.download_button(
        "Download: D&D Hours by Lane (CSV)",
        data=lane_group.to_csv(index=False).encode("utf-8"),
        file_name="dd_hours_by_lane.csv",
        mime="text/csv",
    )

# 4) SHIPMENT EXPLORER TAB
with tab_shipments:
    st.subheader("Shipment Explorer – Over vs Within Free Days (Demurrage at POD)")

    only_over = st.checkbox("Show only shipments over free days (demurrage)", value=True)

    diag_dd = pd.DataFrame({
        "Carrier": df["_Carrier"],
        "POL Port": df["_POL Port"],
        "POD Port": df["_POD Port"],
        "Discharge": discharge_dt,
        "Gate Out": gate_out_dt,
        "Empty Return": empty_return_dt,
        f"Demurrage ({dem_unit}) Discharge→Gate Out": df["_POD_dg_duration"],
        f"Detention ({det_unit}) Gate Out→Empty Return": df["_POD_ge_duration"],
        "Free Days (POD)": df["_FreeDays_POD"],
        "Slack vs LFD (hrs)": df["_LFD_Slack_hours"],
    })

    diag_dd["Demurrage Status"] = np.where(
        diag_dd["Slack vs LFD (hrs)"] < 0,
        "Over Free Days",
        "Within Free Days"
    )

    if only_over:
        diag_view = diag_dd[diag_dd["Demurrage Status"] == "Over Free Days"].copy()
    else:
        diag_view = diag_dd.copy()

    st.dataframe(diag_view, use_container_width=True)

    st.download_button(
        "Download: Shipment-level D&D Hours (CSV)",
        data=diag_view.to_csv(index=False).encode("utf-8"),
        file_name="shipment_dd_hours_completed.csv",
        mime="text/csv",
    )
