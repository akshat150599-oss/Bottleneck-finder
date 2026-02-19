# =============================================================
# D&D Focused Streamlit App (v3 - Full App)
# - Demurrage at POD (Discharge -> Gate Out)
# - Demurrage at POL (Gate In -> Container Loaded)
# - Detention at POD (Gate Out -> Empty Return)
#
# v3 Requested upgrades (ALL):
# 1) Slack converted to DAYS everywhere (single unit across app)
# 2) High-level KPIs show AVG SLACK (signed) per shipment (not avg durations)
# 3) Overtime distribution charts:
#       X = "Days Over" (bucketed), Y = # Shipments (only slack > 0)
# 4) Drilldown under charts:
#       Choose metric + days-over bucket -> show shipment list + shipment counts + downloads
#       Optional Carrier/Port filters (top-volume first)
# 5) Global filters (Carrier/POL/POD) ordered top-volume first; default = All
# 6) By Port & Carrier / By Lane summaries use slack in DAYS and include signed avg slack
# 7) Download ALL shipments above free days (slack>0) for selected metric, with column:
#       "Over Free Days" (days over; positive) + "Over Free Days Bucket" (ceil bucket)
# =============================================================

import pandas as pd
import numpy as np
from io import BytesIO
from pandas.tseries.offsets import BDay

# --- Streamlit import + divider backfill ---
try:
    import streamlit as st
except Exception as e:
    raise RuntimeError("Streamlit is required. Try: pip install streamlit") from e

if not hasattr(st, "divider"):
    def _divider():
        st.markdown("---")
    st.divider = _divider

# =============================================================
# Helper functions
# =============================================================

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
            st.error("`.xlsx` needs **openpyxl**. Install it or upload CSV instead.")
        else:
            st.error("`.xls` needs **xlrd==1.2.0**. Install it or upload CSV instead.")
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

def end_of_day(ts: pd.Timestamp) -> pd.Timestamp:
    if pd.isna(ts):
        return pd.NaT
    return ts.normalize() + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)

def add_days_eod(start_ts: pd.Timestamp, n_days: int, business_days: bool):
    if pd.isna(start_ts) or pd.isna(n_days):
        return pd.NaT
    n = int(n_days)
    if business_days:
        target = (start_ts.normalize() + BDay(n)).to_pydatetime()
        target = pd.Timestamp(target)
    else:
        target = start_ts.normalize() + pd.to_timedelta(n, unit="D")
    return end_of_day(target)

def norm_key(s):
    return (str(s).strip().lower()) if pd.notna(s) else ""

def build_free_days_mapper(df_map: pd.DataFrame, side: str):
    cols = {c.lower(): c for c in df_map.columns}

    def pick(*opts):
        for o in opts:
            if o.lower() in cols:
                return cols[o.lower()]
        return None

    if side == "POD":
        port_col = pick("POD Port", "POD", "Port of Discharge")
    else:
        port_col = pick("POL Port", "POL", "Port of Loading")
    car_col = pick("Carrier Name", "Carrier")
    days_col = pick("Free Days", "FreeDays", "free_days", "Days", "Demurrage Free Days")

    if not days_col:
        return None

    cp, p_only, c_only = {}, {}, {}
    for _, r in df_map.iterrows():
        try:
            days = int(r.get(days_col, np.nan))
        except Exception:
            continue
        if pd.isna(days):
            continue

        port = norm_key(r.get(port_col, "")) if port_col else ""
        car = norm_key(r.get(car_col, "")) if car_col else ""

        if car and port:
            cp[(car, port)] = days
        elif port:
            p_only[port] = days
        elif car:
            c_only[car] = days

    return cp, p_only, c_only

def apply_free_days(df_in: pd.DataFrame, car_col: str, port_col: str,
                    default_days: int, mapping_tuple, side_label: str):
    car_key = df_in[car_col].map(norm_key)
    port_key = df_in[port_col].map(norm_key)

    days = pd.Series(default_days, index=df_in.index, dtype="float")
    source = pd.Series("default", index=df_in.index, dtype="object")

    if mapping_tuple:
        cp, p_only, c_only = mapping_tuple

        # Carrier + Port
        if cp:
            combo_key = car_key + "||" + port_key

            def _lookup_combo(k):
                parts = k.split("||", 1)
                if len(parts) != 2:
                    return np.nan
                return cp.get((parts[0], parts[1]), np.nan)

            combo_days = combo_key.map(_lookup_combo)
            mask = ~pd.isna(combo_days)
            days = np.where(mask, combo_days, days)
            source = np.where(mask, "carrier+port", source)

        # Port-only
        if p_only:
            port_days = port_key.map(lambda k: p_only.get(k, np.nan))
            mask = ~pd.isna(port_days)
            days = np.where(mask, port_days, days)
            source = np.where(mask, f"{side_label}-only", source)

        # Carrier-only
        if c_only:
            car_days = car_key.map(lambda k: c_only.get(k, np.nan))
            mask = ~pd.isna(car_days)
            days = np.where(mask, car_days, days)
            source = np.where(mask, "carrier-only", source)

    return pd.to_numeric(days, errors="coerce"), pd.Series(source, index=df_in.index)

def slack_group_stats_days(slack_days: pd.Series) -> pd.Series:
    s = pd.to_numeric(slack_days, errors='coerce').dropna()
    if len(s) == 0:
        return pd.Series({
            "Shipments": 0,
            "Over Count": 0,
            "% Over": np.nan,
            "Avg Slack (days)": np.nan,   # signed mean
            "Avg Over (days)": np.nan,    # only s>0
            "Max Over (days)": np.nan
        })
    over = s[s > 0]
    return pd.Series({
        "Shipments": len(s),
        "Over Count": int((s > 0).sum()),
        "% Over": round(100.0 * (s > 0).mean(), 2),
        "Avg Slack (days)": float(s.mean()),
        "Avg Over (days)": float(over.mean()) if len(over) else 0.0,
        "Max Over (days)": float(over.max()) if len(over) else 0.0
    })

def overtime_day_hist(slack_days: pd.Series) -> pd.DataFrame:
    s = pd.to_numeric(slack_days, errors="coerce").dropna()
    over = s[s > 0]
    if over.empty:
        return pd.DataFrame({"Over Days": [], "Shipments": []})
    buckets = np.ceil(over).astype(int)  # 0.2->1, 1.0->1, 1.1->2
    vc = buckets.value_counts().sort_index()
    return pd.DataFrame({"Over Days": vc.index.astype(int), "Shipments": vc.values})

def bucket_values_for_series(slack_days: pd.Series) -> list:
    s = pd.to_numeric(slack_days, errors="coerce").dropna()
    over = s[s > 0]
    if over.empty:
        return []
    return sorted(np.ceil(over).astype(int).unique().tolist())

# =============================================================
# App layout
# =============================================================

st.set_page_config(page_title="D&D Time Analyzer", layout="wide")
st.title("📦 Demurrage & Detention Time Analyzer")

support_msg = []
support_msg.append("✅ .xlsx (openpyxl)" if HAS_OPENPYXL else "❌ .xlsx (install `openpyxl`)")
support_msg.append("✅ .xls (xlrd==1.2.0)" if HAS_XLRD12 else "❌ .xls (install `xlrd==1.2.0`)")
st.info("File support in this environment: " + " | ".join(support_msg))

st.markdown(
    """
This app focuses **only on Demurrage & Detention time**, *not* on charges.

**Base durations (displayed in days):**
- **Demurrage at POD** = `Discharge` → `Gate Out`
- **Demurrage at POL** = `Gate In` → `Container Loaded`
- **Detention at POD** = `Gate Out` → `Empty Return`

All three are computed in hours and displayed in **days**.

**Slack (displayed in days, signed, + = overtime)**  
We treat free days as the *allowed* time and compare actual time:

- **Slack vs LFD (Dem POD)** = `Dem POD (hours)` − `POD Free Days × 24`
- **Slack vs OFD (Dem POL)** = `Dem POL (hours)` − `POL Free Days × 24`
- **Detention Slack (POD)** = `Det POD (hours)` − `Det Free Days × 24`

> **Slack > 0** = Over Free Days (overtime)  
> **Slack ≤ 0** = Within Free Days (may be negative)
"""
)

# -------------------------------------------------------------
# File Upload
# -------------------------------------------------------------
allowed_types = ["csv"]
if HAS_OPENPYXL:
    allowed_types.append("xlsx")
if HAS_XLRD12:
    allowed_types.append("xls")

uploaded = st.file_uploader("Upload your shipment file", type=allowed_types)
if not uploaded:
    st.stop()

name = uploaded.name.lower()

if name.endswith(".csv"):
    df_raw = load_csv(uploaded)
    sheet_name = None
else:
    is_xlsx = name.endswith(".xlsx")
    if is_xlsx and not HAS_OPENPYXL:
        st.error("This environment can't read `.xlsx` yet. Install **openpyxl** or upload CSV.")
        st.stop()
    if (not is_xlsx) and (not HAS_XLRD12):
        st.error("This environment can't read legacy `.xls`. Install **xlrd==1.2.0** or upload CSV.")
        st.stop()

    excel_bytes = load_excel_bytes(uploaded)
    xls, engine = excel_file(excel_bytes, is_xlsx=is_xlsx)
    sheet_name = st.selectbox("Choose sheet", xls.sheet_names, index=0)
    df_raw = read_sheet(excel_bytes, sheet_name, engine=engine)

df_raw = normalize_columns(df_raw)

# -------------------------------------------------------------
# Column Mapping (includes Shipment ID)
# -------------------------------------------------------------
default_cols = {
    "shipment_id": find_col(
        df_raw,
        [
            "Container Number",
            "Container No",
            "Container_No",
            "Container",
            "Cntr No",
            "Shipment ID",
            "BOL",
            "Bill of Lading",
        ],
    ),
    "carrier": find_col(df_raw, ["Carrier Name", "carrier", "Carrier"]),
    "gate_in": find_col(df_raw, ["2-Gate In Timestamp", "Gate In Timestamp", "2 - Gate In Timestamp"]),
    "container_loaded": find_col(
        df_raw, ["3-Container Loaded Timestamp", "Container Loaded Timestamp", "3 - Container Loaded Timestamp"]
    ),
    "discharge": find_col(
        df_raw, ["6-Container Discharge Timestamp", "Container Discharge Timestamp", "6 - Container Discharge Timestamp"]
    ),
    "gate_out": find_col(df_raw, ["7-Gate Out Timestamp", "Gate Out Timestamp", "7 - Gate Out Timestamp"]),
    "empty_return": find_col(
        df_raw, ["8-Empty Return Timestamp", "Empty Return Timestamp", "8 - Empty Return Timestamp"]
    ),
    "pol": find_col(df_raw, ["POL Port", "POL", "Port of Loading", "POL Name", "Origin Port"]),
    "pod": find_col(df_raw, ["POD Port", "POD", "Port of Discharge", "POD Name", "Destination Port"]),
}

with st.expander("Column Mapping", expanded=True):
    c1, c2 = st.columns(2)
    with c1:
        shipment_id_col = st.selectbox(
            "Shipment ID (e.g., BOL or Container)",
            df_raw.columns,
            index=default_index(default_cols["shipment_id"], df_raw.columns),
            help="Used to identify each shipment in the outputs. Default is Container Number if found.",
        )
        carrier_col = st.selectbox(
            "Carrier column",
            df_raw.columns,
            index=default_index(default_cols["carrier"], df_raw.columns),
        )
        pol_col = st.selectbox(
            "POL (Port of Loading) column",
            df_raw.columns,
            index=default_index(default_cols["pol"], df_raw.columns),
        )
        gate_in_col = st.selectbox(
            "2-Gate In Timestamp",
            df_raw.columns,
            index=default_index(default_cols["gate_in"], df_raw.columns),
        )
        container_loaded_col = st.selectbox(
            "3-Container Loaded Timestamp",
            df_raw.columns,
            index=default_index(default_cols["container_loaded"], df_raw.columns),
        )
    with c2:
        pod_col = st.selectbox(
            "POD (Port of Discharge) column",
            df_raw.columns,
            index=default_index(default_cols["pod"], df_raw.columns),
        )
        discharge_col = st.selectbox(
            "6-Container Discharge Timestamp",
            df_raw.columns,
            index=default_index(default_cols["discharge"], df_raw.columns),
        )
        gate_out_col = st.selectbox(
            "7-Gate Out Timestamp",
            df_raw.columns,
            index=default_index(default_cols["gate_out"], df_raw.columns),
        )
        empty_return_col = st.selectbox(
            "8-Empty Return Timestamp",
            df_raw.columns,
            index=default_index(default_cols["empty_return"], df_raw.columns),
        )

# --- Deduplicate by chosen Shipment ID ---
before_rows = len(df_raw)
df_raw = df_raw.drop_duplicates(subset=[shipment_id_col]).reset_index(drop=True)
after_rows = len(df_raw)
if after_rows < before_rows:
    st.info(
        f"Deduplicated by Shipment ID '{shipment_id_col}': "
        f"removed {before_rows - after_rows} duplicate rows."
    )

# -------------------------------------------------------------
# Settings
# -------------------------------------------------------------
st.divider()
st.subheader("Settings")

dayfirst = st.checkbox(
    "Dates are day-first (DD/MM/YYYY)",
    value=True,
    help="Tick if your timestamps are in DD/MM/YYYY format.",
)

# display: durations in days
duration_unit_factor = 1.0 / 24.0
duration_unit_label = "days"

# display: slack in days (requested)
slack_unit_factor = 1.0 / 24.0
slack_unit_label = "days"

neg_policy = st.selectbox(
    "Milestone durations: negatives",
    ["Treat as NaN (drop from stats)", "Keep (could be data issue)"],
    index=0,
    help="If a gap is negative (end < start), it's usually a data/timezone issue.",
)

# -------------------------------------------------------------
# Compute milestone durations (base hours)
# -------------------------------------------------------------
gate_in_dt = to_datetime(df_raw[gate_in_col], dayfirst=dayfirst)
container_loaded_dt = to_datetime(df_raw[container_loaded_col], dayfirst=dayfirst)
discharge_dt = to_datetime(df_raw[discharge_col], dayfirst=dayfirst)
gate_out_dt = to_datetime(df_raw[gate_out_col], dayfirst=dayfirst)
empty_return_dt = to_datetime(df_raw[empty_return_col], dayfirst=dayfirst)

pol_gap = compute_duration_hours(gate_in_dt, container_loaded_dt)      # Dem POL base (hours)
pod_dg_gap = compute_duration_hours(discharge_dt, gate_out_dt)         # Dem POD base (hours)
pod_ge_gap = compute_duration_hours(gate_out_dt, empty_return_dt)      # Det POD base (hours)

if neg_policy.startswith("Treat as NaN"):
    pol_gap = pol_gap.where(pol_gap >= 0)
    pod_dg_gap = pod_dg_gap.where(pod_dg_gap >= 0)
    pod_ge_gap = pod_ge_gap.where(pod_ge_gap >= 0)

df = df_raw.copy()
df["_ShipmentID"] = df[shipment_id_col].astype(str).str.strip()
df["_Carrier"] = df[carrier_col].astype(str).str.strip()
df["_POL Port"] = df[pol_col].astype(str).str.strip() if pol_col else "UNKNOWN_POL"
df["_POD Port"] = df[pod_col].astype(str).str.strip() if pod_col else "UNKNOWN_POD"

df["_Dem_POL_hours"] = pol_gap
df["_Dem_POD_hours"] = pod_dg_gap
df["_Det_POD_hours"] = pod_ge_gap

# -------------------------------------------------------------
# Free-Time Settings (Demurrage + Detention)
# -------------------------------------------------------------
def add_days_eod_vector(start_series, days_series, business_days_flag):
    return [
        add_days_eod(s, d, business_days_flag)
        for s, d in zip(start_series, days_series)
    ]

st.divider()
st.subheader("Free Time Settings (in Days)")

# --- POD Demurrage (LFD) ---
st.markdown("### Destination Demurrage (POD) – Free Days (LFD)")
c1, c2, c3 = st.columns([1, 1, 2])
with c1:
    default_free_days_pod = st.number_input(
        "Default POD Free Days (LFD)",
        0,
        60,
        5,
        1,
        help="If no mapping is provided, we assume this many free days at POD.",
    )
with c2:
    business_days_pod = st.checkbox(
        "POD free days use Business Days?",
        value=False,
        help="If checked, we use Mon–Fri calendar before setting LFD at end of day.",
    )
with c3:
    fd_map_pod_file = st.file_uploader(
        "Optional POD Free Days mapping CSV",
        type=["csv"],
        key="podmap",
        help=(
            "Columns (any order):\n"
            "- POD Port (matches POD names)\n"
            "- Carrier Name (optional)\n"
            "- Free Days (integer)\n"
        ),
    )

pod_mapper = None
if fd_map_pod_file is not None:
    try:
        df_pod_map = pd.read_csv(fd_map_pod_file)
        pod_mapper = build_free_days_mapper(df_pod_map, side="POD")
        st.success("Loaded POD Free Days mapping.")
    except Exception as e:
        st.warning(f"Could not read POD mapping CSV: {e}")

df["_FreeDays_POD"], df["_FD_POD_source"] = apply_free_days(
    df, "_Carrier", "_POD Port", default_free_days_pod, pod_mapper, "POD"
)
df["_Estimated_LFD"] = add_days_eod_vector(discharge_dt, df["_FreeDays_POD"], business_days_pod)

df["_Slack_LFD_hours"] = df["_Dem_POD_hours"] - df["_FreeDays_POD"] * 24.0
df["_Slack_LFD_days"] = df["_Slack_LFD_hours"] * slack_unit_factor

pod_cov = df["_FD_POD_source"].value_counts(dropna=False).to_dict()
st.caption(f"POD Free Days source breakdown: {pod_cov}")

# --- POL Demurrage (OFD) ---
st.markdown("### Origin Demurrage (POL) – Free Days (OFD)")
c1, c2, c3 = st.columns([1, 1, 2])
with c1:
    default_free_days_pol = st.number_input(
        "Default POL Free Days (OFD)",
        0,
        60,
        3,
        1,
        help="If no mapping is provided, we assume this many free days at POL.",
    )
with c2:
    business_days_pol = st.checkbox(
        "POL free days use Business Days?",
        value=False,
        help="If checked, we use Mon–Fri calendar before setting OFD at end of day.",
    )
with c3:
    fd_map_pol_file = st.file_uploader(
        "Optional POL Free Days mapping CSV",
        type=["csv"],
        key="polmap",
        help=(
            "Columns (any order):\n"
            "- POL Port (matches POL names)\n"
            "- Carrier Name (optional)\n"
            "- Free Days (integer)\n"
        ),
    )

pol_mapper = None
if fd_map_pol_file is not None:
    try:
        df_pol_map = pd.read_csv(fd_map_pol_file)
        pol_mapper = build_free_days_mapper(df_pol_map, side="POL")
        st.success("Loaded POL Free Days mapping.")
    except Exception as e:
        st.warning(f"Could not read POL mapping CSV: {e}")

df["_FreeDays_POL"], df["_FD_POL_source"] = apply_free_days(
    df, "_Carrier", "_POL Port", default_free_days_pol, pol_mapper, "POL"
)
df["_Estimated_OFD"] = add_days_eod_vector(gate_in_dt, df["_FreeDays_POL"], business_days_pol)

df["_Slack_OFD_hours"] = df["_Dem_POL_hours"] - df["_FreeDays_POL"] * 24.0
df["_Slack_OFD_days"] = df["_Slack_OFD_hours"] * slack_unit_factor

pol_cov = df["_FD_POL_source"].value_counts(dropna=False).to_dict()
st.caption(f"POL Free Days source breakdown: {pol_cov}")

# --- Detention free days at POD ---
st.markdown("### Detention at POD – Free Days")

c1, c2 = st.columns([1, 2])
with c1:
    default_free_days_det = st.number_input(
        "Default Detention Free Days at POD",
        0,
        60,
        0,
        1,
        help="Default detention free days after Gate Out at POD.",
    )
with c2:
    det_map_pod_file = st.file_uploader(
        "Optional Detention Free Days mapping CSV (POD Port, Carrier Name, Free Days)",
        type=["csv"],
        key="detmap",
        help=(
            "Columns (any order):\n"
            "- POD Port (matches POD names)\n"
            "- Carrier Name (optional)\n"
            "- Free Days (integer)\n"
        ),
    )

det_mapper = None
if det_map_pod_file is not None:
    try:
        df_det_map = pd.read_csv(det_map_pod_file)
        det_mapper = build_free_days_mapper(df_det_map, side="POD")
        st.success("Loaded Detention Free Days mapping for POD.")
    except Exception as e:
        st.warning(f"Could not read Detention mapping CSV: {e}")

df["_Det_FreeDays_POD"], df["_FD_DET_source"] = apply_free_days(
    df, "_Carrier", "_POD Port", default_free_days_det, det_mapper, "POD"
)

df["_Det_Slack_hours"] = df["_Det_POD_hours"] - df["_Det_FreeDays_POD"] * 24.0
df["_Det_Slack_days"] = df["_Det_Slack_hours"] * slack_unit_factor

det_cov = df["_FD_DET_source"].value_counts(dropna=False).to_dict()
st.caption(f"Detention Free Days at POD source breakdown: {det_cov}")

# -------------------------------------------------------------
# Global Filters (top-volume first, default All)
# -------------------------------------------------------------
st.divider()
st.subheader("Filters")

carrier_list = df["_Carrier"].dropna().value_counts().index.tolist()
carriers = st.multiselect("Carriers (top volume first)", carrier_list, default=None)
if carriers:
    df = df[df["_Carrier"].isin(carriers)]

pol_list = df["_POL Port"].dropna().value_counts().index.tolist()
pols = st.multiselect("POL Ports (top volume first)", pol_list, default=None)
if pols:
    df = df[df["_POL Port"].isin(pols)]

pod_list = df["_POD Port"].dropna().value_counts().index.tolist()
pods = st.multiselect("POD Ports (top volume first)", pod_list, default=None)
if pods:
    df = df[df["_POD Port"].isin(pods)]

idx = df.index

# Base durations (hours)
dem_pol_hours = df.loc[idx, "_Dem_POL_hours"]
dem_pod_hours = df.loc[idx, "_Dem_POD_hours"]
det_pod_hours = df.loc[idx, "_Det_POD_hours"]

# Slack (days)
slack_lfd_days = df.loc[idx, "_Slack_LFD_days"]
slack_ofd_days = df.loc[idx, "_Slack_OFD_days"]
slack_det_days = df.loc[idx, "_Det_Slack_days"]

shipment_ids = df.loc[idx, "_ShipmentID"]
carrier_vals = df.loc[idx, "_Carrier"]
pol_vals = df.loc[idx, "_POL Port"]
pod_vals = df.loc[idx, "_POD Port"]

dem_pod_status = np.where(slack_lfd_days > 0, "Over Free Days", "Within Free Days")
dem_pol_status = np.where(slack_ofd_days > 0, "Over Free Days", "Within Free Days")
det_pod_status = np.where(slack_det_days > 0, "Over Free Days", "Within Free Days")

# -------------------------------------------------------------
# Tabs
# -------------------------------------------------------------
tab_charts, tab_port_carrier, tab_lane, tab_ship = st.tabs(
    ["Charts", "By Port & Carrier", "By Lane (POL → POD)", "Shipment Explorer"]
)

# ============================
# TAB 1: Charts
# ============================
with tab_charts:
    st.subheader("High-level D&D Time View")

    total_dem_pod = dem_pod_hours.dropna().sum()
    total_dem_pol = dem_pol_hours.dropna().sum()
    total_det_pod = det_pod_hours.dropna().sum()

    def slack_caption(avg_val):
        if pd.isna(avg_val):
            return ""
        if avg_val < 0:
            return "🟦 Negative avg slack = within free days (on average)."
        if avg_val > 0:
            return "🟥 Positive avg slack = overtime (on average)."
        return "⬜ Avg slack = 0."

    c1, c2, c3 = st.columns(3)

    with c1:
        st.metric(
            f"Total Demurrage at POD ({duration_unit_label})",
            f"{total_dem_pod * duration_unit_factor:,.2f}",
        )
        avg_slack = slack_lfd_days.mean()
        st.metric(
            f"Avg Slack vs LFD at POD ({slack_unit_label})",
            f"{(avg_slack if pd.notna(avg_slack) else 0):,.2f}",
            help="Signed slack in days: negative = within free days; positive = overtime."
        )
        st.caption(slack_caption(avg_slack))

    with c2:
        st.metric(
            f"Total Demurrage at POL ({duration_unit_label})",
            f"{total_dem_pol * duration_unit_factor:,.2f}",
        )
        avg_slack = slack_ofd_days.mean()
        st.metric(
            f"Avg Slack vs OFD at POL ({slack_unit_label})",
            f"{(avg_slack if pd.notna(avg_slack) else 0):,.2f}",
            help="Signed slack in days: negative = within free days; positive = overtime."
        )
        st.caption(slack_caption(avg_slack))

    with c3:
        st.metric(
            f"Total Detention at POD ({duration_unit_label})",
            f"{total_det_pod * duration_unit_factor:,.2f}",
        )
        avg_slack = slack_det_days.mean()
        st.metric(
            f"Avg Detention Slack at POD ({slack_unit_label})",
            f"{(avg_slack if pd.notna(avg_slack) else 0):,.2f}",
            help="Signed slack in days: negative = within free days; positive = overtime."
        )
        st.caption(slack_caption(avg_slack))

    st.markdown(f"#### Overall Slack Distributions ({slack_unit_label}, + = overtime)")
    colA, colB, colC = st.columns(3)
    with colA:
        st.write("**Slack vs LFD (Dem POD)**")
        st.write(slack_lfd_days.describe())
    with colB:
        st.write("**Slack vs OFD (Dem POL)**")
        st.write(slack_ofd_days.describe())
    with colC:
        st.write("**Detention Slack at POD**")
        st.write(slack_det_days.describe())

    # Overtime distribution charts
    st.divider()
    st.subheader("Overtime Distribution (Over Free Days Only)")
    st.caption("X-axis is whole **days over** (bucketed using ceil). Y-axis is **# shipments**.")

    h1, h2, h3 = st.columns(3)
    with h1:
        st.write("**Dem POD overtime (days)**")
        hist = overtime_day_hist(slack_lfd_days)
        if hist.empty:
            st.info("No overtime shipments for Dem POD.")
        else:
            st.bar_chart(hist.set_index("Over Days"))
    with h2:
        st.write("**Dem POL overtime (days)**")
        hist = overtime_day_hist(slack_ofd_days)
        if hist.empty:
            st.info("No overtime shipments for Dem POL.")
        else:
            st.bar_chart(hist.set_index("Over Days"))
    with h3:
        st.write("**Detention overtime (days)**")
        hist = overtime_day_hist(slack_det_days)
        if hist.empty:
            st.info("No overtime shipments for Detention.")
        else:
            st.bar_chart(hist.set_index("Over Days"))

    # Drilldown picker + counts + downloads
    st.divider()
    st.subheader("Overtime Drilldown (pick a metric + days-over bucket)")

    metric_options = {
        "Demurrage POD (Slack vs LFD)": ("_Slack_LFD_days", "POD"),
        "Demurrage POL (Slack vs OFD)": ("_Slack_OFD_days", "POL"),
        "Detention POD (Detention Slack)": ("_Det_Slack_days", "POD"),
    }
    metric_label = st.selectbox("Metric", list(metric_options.keys()), index=0)
    slack_col, side = metric_options[metric_label]

    available_buckets = bucket_values_for_series(df[slack_col])

    if not available_buckets:
        st.info("No overtime shipments for the selected metric.")
    else:
        selected_bucket = st.selectbox("Show shipments over by (days)", available_buckets, index=0)
        lo, hi = selected_bucket - 1, selected_bucket

        overtime_all = df[pd.to_numeric(df[slack_col], errors="coerce") > 0].copy()

        drill = overtime_all[
            (pd.to_numeric(overtime_all[slack_col], errors="coerce") > lo)
            & (pd.to_numeric(overtime_all[slack_col], errors="coerce") <= hi)
        ].copy()

        # Optional drill filters
        st.caption("Optional drill filters (defaults to All):")
        fc1, fc2 = st.columns(2)

        with fc1:
            carrier_order = drill["_Carrier"].value_counts().index.tolist()
            carrier_pick = st.multiselect("Carrier (top volume first)", carrier_order, default=None)

        with fc2:
            if side == "POL":
                port_order = drill["_POL Port"].value_counts().index.tolist()
                port_pick = st.multiselect("POL Port (top volume first)", port_order, default=None)
            else:
                port_order = drill["_POD Port"].value_counts().index.tolist()
                port_pick = st.multiselect("POD Port (top volume first)", port_order, default=None)

        if carrier_pick:
            drill = drill[drill["_Carrier"].isin(carrier_pick)]
        if port_pick:
            if side == "POL":
                drill = drill[drill["_POL Port"].isin(port_pick)]
            else:
                drill = drill[drill["_POD Port"].isin(port_pick)]

        total_overtime_shipments = overtime_all["_ShipmentID"].nunique()
        bucket_shipments = drill["_ShipmentID"].nunique()
        pct = (bucket_shipments / total_overtime_shipments * 100.0) if total_overtime_shipments else 0.0

        m1, m2, m3 = st.columns(3)
        with m1:
            st.metric("Shipments in selected bucket", f"{bucket_shipments:,}")
        with m2:
            st.metric("Total overtime shipments (metric)", f"{total_overtime_shipments:,}")
        with m3:
            st.metric("% of overtime shipments", f"{pct:,.2f}%")

        drill["Over Free Days"] = pd.to_numeric(drill[slack_col], errors="coerce").round(4)
        drill["Over Free Days Bucket"] = np.ceil(pd.to_numeric(drill[slack_col], errors="coerce")).astype("Int64")

        drill_view = pd.DataFrame({
            "Shipment ID": drill["_ShipmentID"],
            "Carrier": drill["_Carrier"],
            "POL Port": drill["_POL Port"],
            "POD Port": drill["_POD Port"],
            "Over Free Days": drill["Over Free Days"],
            "Over Free Days Bucket": drill["Over Free Days Bucket"],
            f"Demurrage at POL ({duration_unit_label})": pd.to_numeric(drill["_Dem_POL_hours"], errors="coerce") * duration_unit_factor,
            f"Demurrage at POD ({duration_unit_label})": pd.to_numeric(drill["_Dem_POD_hours"], errors="coerce") * duration_unit_factor,
            f"Detention at POD ({duration_unit_label})": pd.to_numeric(drill["_Det_POD_hours"], errors="coerce") * duration_unit_factor,
        }).sort_values("Over Free Days", ascending=False)

        st.dataframe(drill_view, use_container_width=True)

        st.download_button(
            "Download selected bucket shipments (CSV)",
            drill_view.to_csv(index=False).encode("utf-8"),
            file_name="dd_overtime_selected_bucket_shipments.csv",
            mime="text/csv",
        )

        st.divider()
        st.subheader("Download: All shipments above free days (selected metric)")

        overtime_all["Over Free Days"] = pd.to_numeric(overtime_all[slack_col], errors="coerce").round(4)
        overtime_all["Over Free Days Bucket"] = np.ceil(pd.to_numeric(overtime_all[slack_col], errors="coerce")).astype("Int64")

        overtime_all_view = pd.DataFrame({
            "Shipment ID": overtime_all["_ShipmentID"],
            "Carrier": overtime_all["_Carrier"],
            "POL Port": overtime_all["_POL Port"],
            "POD Port": overtime_all["_POD Port"],
            "Over Free Days": overtime_all["Over Free Days"],
            "Over Free Days Bucket": overtime_all["Over Free Days Bucket"],
            f"Demurrage at POL ({duration_unit_label})": pd.to_numeric(overtime_all["_Dem_POL_hours"], errors="coerce") * duration_unit_factor,
            f"Demurrage at POD ({duration_unit_label})": pd.to_numeric(overtime_all["_Dem_POD_hours"], errors="coerce") * duration_unit_factor,
            f"Detention at POD ({duration_unit_label})": pd.to_numeric(overtime_all["_Det_POD_hours"], errors="coerce") * duration_unit_factor,
        }).sort_values("Over Free Days", ascending=False)

        st.caption(f"Total overtime shipments in this metric: {overtime_all_view['Shipment ID'].nunique():,}")
        st.dataframe(overtime_all_view.head(200), use_container_width=True)

        st.download_button(
            "Download ALL overtime shipments (CSV)",
            overtime_all_view.to_csv(index=False).encode("utf-8"),
            file_name="dd_overtime_all_shipments_selected_metric.csv",
            mime="text/csv",
        )

# ============================
# TAB 2: By Port & Carrier
# ============================
with tab_port_carrier:
    st.subheader("By Port & Carrier")

    st.markdown("### Demurrage at POD (Discharge → Gate Out)")
    dem_pod_df = pd.DataFrame(
        {
            "POD Port": pod_vals,
            "Carrier": carrier_vals,
            "Dem_POD_hours": dem_pod_hours,
            "Slack_LFD_days": slack_lfd_days,
        }
    )

    dem_pod_summary = (
        dem_pod_df.groupby(["POD Port", "Carrier"])
        .agg(
            Total_Dem_Days=("Dem_POD_hours", lambda s: s.sum() * duration_unit_factor),
            Avg_Dem_Days=("Dem_POD_hours", lambda s: s.mean() * duration_unit_factor),
            Median_Dem_Days=("Dem_POD_hours", lambda s: s.median() * duration_unit_factor),
        )
        .reset_index()
    )

    slack_pod_summary = (
        dem_pod_df.groupby(["POD Port", "Carrier"])["Slack_LFD_days"]
        .apply(slack_group_stats_days)
        .unstack()
        .reset_index()
    )

    dem_pod_merged = dem_pod_summary.merge(slack_pod_summary, on=["POD Port", "Carrier"], how="left")

    st.dataframe(dem_pod_merged, use_container_width=True)
    st.download_button(
        "Download Demurrage POD by POD+Carrier (CSV)",
        dem_pod_merged.to_csv(index=False).encode("utf-8"),
        file_name="demurrage_pod_by_pod_carrier.csv",
        mime="text/csv",
    )

    st.divider()

    st.markdown("### Demurrage at POL (Gate In → Container Loaded)")
    dem_pol_df = pd.DataFrame(
        {
            "POL Port": pol_vals,
            "Carrier": carrier_vals,
            "Dem_POL_hours": dem_pol_hours,
            "Slack_OFD_days": slack_ofd_days,
        }
    )

    dem_pol_summary = (
        dem_pol_df.groupby(["POL Port", "Carrier"])
        .agg(
            Total_Dem_Days=("Dem_POL_hours", lambda s: s.sum() * duration_unit_factor),
            Avg_Dem_Days=("Dem_POL_hours", lambda s: s.mean() * duration_unit_factor),
            Median_Dem_Days=("Dem_POL_hours", lambda s: s.median() * duration_unit_factor),
        )
        .reset_index()
    )

    slack_pol_summary = (
        dem_pol_df.groupby(["POL Port", "Carrier"])["Slack_OFD_days"]
        .apply(slack_group_stats_days)
        .unstack()
        .reset_index()
    )

    dem_pol_merged = dem_pol_summary.merge(slack_pol_summary, on=["POL Port", "Carrier"], how="left")

    st.dataframe(dem_pol_merged, use_container_width=True)
    st.download_button(
        "Download Demurrage POL by POL+Carrier (CSV)",
        dem_pol_merged.to_csv(index=False).encode("utf-8"),
        file_name="demurrage_pol_by_pol_carrier.csv",
        mime="text/csv",
    )

    st.divider()

    st.markdown("### Detention at POD (Gate Out → Empty Return)")
    det_df = pd.DataFrame(
        {
            "POD Port": pod_vals,
            "Carrier": carrier_vals,
            "Det_POD_hours": det_pod_hours,
            "Det_Slack_days": slack_det_days,
        }
    )

    det_summary = (
        det_df.groupby(["POD Port", "Carrier"])
        .agg(
            Total_Det_Days=("Det_POD_hours", lambda s: s.sum() * duration_unit_factor),
            Avg_Det_Days=("Det_POD_hours", lambda s: s.mean() * duration_unit_factor),
            Median_Det_Days=("Det_POD_hours", lambda s: s.median() * duration_unit_factor),
            Max_Det_Days=("Det_POD_hours", lambda s: s.max() * duration_unit_factor),
        )
        .reset_index()
    )

    det_slack_summary = (
        det_df.groupby(["POD Port", "Carrier"])["Det_Slack_days"]
        .apply(slack_group_stats_days)
        .unstack()
        .reset_index()
    )

    det_merged = det_summary.merge(det_slack_summary, on=["POD Port", "Carrier"], how="left")

    st.dataframe(det_merged, use_container_width=True)
    st.download_button(
        "Download Detention POD by POD+Carrier (CSV)",
        det_merged.to_csv(index=False).encode("utf-8"),
        file_name="detention_pod_by_pod_carrier.csv",
        mime="text/csv",
    )

# ============================
# TAB 3: By Lane (POL → POD)
# ============================
with tab_lane:
    st.subheader("By Lane (POL → POD)")

    lane_df = pd.DataFrame(
        {
            "POL Port": pol_vals,
            "POD Port": pod_vals,
            "Dem_POL_hours": dem_pol_hours,
            "Dem_POD_hours": dem_pod_hours,
            "Det_POD_hours": det_pod_hours,
            "Slack_LFD_days": slack_lfd_days,
            "Slack_OFD_days": slack_ofd_days,
            "Det_Slack_days": slack_det_days,
        }
    )

    lane_group = lane_df.groupby(["POL Port", "POD Port"])

    lane_summary = lane_group.agg(
        Shipments=("Dem_POD_hours", "count"),
        Avg_Dem_POL_Days=("Dem_POL_hours", lambda s: s.mean() * duration_unit_factor),
        Avg_Dem_POD_Days=("Dem_POD_hours", lambda s: s.mean() * duration_unit_factor),
        Avg_Det_POD_Days=("Det_POD_hours", lambda s: s.mean() * duration_unit_factor),

        Avg_Slack_LFD_Days=("Slack_LFD_days", "mean"),
        Avg_Slack_OFD_Days=("Slack_OFD_days", "mean"),
        Avg_Slack_Det_Days=("Det_Slack_days", "mean"),

        Pct_Over_LFD=("Slack_LFD_days", lambda s: round(100.0 * (pd.to_numeric(s, errors="coerce") > 0).mean(), 2)),
        Pct_Over_OFD=("Slack_OFD_days", lambda s: round(100.0 * (pd.to_numeric(s, errors="coerce") > 0).mean(), 2)),
        Pct_Over_Det=("Det_Slack_days", lambda s: round(100.0 * (pd.to_numeric(s, errors="coerce") > 0).mean(), 2)),
    ).reset_index()

    st.dataframe(lane_summary, use_container_width=True)
    st.download_button(
        "Download Lane Summary (CSV)",
        lane_summary.to_csv(index=False).encode("utf-8"),
        file_name="dd_time_by_lane.csv",
        mime="text/csv",
    )

# ============================
# TAB 4: Shipment Explorer
# ============================
with tab_ship:
    st.subheader("Shipment Explorer")

    sort_options = {
        f"Demurrage at POD ({duration_unit_label})": "Dem_POD_disp",
        f"Demurrage at POL ({duration_unit_label})": "Dem_POL_disp",
        f"Detention at POD ({duration_unit_label})": "Det_POD_disp",
        f"Slack vs LFD ({slack_unit_label}, + = over)": "Slack_LFD_days",
        f"Slack vs OFD ({slack_unit_label}, + = over)": "Slack_OFD_days",
        f"Detention Slack at POD ({slack_unit_label}, + = over)": "Det_Slack_days",
    }
    sort_key = st.selectbox("Sort by", list(sort_options.keys()), index=0)
    descending = st.checkbox("Sort descending?", value=True)

    explorer_df = pd.DataFrame(
        {
            "Shipment ID": shipment_ids,
            "Carrier": carrier_vals,
            "POL Port": pol_vals,
            "POD Port": pod_vals,
            "Gate In (POL)": gate_in_dt.loc[idx],
            "Container Loaded (POL)": container_loaded_dt.loc[idx],
            "Discharge (POD)": discharge_dt.loc[idx],
            "Gate Out (POD)": gate_out_dt.loc[idx],
            "Empty Return (POD)": empty_return_dt.loc[idx],
            f"Demurrage at POL ({duration_unit_label})": dem_pol_hours * duration_unit_factor,
            f"Demurrage at POD ({duration_unit_label})": dem_pod_hours * duration_unit_factor,
            f"Detention at POD ({duration_unit_label})": det_pod_hours * duration_unit_factor,
            f"Slack vs OFD ({slack_unit_label}, + = over)": slack_ofd_days,
            f"Slack vs LFD ({slack_unit_label}, + = over)": slack_lfd_days,
            f"Detention Slack at POD ({slack_unit_label}, + = over)": slack_det_days,
            "Demurrage Status at POL": dem_pol_status,
            "Demurrage Status at POD": dem_pod_status,
            "Detention Status at POD": det_pod_status,
        }
    )

    explorer_df["Dem_POL_disp"] = dem_pol_hours * duration_unit_factor
    explorer_df["Dem_POD_disp"] = dem_pod_hours * duration_unit_factor
    explorer_df["Det_POD_disp"] = det_pod_hours * duration_unit_factor
    explorer_df["Slack_LFD_days"] = slack_lfd_days
    explorer_df["Slack_OFD_days"] = slack_ofd_days
    explorer_df["Det_Slack_days"] = slack_det_days

    sort_col_internal = sort_options[sort_key]
    explorer_df = explorer_df.sort_values(sort_col_internal, ascending=not descending)

    show_cols = [
        "Shipment ID",
        "Carrier",
        "POL Port",
        "POD Port",
        "Gate In (POL)",
        "Container Loaded (POL)",
        "Discharge (POD)",
        "Gate Out (POD)",
        "Empty Return (POD)",
        f"Demurrage at POL ({duration_unit_label})",
        f"Demurrage at POD ({duration_unit_label})",
        f"Detention at POD ({duration_unit_label})",
        f"Slack vs OFD ({slack_unit_label}, + = over)",
        f"Slack vs LFD ({slack_unit_label}, + = over)",
        f"Detention Slack at POD ({slack_unit_label}, + = over)",
        "Demurrage Status at POL",
        "Demurrage Status at POD",
        "Detention Status at POD",
    ]

    st.dataframe(explorer_df[show_cols], use_container_width=True)

    st.download_button(
        "Download Shipment-level D&D Time (CSV)",
        explorer_df[show_cols].to_csv(index=False).encode("utf-8"),
        file_name="shipment_dd_time_slack_days.csv",
        mime="text/csv",
    )
