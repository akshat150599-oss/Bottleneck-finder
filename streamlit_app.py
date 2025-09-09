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
#   Primary: NGA World Port Index (WPI) via MSI API (CSV/JSON)
#   Secondary: UN/LOCODE (UNECE) via DataHub (CSV with coordinates)
#   Tertiary: Small embedded list as a last-resort safety net
# =============================================================
# Notes on sources (authoritative, public):
# - NGA WPI: https://msi.nga.mil/Publications/WPI (API documented via MSI API)
#   CSV endpoint pattern (as published on Postman examples):
#   https://msi.nga.mil/api/publications/world-port-index?output=csv
# - UN/LOCODE by UNECE: latest CSV mirrored via DataHub
#   https://datahub.io/core/un-locode (code-list.csv includes Coordinates)
# These URLs are used read-only and results are cached (24h) to minimize calls.

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
    # Some endpoints may return application/json but with CSV content; trust text
    data = r.text
    # Ensure not empty
    if not data or len(data) < 10:
        raise ValueError("Empty/invalid response")
    return pd.read_csv(StringIO(data))

@st.cache_data(ttl=60*60*24)
def fetch_wpi_ports() -> pd.DataFrame:
    """Fetch World Port Index (WPI) ports from NGA MSI API as a normalized DataFrame.

    Expected columns (we handle variants defensively):
      - Port name: 'portName', 'Port Name', 'PORT_NAME', 'name'
      - Country: 'countryName', 'Country', 'COUNTRY'
      - Latitude: 'latitude', 'Latitude', 'LATITUDE'
      - Longitude: 'longitude', 'Longitude', 'LONGITUDE'
    """
    df = _http_get_csv(WPI_CSV_URL)
    # Normalize columns
    cols = {c.lower(): c for c in df.columns}
    def pick(*opts):
        for o in opts:
            if o.lower() in cols:
                return cols[o.lower()]
        return None
    c_port = pick('portname','port name','port','name','main_port_name')
    c_ctry = pick('countryname','country','country name')
    c_lat  = pick('latitude','lat','y')
    c_lon  = pick('longitude','lon','x')
    if not (c_port and c_ctry and c_lat and c_lon):
        # Try to coerce from possible ArcGIS export field names
        if 'PORT_NAME' in df.columns: c_port = 'PORT_NAME'
        if 'COUNTRY' in df.columns: c_ctry = 'COUNTRY'
        if 'LATITUDE' in df.columns: c_lat = 'LATITUDE'
        if 'LONGITUDE' in df.columns: c_lon = 'LONGITUDE'
    if not (c_port and c_ctry and c_lat and c_lon):
        raise ValueError("WPI CSV missing expected columns")
    out = pd.DataFrame({
        'Port': df[c_port].astype(str).str.strip(),
        'Country': df[c_ctry].astype(str).str.strip(),
        'Latitude': pd.to_numeric(df[c_lat], errors='coerce'),
        'Longitude': pd.to_numeric(df[c_lon], errors='coerce'),
    })
    out = out.dropna(subset=['Latitude','Longitude']).drop_duplicates()
    return out

# UN/LOCODE has Degrees+Minutes strings like 5124N 00005W. Convert to decimal.

def _dm_to_decimal(dm: str):
    dm = str(dm).strip()
    if not dm or dm == 'nan':
        return np.nan, np.nan
    try:
        parts = dm.split()
        if len(parts) != 2:
            return np.nan, np.nan
        latp, lonp = parts
        # LAT e.g., 5124N -> 51 deg, 24 min
        deg = int(latp[:-3]); minutes = int(latp[-3:-1]); hemi = latp[-1]
        lat = deg + minutes/60.0
        if hemi in ('S','s'):
            lat = -lat
        # LON e.g., 00005W -> 0 deg, 5 min
        deg = int(lonp[:-3]); minutes = int(lonp[-3:-1]); hemi = lonp[-1]
        lon = deg + minutes/60.0
        if hemi in ('W','w'):
            lon = -lon
        return lat, lon
    except Exception:
        return np.nan, np.nan

@st.cache_data(ttl=60*60*24)
def fetch_unlocode_ports() -> pd.DataFrame:
    df = _http_get_csv(UNLOCODE_URL)
    # Expect columns: Country (ISO2), Location, Name, Coordinates, Function
    cols = {c.lower(): c for c in df.columns}
    c_name = cols.get('name') or cols.get('namewodiacritics')
    c_ctry = cols.get('country')
    c_coords = cols.get('coordinates')
    c_func = cols.get('function')
    if not (c_name and c_ctry and c_coords):
        raise ValueError('UN/LOCODE CSV missing expected columns')
    # Keep sea ports only if Function contains '1'
    if c_func and c_func in df.columns:
        df = df[df[c_func].astype(str).str.contains('1', na=False)]
    latlons = df[c_coords].apply(_dm_to_decimal)
    lat = latlons.apply(lambda t: t[0])
    lon = latlons.apply(lambda t: t[1])
    out = pd.DataFrame({
        'Port': df[c_name].astype(str).str.strip(),
        'Country': df[c_ctry].astype(str).str.strip(),  # ISO2
        'Latitude': lat,
        'Longitude': lon,
    })
    out = out.dropna(subset=['Latitude','Longitude']).drop_duplicates()
    return out

@st.cache_data(ttl=60*60*24)
def get_port_master(prefer: str = 'WPI') -> pd.DataFrame:
    """Return a Port Master table with columns: Port, Country, Latitude, Longitude.
    `prefer` in {'WPI','UNLOCODE'} selects which online source to try first.
    Falls back to the other, then to a tiny embedded list.
    """
    try:
        if prefer == 'WPI':
            return fetch_wpi_ports()
        else:
            return fetch_unlocode_ports()
    except Exception:
        # try the other one
        try:
            if prefer == 'WPI':
                return fetch_unlocode_ports()
            else:
                return fetch_wpi_ports()
        except Exception:
            # last resort
            return pd.read_csv(StringIO(EMBEDDED_PORT_MASTER_CSV))

# =============================================================
# Core App (keeps all previous views; adds tooltips + online Port Master)
# =============================================================

st.set_page_config(page_title="Ocean Bottleneck Analyzer", layout="wide")
st.title("📦 Ocean Bottleneck Analyzer")
st.caption("Identify bottlenecks, free-day risk (LFD/OFD), and get smart suggestions")

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
(Stats: count, avg/mean, median, mode; mode rounded to 1 decimal.)

**Free Day windows**
- **Estimated LFD (POD)** = Discharge + **Free Days (POD)** → **Slack vs LFD** = LFD − Gate Out  
- **Estimated OFD (POL)** = Gate In + **Free Days (POL)** → **Slack vs OFD** = OFD − Container Loaded  
- Choose **Calendar** vs **Business** days (end-of-day policy to minimize false negatives)  
- Optional **mapping CSVs** override defaults at **Carrier+Port / Port-only / Carrier-only** levels.
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
    df = df.copy(); df.columns = [str(c).strip() for c in df.columns]; return df

def find_col(df: pd.DataFrame, name_variants):
    cand = {c.lower(): c for c in df.columns}
    for v in name_variants:
        if v.lower() in cand: return cand[v.lower()]
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
# Settings + Tooltips
# -----------------------
st.divider(); st.subheader("Settings")
dayfirst = st.checkbox("Dates are day-first (DD/MM/YYYY)", value=True, help="Your files are parsed in DD/MM/YY HH:MM:SS AM/PM format by default.")
unit = st.selectbox("Units", ["hours","minutes"], index=0, help="Choose the unit for all reported durations.")
mode_round = st.slider("Mode rounding (units)", 0, 3, 1, help="We round to this many decimals before computing the mode.")
neg_policy = st.selectbox(
    "Milestone durations: negatives",
    ["Keep (could be data issue)","Treat as NaN (drop from stats)"],
    index=1,
    help="If a gap is negative (end < start), it's likely a data/timezone issue. Choose whether to drop those rows."
)
neg_tol = st.slider(
    "Slack tolerance (clip small negatives to 0)", 0.0, 6.0, 2.0, 0.5,
    help="Applies to LFD/OFD slack only, to avoid flagging near-zero negatives due to timestamp granularity."
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
# Free-Day windows (LFD/OFD) + tooltips for mapping CSVs
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
            mask = ~pd.isna(combo); days = np.where(mask, combo, days); source = np.where(mask, "carrier+port", source)
        if p_only:
            pod = port_key.map(lambda k: p_only.get(k, np.nan))
            mask = ~pd.isna(pod); days = np.where(mask, pod, days); source = np.where(mask, f"{side_label}-only", source)
        if c_only:
            car = car_key.map(lambda k: c_only.get(k, np.nan))
            mask = ~pd.isna(car); days = np.where(mask, car, days); source = np.where(mask, "carrier-only", source)
    return pd.to_numeric(days, errors="coerce"), pd.Series(source, index=df_in.index)

def clip_small_negatives_to_zero(series: pd.Series, tol_hours: float) -> pd.Series:
    return series.mask((series < 0) & (series >= -tol_hours), 0)

# --- Estimated LFD (POD)
st.divider(); st.subheader("Estimated LFD (POD)")
c1, c2, c3 = st.columns([1,1,2])
with c1:
    default_free_days_pod = st.number_input("Default POD Free Days", 0, 60, 5, 1,
        help="If no mapping is provided, we assume this many free days at POD.")
with c2:
    business_days_pod = st.checkbox("POD counts Business Days (skip Sat/Sun)?", value=False,
        help="If on, we add business days using a Monday–Friday calendar before setting LFD at end-of-day.")
with c3:
    fd_map_pod_file = st.file_uploader(
        "Optional POD Free-Days mapping CSV (POD Port, Carrier Name, Free Days)", type=["csv"], key="podmap",
        help=(
            "CSV schema (no header order required):\n"
            "- POD Port (text) — must match your data's POD names\n"
            "- Carrier Name (text) — optional; leave blank for port-wide rule\n"
            "- Free Days (integer)\n\n"
            "Example:\n"
            "POD Port,Carrier Name,Free Days\n"
            "Los Angeles,ACME LINE,4\n"
            "Los Angeles,,5\n"
            "Rotterdam,AnyCarrier,6\n"
        )
    )

pod_mapper = None
if fd_map_pod_file is not None:
    try:
        df_pod_map = pd.read_csv(fd_map_pod_file)
        pod_mapper = build_free_days_mapper(df_pod_map, side="POD")
        st.success("Loaded POD Free-Days mapping.")
    except Exception as e:
        st.warning(f"Could not read POD mapping CSV: {e}")

df["_FreeDays_POD"], df["_FD_POD_source"] = apply_free_days(df, "_Carrier", "_POD Port",
                                                            default_free_days_pod, pod_mapper, "POD")
df["_Estimated_LFD"] = [add_days_eod(d, n, business_days_pod) for d, n in zip(discharge_dt, df["_FreeDays_POD"])]
df["_LFD_Slack_hours"] = (df["_Estimated_LFD"] - gate_out_dt).dt.total_seconds() / 3600.0
df["_LFD_Slack_hours"] = clip_small_negatives_to_zero(df["_LFD_Slack_hours"], neg_tol)

pod_cov = df["_FD_POD_source"].value_counts(dropna=False).to_dict()
st.caption(f"POD Free Days source breakdown: {pod_cov}")

# --- Estimated OFD (POL)
st.divider(); st.subheader("Estimated OFD (POL)")
c1, c2, c3 = st.columns([1,1,2])
with c1:
    default_free_days_pol = st.number_input("Default POL Free Days (Origin)", 0, 60, 3, 1,
        help="If no mapping is provided, we assume this many free days at POL.")
with c2:
    business_days_pol = st.checkbox("POL counts Business Days (skip Sat/Sun)?", value=False,
        help="If on, we add business days using a Monday–Friday calendar before setting OFD at end-of-day.")
with c3:
    fd_map_pol_file = st.file_uploader(
        "Optional POL Free-Days mapping CSV (POL Port, Carrier Name, Free Days)", type=["csv"], key="polmap",
        help=(
            "CSV schema (no header order required):\n"
            "- POL Port (text) — must match your data's POL names\n"
            "- Carrier Name (text) — optional; leave blank for port-wide rule\n"
            "- Free Days (integer)\n\n"
            "Example:\n"
            "POL Port,Carrier Name,Free Days\n"
            "Nhava Sheva (JNPT),ACME LINE,3\n"
            "Nhava Sheva (JNPT),,4\n"
            "Mundra,AnyCarrier,5\n"
        )
    )

pol_mapper = None
if fd_map_pol_file is not None:
    try:
        df_pol_map = pd.read_csv(fd_map_pol_file)
        pol_mapper = build_free_days_mapper(df_pol_map, side="POL")
        st.success("Loaded POL Free-Days mapping.")
    except Exception as e:
        st.warning(f"Could not read POL mapping CSV: {e}")


df["_FreeDays_POL"], df["_FD_POL_source"] = apply_free_days(df, "_Carrier", "_POL Port",
                                                            default_free_days_pol, pol_mapper, "POL")
df["_Estimated_OFD"] = [add_days_eod(d, n, business_days_pol) for d, n in zip(gate_in_dt, df["_FreeDays_POL"])]
df["_OFD_Slack_hours"] = (df["_Estimated_OFD"] - container_loaded_dt).dt.total_seconds() / 3600.0
df["_OFD_Slack_hours"] = clip_small_negatives_to_zero(df["_OFD_Slack_hours"], neg_tol)

pol_cov = df["_FD_POL_source"].value_counts(dropna=False).to_dict()
st.caption(f"POL Free Days source breakdown: {pol_cov}")

# -----------------------
# Filters
# -----------------------
st.subheader("Filters")
carriers = st.multiselect("Carriers", sorted(df["_Carrier"].dropna().astype(str).unique().tolist()), default=None,
    help="Filter all downstream tables by selected carriers.")
if carriers: df = df[df["_Carrier"].isin(carriers)]

# -----------------------
# Milestone Gap Results (kept)
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
st.download_button("Download milestone-gap results (CSV)", data=pretty.to_csv(index=False).encode('utf-8'),
                   file_name="carrier_port_bottleneck_summary.csv", mime="text/csv")

# -----------------------
# Risk Summaries (kept)
# -----------------------

def slack_group_stats(g: pd.Series) -> pd.Series:
    s = pd.to_numeric(g, errors='coerce').dropna()
    if len(s)==0: return pd.Series({"shipments":0,"late_count":0,"late_rate_%":np.nan,"median_slack_hours":np.nan,"avg_slack_hours":np.nan})
    late_count = (s < 0).sum()
    return pd.Series({
        "shipments": len(s),
        "late_count": late_count,
        "late_rate_%": round(100.0 * late_count / len(s), 2),
        "median_slack_hours": round(s.median(), 2),
        "avg_slack_hours": round(s.mean(), 2)
    })

st.subheader("Estimated LFD Risk (Carrier → POD)")
lfd_summary = (df.groupby(["_Carrier","_POD Port"])["_LFD_Slack_hours"].apply(slack_group_stats).reset_index()
                 .rename(columns={"_Carrier":"Carrier","_POD Port":"POD Port"}))
st.dataframe(lfd_summary, use_container_width=True)
st.download_button("Download LFD summary (CSV)", data=lfd_summary.to_csv(index=False).encode('utf-8'),
                   file_name="lfd_summary_by_carrier_pod.csv", mime="text/csv")

st.subheader("Estimated OFD Risk (Carrier → POL)")
ofd_summary = (df.groupby(["_Carrier","_POL Port"])["_OFD_Slack_hours"].apply(slack_group_stats).reset_index()
                 .rename(columns={"_Carrier":"Carrier","_POL Port":"POL Port"}))
st.dataframe(ofd_summary, use_container_width=True)
st.download_button("Download OFD summary (CSV)", data=ofd_summary.to_csv(index=False).encode('utf-8'),
                   file_name="ofd_summary_by_carrier_pol.csv", mime="text/csv")

# -----------------------
# Diagnostics (kept)
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
    neg_only = st.checkbox("Only rows with negative slack (POD or POL)", value=True,
        help="Quickly isolate potential fee risk where slack < 0.")
    if neg_only:
        mask = (diag["Slack vs LFD (hrs)"] < 0) | (diag["Slack vs OFD (hrs)"] < 0)
        diag_view = diag.loc[mask].copy()
    else:
        diag_view = diag.copy()
    st.dataframe(diag_view, use_container_width=True)
    st.download_button("Download diagnostics (CSV)", data=diag_view.to_csv(index=False).encode('utf-8'),
                       file_name="slack_diagnostics_rows.csv", mime="text/csv")

# =========================
# SUGGESTIONS (NEW SECTION)
# =========================

st.divider(); st.header("🧠 Suggestions")

# Shared knobs
st.subheader("Settings for Suggestions")
colA, colB, colC, colD = st.columns([1,1,1,1])
with colA:
    min_count_side = st.number_input("Min shipments per side", 1, 100, 10, 1,
        help="Carrier must have at least this many shipments on POL and on POD sides of the lane.")
with colB:
    improve_pct = st.slider("Required improvement vs lane avg (%)", 0, 50, 10, 1,
        help="Carrier must beat lane average by this % on BOTH POL & POD.")
with colC:
    w_pol = st.number_input("Weight: POL dwell", 0.0, 1.0, 0.5, 0.1,
        help="Weight of POL GateIn→Loaded improvement in the composite score.")
with colD:
    w_pod = st.number_input("Weight: POD dwell", 0.0, 1.0, 0.5, 0.1,
        help="Weight of POD Discharge→GateOut improvement in the composite score.")

w_sum = w_pol + w_pod
w_pol, w_pod = ((0.5,0.5) if w_sum == 0 else (w_pol / w_sum, w_pod / w_sum))
unit_suffix_plain = "mins" if unit == "minutes" else "hrs"

# 1) Best Carrier for Lane (POL→POD) — using AVERAGE
st.subheader("1) Best Carrier for Lane (POL → POD) — using AVERAGE")

lane_car = (
    df.groupby(["_Carrier", "_POL Port", "_POD Port"])
      .agg(POL_mean=("_POL_duration", "mean"),
           POL_cnt =("_POL_duration", lambda s: pd.to_numeric(s, errors="coerce").notna().sum()),
           POD_mean=("_POD_dg_duration", "mean"),
           POD_cnt =("_POD_dg_duration", lambda s: pd.to_numeric(s, errors="coerce").notna().sum()))
      .reset_index()
)

lane_base = (
    df.groupby(["_POL Port", "_POD Port"])
      .agg(lane_POL_mean=("_POL_duration", "mean"),
           lane_POD_mean=("_POD_dg_duration", "mean"))
      .reset_index()
)

best_lane = (
    lane_car.merge(lane_base, on=["_POL Port", "_POD Port"], how="left")
            .rename(columns={"_Carrier":"Carrier","_POL Port":"POL","_POD Port":"POD"})
)

thr = (100 - improve_pct) / 100.0
eligible = (
    (best_lane["POL_cnt"] >= min_count_side) &
    (best_lane["POD_cnt"] >= min_count_side) &
    (best_lane["POL_mean"] <= best_lane["lane_POL_mean"] * thr) &
    (best_lane["POD_mean"] <= best_lane["lane_POD_mean"] * thr)
)

cand = best_lane.loc[eligible].copy()

eps = 1e-9
cand["imp_POL"] = (cand["lane_POL_mean"] - cand["POL_mean"]) / (cand["lane_POL_mean"] + eps)
cand["imp_POD"] = (cand["lane_POD_mean"] - cand["POD_mean"]) / (cand["lane_POD_mean"] + eps)
cand["Score"]   = w_pol * cand["imp_POL"] + w_pod * cand["imp_POD"]

if cand.empty:
    st.info("No carrier qualifies yet under current thresholds. Try lowering 'Min shipments' or 'Required improvement'.")
else:
    out = cand[["POL","POD","Carrier","POL_cnt","POL_mean","lane_POL_mean","POD_cnt","POD_mean","lane_POD_mean","imp_POL","imp_POD","Score"]].copy()
    out = out.rename(columns={
        "POL_cnt": f"Count POL",
        "POD_cnt": f"Count POD",
        "POL_mean": f"Avg POL ({unit_suffix_plain})",
        "lane_POL_mean": f"Lane Avg POL ({unit_suffix_plain})",
        "POD_mean": f"Avg POD ({unit_suffix_plain})",
        "lane_POD_mean": f"Lane Avg POD ({unit_suffix_plain})",
        "imp_POL": "Improvement POL",
        "imp_POD": "Improvement POD",
    })
    for c in [f"Avg POL ({unit_suffix_plain})", f"Lane Avg POL ({unit_suffix_plain})",
              f"Avg POD ({unit_suffix_plain})", f"Lane Avg POD ({unit_suffix_plain})",
              "Improvement POL", "Improvement POD", "Score"]:
        out[c] = pd.to_numeric(out[c], errors='coerce').round(3)
    out = out.sort_values("Score", ascending=False).reset_index(drop=True)
    st.dataframe(out, use_container_width=True)
    st.download_button("Download: Best Carrier for Lane (CSV)", out.to_csv(index=False).encode('utf-8'),
                       file_name="best_carrier_for_lane_avg.csv", mime="text/csv")

# 2) Alternate Lane Suggestions — now using ONLINE Port Master by default
st.subheader("2) Alternate Lane Suggestions (same country, from authoritative online source)")

col0, col1, col2, col3 = st.columns([1,1,1,1])
with col0:
    pm_prefer = st.selectbox("Port Master source (default: WPI)", ["WPI","UNLOCODE"], index=0,
        help="WPI = NGA World Port Index; UN/LOCODE = UNECE code list with coordinates. Both are public + trusted.")
with col1:
    max_km = st.number_input("Max neighbor distance (km)", 10, 2000, 400, 10,
        help="We only consider same-country ports within this radius as alternates.")
with col2:
    alt_improve_pct = st.slider("Min improvement to suggest (%)", 0, 50, 15, 1,
        help="Suggested lane must be at least this % faster on the relevant side (avg).")
with col3:
    alt_min_count = st.number_input("Min shipments on suggested lane", 1, 100, 10, 1,
        help="Suggested lane must have at least this many shipments in your data.")

# Optional: allow user-provided overrides to complement online data
pm_file = st.file_uploader(
    "Optional Port Master override CSV (Port, Country, Latitude, Longitude)", type=["csv"], key="portmaster",
    help=(
        "Use to add missing port names or custom coordinates. Columns required: Port, Country, Latitude, Longitude.\n"
        "Country can be ISO2 code or country name; both are accepted."
    )
)

# Load port master (online first, override with uploaded rows if any)
try:
    port_master = get_port_master(pm_prefer)
    if pm_file is not None:
        try:
            df_ovr = pd.read_csv(pm_file)
            cols = {c.lower(): c for c in df_ovr.columns}
            need = [cols.get('port'), cols.get('country'), cols.get('latitude'), cols.get('longitude')]
            if all(need):
                df_ovr = df_ovr.rename(columns={
                    need[0]:'Port', need[1]:'Country', need[2]:'Latitude', need[3]:'Longitude'
                })[['Port','Country','Latitude','Longitude']]
                port_master = pd.concat([port_master, df_ovr], ignore_index=True).dropna(subset=['Latitude','Longitude']).drop_duplicates()
            else:
                st.warning("Override CSV missing one of: Port, Country, Latitude, Longitude.")
        except Exception as e:
            st.warning(f"Could not read override CSV: {e}")
except Exception as e:
    st.error(f"Failed to load online Port Master. Using tiny embedded list. Error: {e}")
    port_master = pd.read_csv(StringIO(EMBEDDED_PORT_MASTER_CSV))

# Build quick lookup by normalized port name + country key
port_master['_pkey'] = port_master['Port'].astype(str).str.strip().str.lower()
port_master['_ckey'] = port_master['Country'].astype(str).str.strip().str.upper()

# If the online source returned country names instead of ISO2, try to keep both forms
# Simple country-name-to-ISO2 helper (minimal set, extend as needed)
COUNTRY_MAP = {
    'INDIA':'IN','UNITED STATES':'US','UNITED KINGDOM':'GB','SINGAPORE':'SG','MALAYSIA':'MY','UNITED ARAB EMIRATES':'AE',
    'NETHERLANDS':'NL','GERMANY':'DE','FRANCE':'FR','SPAIN':'ES','ITALY':'IT','GREECE':'GR','CANADA':'CA','MEXICO':'MX',
}
port_master['_ckey'] = port_master['_ckey'].replace(COUNTRY_MAP)

# Haversine distance

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1; dlon = lon2 - lon1
    a = np.sin(dlat/2)**2 + np.cos(lat1)*np.cos(lat2)*np.sin(dlon/2)**2
    c = 2*np.arcsin(np.sqrt(a))
    return R * c

lane_stats = (
    df.groupby(["_POL Port","_POD Port"])
      .agg(POL_mean=("_POL_duration","mean"),
           POL_cnt =("_POL_duration", lambda s: pd.to_numeric(s, errors="coerce").notna().sum()),
           POD_mean=("_POD_dg_duration","mean"),
           POD_cnt =("_POD_dg_duration", lambda s: pd.to_numeric(s, errors="coerce").notna().sum()))
      .reset_index().rename(columns={"_POL Port":"POL","_POD Port":"POD"})
)

# Helper to find nearest same-country ports given a port string

def nearest_same_country(port_name: str, side: str, maxkm: float):
    # side: 'POL' or 'POD' (just for message clarity)
    row = port_master[port_master['_pkey'] == str(port_name).strip().lower()]
    if row.empty:
        return pd.DataFrame(columns=port_master.columns)
    lat0, lon0, ctry = float(row.iloc[0]['Latitude']), float(row.iloc[0]['Longitude']), row.iloc[0]['_ckey']
    same = port_master[port_master['_ckey'] == ctry].copy()
    same['dist_km'] = haversine(lat0, lon0, same['Latitude'].astype(float), same['Longitude'].astype(float))
    same = same[same['_pkey'] != str(port_name).strip().lower()]
    return same[same['dist_km'] <= maxkm].sort_values('dist_km')

rows = []
for _, r in lane_stats.iterrows():
    pol, pod = r['POL'], r['POD']
    cur_pol_mean, cur_pod_mean = r['POL_mean'], r['POD_mean']

    # Alternative POL (same country, lane must exist with same POD)
    cand_pol = nearest_same_country(pol, 'POL', max_km)
    for _, cpol in cand_pol.iterrows():
        alt_pol = cpol['Port']; dist = cpol['dist_km']
        alt_row = lane_stats[(lane_stats['POL'] == alt_pol) & (lane_stats['POD'] == pod)]
        if alt_row.empty: continue
        alt_mean = alt_row.iloc[0]['POL_mean']; alt_cnt = alt_row.iloc[0]['POL_cnt']
        if pd.isna(alt_mean) or alt_cnt < alt_min_count: continue
        imp = (cur_pol_mean - alt_mean) / (cur_pol_mean + 1e-9)
        if imp*100 >= alt_improve_pct:
            rows.append({
                'Side Changed':'POL', 'Current POL':pol, 'Suggested POL':alt_pol, 'POD (fixed)':pod,
                'Distance (km)': round(float(dist), 1),
                f'Current Avg POL ({unit_suffix_plain})': round(float(cur_pol_mean),2),
                f'Suggested Avg POL ({unit_suffix_plain})': round(float(alt_mean),2),
                'Improvement %': round(float(imp*100),1), 'Suggested Count': int(alt_cnt)
            })

    # Alternative POD (same country, lane must exist with same POL)
    cand_pod = nearest_same_country(pod, 'POD', max_km)
    for _, cpod in cand_pod.iterrows():
        alt_pod = cpod['Port']; dist = cpod['dist_km']
        alt_row = lane_stats[(lane_stats['POL'] == pol) & (lane_stats['POD'] == alt_pod)]
        if alt_row.empty: continue
        alt_mean = alt_row.iloc[0]['POD_mean']; alt_cnt = alt_row.iloc[0]['POD_cnt']
        if pd.isna(alt_mean) or alt_cnt < alt_min_count: continue
        imp = (cur_pod_mean - alt_mean) / (cur_pod_mean + 1e-9)
        if imp*100 >= alt_improve_pct:
            rows.append({
                'Side Changed':'POD', 'POL (fixed)':pol, 'Current POD':pod, 'Suggested POD':alt_pod,
                'Distance (km)': round(float(dist), 1),
                f'Current Avg POD ({unit_suffix_plain})': round(float(cur_pod_mean),2),
                f'Suggested Avg POD ({unit_suffix_plain})': round(float(alt_mean),2),
                'Improvement %': round(float(imp*100),1), 'Suggested Count': int(alt_cnt)
            })

alt_df = pd.DataFrame(rows)
if alt_df.empty:
    st.info("No alternate lane met the distance, improvement, and sample-size thresholds.")
else:
    st.dataframe(alt_df.sort_values("Improvement %", ascending=False).reset_index(drop=True), use_container_width=True)
    st.download_button("Download: Alternate Lane Suggestions (CSV)", alt_df.to_csv(index=False).encode('utf-8'),
                       file_name="alternate_lane_suggestions.csv", mime="text/csv")

# 3) Bottleneck Points
st.subheader("3) Bottleneck Points (choke points)")

topN = st.slider("Show Top N per list", 5, 50, 10, 1, help="How many rows to display for each worst-performing list.")

lane_car_full = (
    df.groupby(["_Carrier","_POL Port","_POD Port"])
      .agg(Avg_POL=("_POL_duration","mean"), Cnt_POL=("_POL_duration", lambda s: pd.to_numeric(s, errors='coerce').notna().sum()),
           Avg_POD=("_POD_dg_duration","mean"), Cnt_POD=("_POD_dg_duration", lambda s: pd.to_numeric(s, errors='coerce').notna().sum()),
           Avg_RET=("_POD_ge_duration","mean"), Cnt_RET=("_POD_ge_duration", lambda s: pd.to_numeric(s, errors='coerce').notna().sum()))
      .reset_index().rename(columns={"_Carrier":"Carrier","_POL Port":"POL","_POD Port":"POD"})
)

worst_pol = lane_car_full[lane_car_full["Cnt_POL"] >= min_count_side].copy().sort_values("Avg_POL", ascending=False).head(topN)
worst_pol = worst_pol.rename(columns={"Avg_POL": f"Avg POL ({unit_suffix_plain})", "Cnt_POL":"Count POL"})

worst_pod = lane_car_full[lane_car_full["Cnt_POD"] >= min_count_side].copy().sort_values("Avg_POD", ascending=False).head(topN)
worst_pod = worst_pod.rename(columns={"Avg_POD": f"Avg POD ({unit_suffix_plain})", "Cnt_POD":"Count POD"})

worst_ret = lane_car_full[lane_car_full["Cnt_RET"] >= min_count_side].copy().sort_values("Avg_RET", ascending=False).head(topN)
worst_ret = worst_ret.rename(columns={"Avg_RET": f"Avg Return ({unit_suffix_plain})", "Cnt_RET":"Count Return"})

colX, colY, colZ = st.columns(3)
with colX:
    st.markdown("**Top POL choke points**")
    st.dataframe(worst_pol, use_container_width=True)
with colY:
    st.markdown("**Top POD choke points**")
    st.dataframe(worst_pod, use_container_width=True)
with colZ:
    st.markdown("**Top Return-step choke points**")
    st.dataframe(worst_ret, use_container_width=True)

# Late-rate hotspots using existing summaries
try:
    lfd_hot = lfd_summary.sort_values("late_rate_%", ascending=False).head(topN)
    st.markdown("**Top LFD late-rate hotspots (Carrier → POD)**")
    st.dataframe(lfd_hot, use_container_width=True)
except Exception:
    pass

try:
    ofd_hot = ofd_summary.sort_values("late_rate_%", ascending=False).head(topN)
    st.markdown("**Top OFD late-rate hotspots (Carrier → POL)**")
    st.dataframe(ofd_hot, use_container_width=True)
except Exception:
    pass
