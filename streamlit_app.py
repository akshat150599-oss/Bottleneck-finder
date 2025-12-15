# --- Imports + compatibility for st.divider ---
try:
    import streamlit as st
except Exception as e:
    raise RuntimeError("Streamlit is required. Try: pip install streamlit") from e

# Backfill st.divider() for older Streamlit versions
if not hasattr(st, "divider"):
    def _divider():
        st.markdown("---")
    st.divider = _divider

import pandas as pd
import numpy as np
from io import BytesIO
from pandas.tseries.offsets import BDay

# =============================================================
# Core App: Demurrage & Detention Analyzer
# =============================================================

st.set_page_config(page_title="D&D Time Analyzer", layout="wide")
st.title("⏱ Demurrage & Detention Time Analyzer")
st.caption("Focus on how long containers spend under Demurrage & Detention and whether they were within free time or over.")

# -----------------------
# Optional dependency checks (Excel engines)
# -----------------------

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
### What this app focuses on

This app **only** looks at Demurrage & Detention time for *completed* shipments.

- **Demurrage at POD (Destination)**  
  Discharge → Gate Out, compared with **POD Free Days (LFD)**

- **Demurrage at POL (Origin)**  
  Gate In → Container Loaded, compared with **POL Free Days (OFD)**

- **Detention at POD (Destination Equipment)**  
  Gate Out → Empty Return, compared with a **Detention Free Time at POD** you choose.

For each side, **Slack** is defined as:

- Slack **> 0** → shipment went **over** free time (D&D risk)  
- Slack ≤ 0 → shipment was **within** free time
"""
)

# -----------------------
# File upload
# -----------------------
uploaded = st.file_uploader("Upload your shipment file (CSV / Excel)", type=allowed_types)
if not uploaded:
    st.stop()
name = uploaded.name.lower()

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
            st.error("`.xlsx` needs **openpyxl**.")
        else:
            st.error("`.xls` needs **xlrd==1.2.0**.")
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
        st.error("This environment can't read legacy `.xls`. Install **xlrd==1.2.0` or upload a CSV."); st.stop()
    excel_bytes = load_excel_bytes(uploaded)
    xls, engine = excel_file(excel_bytes, is_xlsx=is_xlsx)
    sheet_name = st.selectbox("Choose sheet", xls.sheet_names, index=0)
    df_raw = read_sheet(excel_bytes, sheet_name, engine=engine)

df_raw = normalize_columns(df_raw)

# -----------------------
# Column mapping helpers
# -----------------------
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

# Default guesses
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

# -----------------------
# Column mapping UI
# -----------------------
with st.expander("Step 1 – Map columns", expanded=True):
    c1, c2 = st.columns(2)
    with c1:
        carrier_col = st.selectbox(
            "Carrier column",
            df_raw.columns,
            index=default_index(default_cols['carrier'], df_raw.columns),
            help="Name of the column containing carrier name."
        )
        pol_col = st.selectbox(
            "POL (Port of Loading) column",
            df_raw.columns,
            index=default_index(default_cols['pol'], df_raw.columns),
        )
        gate_in_col = st.selectbox(
            "Gate In at POL",
            df_raw.columns,
            index=default_index(default_cols['gate_in'], df_raw.columns),
            help="Timestamp when container entered origin terminal."
        )
        container_loaded_col = st.selectbox(
            "Container Loaded at POL",
            df_raw.columns,
            index=default_index(default_cols['container_loaded'], df_raw.columns),
            help="Timestamp when container was loaded on vessel at origin."
        )
    with c2:
        pod_col = st.selectbox(
            "POD (Port of Discharge) column",
            df_raw.columns,
            index=default_index(default_cols['pod'], df_raw.columns),
        )
        discharge_col = st.selectbox(
            "Discharge at POD",
            df_raw.columns,
            index=default_index(default_cols['discharge'], df_raw.columns),
            help="Timestamp when container was discharged from vessel at destination."
        )
        gate_out_col = st.selectbox(
            "Gate Out at POD",
            df_raw.columns,
            index=default_index(default_cols['gate_out'], df_raw.columns),
            help="Timestamp when container left destination terminal."
        )
        empty_return_col = st.selectbox(
            "Empty Return at POD",
            df_raw.columns,
            index=default_index(default_cols['empty_return'], df_raw.columns),
            help="Timestamp when empty container was returned."
        )

# -----------------------
# Basic settings
# -----------------------
st.divider(); st.subheader("Step 2 – Parsing & data quality settings")
dayfirst = st.checkbox(
    "Dates are day-first (DD/MM/YYYY)",
    value=True,
    help="If checked, timestamps are parsed as DD/MM/YY HH:MM:SS."
)
neg_policy = st.selectbox(
    "Negative durations (end < start)",
    ["Treat as NaN (drop from stats)", "Keep (could be data issue)"],
    index=0,
    help="Usually negative gaps mean bad timestamps. Recommended: Treat as NaN."
)
neg_tol = st.slider(
    "Slack tolerance: treat very small overages as within (hours)",
    0.0, 6.0, 2.0, 0.5,
    help="If slack > 0 but ≤ this many hours, we treat it as 0 (within free time)."
)

def to_datetime(series: pd.Series, dayfirst: bool = True) -> pd.Series:
    return pd.to_datetime(series, errors='coerce', dayfirst=dayfirst)

def compute_duration_hours(start: pd.Series, end: pd.Series) -> pd.Series:
    return (end - start).dt.total_seconds() / 3600.0

# -----------------------
# Compute raw durations (HOURS)
# -----------------------
gate_in_dt = to_datetime(df_raw[gate_in_col], dayfirst=dayfirst)
container_loaded_dt = to_datetime(df_raw[container_loaded_col], dayfirst=dayfirst)
discharge_dt = to_datetime(df_raw[discharge_col], dayfirst=dayfirst)
gate_out_dt = to_datetime(df_raw[gate_out_col], dayfirst=dayfirst)
empty_return_dt = to_datetime(df_raw[empty_return_col], dayfirst=dayfirst)

# POL demurrage-like: Gate In → Container Loaded
pol_gap = compute_duration_hours(gate_in_dt, container_loaded_dt)
# POD demurrage-like: Discharge → Gate Out
pod_dg_gap = compute_duration_hours(discharge_dt, gate_out_dt)
# POD detention-like: Gate Out → Empty Return
pod_ge_gap = compute_duration_hours(gate_out_dt, empty_return_dt)

if neg_policy == "Treat as NaN (drop from stats)":
    pol_gap = pol_gap.where(pol_gap >= 0)
    pod_dg_gap = pod_dg_gap.where(pod_dg_gap >= 0)
    pod_ge_gap = pod_ge_gap.where(pod_ge_gap >= 0)

# Working frame
df = df_raw.copy()
df["_Carrier"]   = df[carrier_col].astype(str).str.strip()
df["_POL Port"]  = df[pol_col].astype(str).str.strip() if pol_col else "UNKNOWN_POL"
df["_POD Port"]  = df[pod_col].astype(str).str.strip() if pod_col else "UNKNOWN_POD"

# -----------------------
# Free days mapping + slack (POL & POD demurrage)
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

def clip_small_over_to_zero(series: pd.Series, tol_hours: float) -> pd.Series:
    """If slack > 0 but ≤ tol_hours, treat as 0 (within free time)."""
    if tol_hours is None or tol_hours <= 0:
        return series
    return series.mask((series > 0) & (series <= tol_hours), 0)

# --- POD (Destination) Free Days / LFD ---
st.divider(); st.subheader("Step 3 – Destination Demurrage (POD LFD)")

c1, c2, c3 = st.columns([1,1,2])
with c1:
    default_free_days_pod = st.number_input(
        "Default POD Free Days (Demurrage at Destination)",
        0, 60, 5, 1,
        help="If no mapping is provided, we assume this many free days at POD."
    )
with c2:
    business_days_pod = st.checkbox(
        "POD counts Business Days (skip Sat/Sun)?",
        value=False,
        help="If checked, LFD adds business days only (Mon–Fri) before setting the cutoff at end-of-day."
    )
with c3:
    fd_map_pod_file = st.file_uploader(
        "Optional POD Free-Days mapping CSV (POD Port, Carrier Name, Free Days)",
        type=["csv"], key="podmap",
        help="Override POD Free Days by carrier+port / port-only / carrier-only."
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

# Slack: positive = over free time, negative/zero = within
df["_LFD_Slack_hours"] = (gate_out_dt - df["_Estimated_LFD"]).dt.total_seconds() / 3600.0
df["_LFD_Slack_hours"] = clip_small_over_to_zero(df["_LFD_Slack_hours"], neg_tol)

pod_cov = df["_FD_POD_source"].value_counts(dropna=False).to_dict()
st.caption(f"POD Free Days source breakdown: {pod_cov}")

# --- POL (Origin) Free Days / OFD ---
st.divider(); st.subheader("Step 4 – Origin Demurrage (POL OFD)")

c1, c2, c3 = st.columns([1,1,2])
with c1:
    default_free_days_pol = st.number_input(
        "Default POL Free Days (Demurrage at Origin)",
        0, 60, 3, 1,
        help="If no mapping is provided, we assume this many free days at POL."
    )
with c2:
    business_days_pol = st.checkbox(
        "POL counts Business Days (skip Sat/Sun)?",
        value=False,
        help="If checked, OFD adds business days only (Mon–Fri) before setting the cutoff at end-of-day."
    )
with c3:
    fd_map_pol_file = st.file_uploader(
        "Optional POL Free-Days mapping CSV (POL Port, Carrier Name, Free Days)",
        type=["csv"], key="polmap",
        help="Override POL Free Days by carrier+port / port-only / carrier-only."
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

# Slack: positive = over free time, negative/zero = within
df["_OFD_Slack_hours"] = (container_loaded_dt - df["_Estimated_OFD"]).dt.total_seconds() / 3600.0
df["_OFD_Slack_hours"] = clip_small_over_to_zero(df["_OFD_Slack_hours"], neg_tol)

pol_cov = df["_FD_POL_source"].value_counts(dropna=False).to_dict()
st.caption(f"POL Free Days source breakdown: {pol_cov}")

# -----------------------
# Filters
# -----------------------
st.divider(); st.subheader("Step 5 – Filters")
carriers = st.multiselect(
    "Filter by Carrier (optional)",
    sorted(df["_Carrier"].dropna().astype(str).unique().tolist()),
    default=None,
)
if carriers:
    df = df[df["_Carrier"].isin(carriers)]

# Align series to filtered df
idx = df.index

LFD_slack = df["_LFD_Slack_hours"]
OFD_slack = df["_OFD_Slack_hours"]
dem_pod_hours_series = pod_dg_gap.loc[idx]
dem_pol_hours_series = pol_gap.loc[idx]
det_hours_series     = pod_ge_gap.loc[idx]

discharge_f         = discharge_dt.loc[idx]
gate_out_f          = gate_out_dt.loc[idx]
empty_return_f      = empty_return_dt.loc[idx]
gate_in_f           = gate_in_dt.loc[idx]
container_loaded_f  = container_loaded_dt.loc[idx]

# ============================================================
# STEP 6 – D&D TIME + SLACK (Completed Shipments)
# ============================================================

st.divider()
st.header("Step 6 – D&D Time & Slack for Completed Shipments")

# Choose unit for D&D analysis (hours or days)
dd_unit = st.radio(
    "Display unit for D&D metrics",
    ["hours", "days"],
    index=0,
    help="Choose whether all D&D metrics below are shown in hours or days. 1 day = 24 hours."
)
dd_factor = 1.0 if dd_unit == "hours" else 1.0 / 24.0
dd_label = "hrs" if dd_unit == "hours" else "days"

with st.expander("Quick glossary: what each metric means", expanded=False):
    st.markdown(
        f"""
- **Demurrage at POD (Destination)** – Time the container stays **inside the destination port**
  after discharge until gate-out.  
  → Measured as **Discharge → Gate Out**, compared to **POD Free Days (LFD)**.  
  → **Slack vs LFD** (in hours): **positive = over free days**, negative/zero = within.

- **Demurrage at POL (Origin)** – Time the container stays **inside the origin port**
  from gate-in until it is loaded on the vessel.  
  → Measured as **Gate In → Container Loaded**, compared to **POL Free Days (OFD)**.  
  → **Slack vs OFD** (in hours): **positive = over free days**, negative/zero = within.

- **Detention at POD (Destination equipment)** – Time the container stays **with you outside the port**
  after gate-out until empty return.  
  → Measured as **Gate Out → Empty Return**, compared to a **Detention Free Time at POD**
  you choose below (in {dd_unit}).  
  → **Detention Slack at POD** (in hours): **positive = over free time**, negative/zero = within.

- **Free Days / Free Time** – Time during which you do **not** pay D&D. We treat **positive slack**
  as potential D&D risk and **negative or zero slack** as within free time.
        """
    )

# --- Detention free-time input (in chosen unit) ---
st.subheader("Detention Free Time at POD")

if dd_unit == "hours":
    det_free_input = st.number_input(
        "Detention Free Time at POD (in hours)",
        min_value=0.0, max_value=24.0*60, value=0.0, step=1.0,
        help="How many hours after Gate Out you can keep the container before detention is 'over free time'."
    )
    det_free_hours = det_free_input
else:
    det_free_input = st.number_input(
        "Detention Free Time at POD (in days)",
        min_value=0.0, max_value=60.0, value=0.0, step=0.5,
        help="How many days after Gate Out you can keep the container before detention is 'over free time'."
    )
    det_free_hours = det_free_input * 24.0

# Detention slack at POD: positive = over free time, negative/zero = within
det_slack_hours = det_hours_series - det_free_hours
det_slack_hours = clip_small_over_to_zero(det_slack_hours, neg_tol)

def disp(x_hours: float) -> float:
    return x_hours * dd_factor

# --- Over/within stats ---
pod_dem_over_mask = LFD_slack > 0
pol_dem_over_mask = OFD_slack > 0
det_over_mask     = det_slack_hours > 0

total_ship = len(df)

# POD demurrage
pod_count_over = int(pod_dem_over_mask.sum())
pod_pct_over = 100.0 * pod_count_over / total_ship if total_ship else 0.0
pod_over_vals = LFD_slack.where(LFD_slack > 0).dropna()
pod_avg_over_hours = pod_over_vals.mean() if len(pod_over_vals) else 0.0

# POL demurrage
pol_count_over = int(pol_dem_over_mask.sum())
pol_pct_over = 100.0 * pol_count_over / total_ship if total_ship else 0.0
pol_over_vals = OFD_slack.where(OFD_slack > 0).dropna()
pol_avg_over_hours = pol_over_vals.mean() if len(pol_over_vals) else 0.0

# Detention
det_count_over = int(det_over_mask.sum())
det_pct_over = 100.0 * det_count_over / total_ship if total_ship else 0.0
det_over_vals = det_slack_hours.where(det_slack_hours > 0).dropna()
det_avg_over_hours = det_over_vals.mean() if len(det_over_vals) else 0.0

# Totals
total_dem_pod_hours = dem_pod_hours_series.sum()
avg_dem_pod_hours   = dem_pod_hours_series.mean()
total_dem_pol_hours = dem_pol_hours_series.sum()
avg_dem_pol_hours   = dem_pol_hours_series.mean()
total_det_hours     = det_hours_series.sum()
avg_det_hours       = det_hours_series.mean()

# =========================
# OVERVIEW KPIs
# =========================
st.subheader(f"Overview – Within vs Over Free Time ({dd_label})")

row1 = st.columns(4)
with row1[0]:
    st.metric(
        "Total Completed Shipments",
        f"{total_ship:,}",
    )
with row1[1]:
    st.metric("Shipments Over Free Time – Dem POD", f"{pod_count_over:,}")
with row1[2]:
    st.metric("Shipments Over Free Time – Dem POL", f"{pol_count_over:,}")
with row1[3]:
    st.metric("Shipments Over Free Time – Det POD", f"{det_count_over:,}")

row2 = st.columns(4)
with row2[0]:
    st.metric("% Over Free Time – Dem POD", f"{pod_pct_over:.1f}%")
with row2[1]:
    st.metric("% Over Free Time – Dem POL", f"{pol_pct_over:.1f}%")
with row2[2]:
    st.metric("% Over Free Time – Det POD", f"{det_pct_over:.1f}%")
with row2[3]:
    comb = pod_avg_over_hours + pol_avg_over_hours + det_avg_over_hours
    st.metric(
        f"Avg Slack Over Free Time (over cases only) ({dd_label})",
        f"{disp(comb):.2f} {dd_label}",
    )

st.caption(
    "Demurrage free time at POL/POD comes from your mapping (in days). "
    "Detention free time at POD uses the value and unit you set above. "
    "Slack > 0 means over free time; slack ≤ 0 means within."
)

colA, colB, colC = st.columns(3)
with colA:
    st.metric(
        f"Total Demurrage at POD ({dd_label}) – Discharge→Gate Out",
        f"{disp(total_dem_pod_hours):,.2f}",
    )
    st.metric(
        f"Avg Demurrage per Shipment at POD ({dd_label})",
        f"{disp(avg_dem_pod_hours):.2f}" if not np.isnan(avg_dem_pod_hours) else "–",
    )
with colB:
    st.metric(
        f"Total Demurrage at POL ({dd_label}) – Gate In→Loaded",
        f"{disp(total_dem_pol_hours):,.2f}",
    )
    st.metric(
        f"Avg Demurrage per Shipment at POL ({dd_label})",
        f"{disp(avg_dem_pol_hours):.2f}" if not np.isnan(avg_dem_pol_hours) else "–",
    )
with colC:
    st.metric(
        f"Total Detention at POD ({dd_label}) – Gate Out→Empty Return",
        f"{disp(total_det_hours):,.2f}",
    )
    st.metric(
        f"Avg Detention per Shipment at POD ({dd_label})",
        f"{disp(avg_det_hours):.2f}" if not np.isnan(avg_det_hours) else "–",
    )

st.divider()

# =========================
# Detailed Views: Tabs
# =========================
tab_overview, tab_port_carrier, tab_lane, tab_shipments = st.tabs(
    ["Charts", "By Port & Carrier", "By Lane (POL → POD)", "Shipment Explorer"]
)

# 1) CHARTS TAB
with tab_overview:
    st.subheader(f"D&D Time Distribution ({dd_label})")

    # Demurrage at POD by POD Port
    dem_pod_by_pod = (
        pd.DataFrame({"_POD Port": df["_POD Port"], "DemHoursPOD": dem_pod_hours_series})
        .groupby("_POD Port")["DemHoursPOD"]
        .sum()
        .sort_values(ascending=False)
        .head(10)
        .reset_index()
    )
    dem_pod_by_pod["DemTime"] = dem_pod_by_pod["DemHoursPOD"] * dd_factor

    if not dem_pod_by_pod.empty:
        st.markdown(f"**Top 10 POD Ports by Demurrage Time ({dd_label})**")
        st.bar_chart(dem_pod_by_pod.set_index("_POD Port")["DemTime"])
    else:
        st.info("No demurrage-at-POD data to plot by POD Port.")

    st.markdown("---")

    # Detention at POD by POD Port
    det_by_pod = (
        pd.DataFrame({"_POD Port": df["_POD Port"], "DetHoursPOD": det_hours_series})
        .groupby("_POD Port")["DetHoursPOD"]
        .sum()
        .sort_values(ascending=False)
        .head(10)
        .reset_index()
    )
    det_by_pod["DetTime"] = det_by_pod["DetHoursPOD"] * dd_factor

    if not det_by_pod.empty:
        st.markdown(f"**Top 10 POD Ports by Detention Time ({dd_label})**")
        st.bar_chart(det_by_pod.set_index("_POD Port")["DetTime"])
    else:
        st.info("No detention-at-POD data to plot by POD Port.")

# 2) BY PORT & CARRIER TAB
with tab_port_carrier:
    st.subheader(f"Demurrage at POD – By POD Port & Carrier ({dd_label})")
    st.caption("Destination port side: Discharge → Gate Out, using POD Free Days (LFD).")

    col_avg_dem_pod = f"Avg Demurrage ({dd_label})"
    col_med_dem_pod = f"Median Demurrage ({dd_label})"
    col_avg_over_pod = f"Avg Positive Slack (Dem POD, {dd_label})"

    def agg_demurrage_pod_group(g: pd.DataFrame) -> pd.Series:
        shipments = len(g)
        local_slack = g["_LFD_Slack_hours"]
        over = int((local_slack > 0).sum())
        pct_over = 100.0 * over / shipments if shipments else np.nan
        over_vals = local_slack.where(local_slack > 0).dropna()
        avg_hours_over = over_vals.mean() if len(over_vals) else 0.0
        dem_vals = dem_pod_hours_series.loc[g.index]  # hours
        avg_dem = dem_vals.mean()
        med_dem = dem_vals.median()
        return pd.Series({
            "Shipments": shipments,
            "Shipments Over Free Time": over,
            "% Over Free Time": pct_over,
            col_avg_dem_pod: disp(avg_dem),
            col_med_dem_pod: disp(med_dem),
            col_avg_over_pod: disp(avg_hours_over),
        })

    dem_pod_group = (
        df.groupby(["_POD Port", "_Carrier"])
          .apply(agg_demurrage_pod_group)
          .reset_index()
          .rename(columns={"_POD Port": "POD Port", "_Carrier": "Carrier"})
    )

    num_cols_pod = ["% Over Free Time", col_avg_dem_pod, col_med_dem_pod, col_avg_over_pod]
    for c in num_cols_pod:
        dem_pod_group[c] = pd.to_numeric(dem_pod_group[c], errors="coerce").round(2)

    if dem_pod_group.empty:
        st.info("No data to show for Demurrage at POD.")
    else:
        dem_pod_group = dem_pod_group.sort_values("% Over Free Time", ascending=False)
        st.dataframe(dem_pod_group, use_container_width=True)

    st.download_button(
        "Download: Demurrage at POD by POD Port & Carrier (CSV)",
        data=dem_pod_group.to_csv(index=False).encode("utf-8"),
        file_name="demurrage_pod_by_pod_carrier.csv",
        mime="text/csv",
    )

    st.markdown("---")
    st.subheader(f"Demurrage at POL – By POL Port & Carrier ({dd_label})")
    st.caption("Origin port side: Gate In → Container Loaded, using POL Free Days (OFD).")

    col_avg_dem_pol = f"Avg Demurrage ({dd_label})"
    col_med_dem_pol = f"Median Demurrage ({dd_label})"
    col_avg_over_pol = f"Avg Positive Slack (Dem POL, {dd_label})"

    def agg_demurrage_pol_group(g: pd.DataFrame) -> pd.Series:
        shipments = len(g)
        local_slack = g["_OFD_Slack_hours"]
        over = int((local_slack > 0).sum())
        pct_over = 100.0 * over / shipments if shipments else np.nan
        over_vals = local_slack.where(local_slack > 0).dropna()
        avg_hours_over = over_vals.mean() if len(over_vals) else 0.0
        dem_vals = dem_pol_hours_series.loc[g.index]  # hours
        avg_dem = dem_vals.mean()
        med_dem = dem_vals.median()
        return pd.Series({
            "Shipments": shipments,
            "Shipments Over Free Time": over,
            "% Over Free Time": pct_over,
            col_avg_dem_pol: disp(avg_dem),
            col_med_dem_pol: disp(med_dem),
            col_avg_over_pol: disp(avg_hours_over),
        })

    dem_pol_group = (
        df.groupby(["_POL Port", "_Carrier"])
          .apply(agg_demurrage_pol_group)
          .reset_index()
          .rename(columns={"_POL Port": "POL Port", "_Carrier": "Carrier"})
    )

    num_cols_pol = ["% Over Free Time", col_avg_dem_pol, col_med_dem_pol, col_avg_over_pol]
    for c in num_cols_pol:
        dem_pol_group[c] = pd.to_numeric(dem_pol_group[c], errors="coerce").round(2)

    if dem_pol_group.empty:
        st.info("No data to show for Demurrage at POL.")
    else:
        dem_pol_group = dem_pol_group.sort_values("% Over Free Time", ascending=False)
        st.dataframe(dem_pol_group, use_container_width=True)

    st.download_button(
        "Download: Demurrage at POL by POL Port & Carrier (CSV)",
        data=dem_pol_group.to_csv(index=False).encode("utf-8"),
        file_name="demurrage_pol_by_pol_carrier.csv",
        mime="text/csv",
    )

    st.markdown("---")
    st.subheader(f"Detention at POD – By POD Port & Carrier ({dd_label})")
    st.caption("Equipment time outside terminal: Gate Out → Empty Return, using detention free time you set above.")

    col_avg_det = f"Avg Detention ({dd_label})"
    col_med_det = f"Median Detention ({dd_label})"
    col_max_det = f"Max Detention ({dd_label})"
    col_avg_det_slack = f"Avg Positive Slack (Det POD, {dd_label})"

    def agg_detention_group(g: pd.DataFrame) -> pd.Series:
        shipments = len(g)
        det_vals = det_hours_series.loc[g.index]
        avg_det = det_vals.mean()
        med_det = det_vals.median()
        max_det = det_vals.max()
        local_slack = det_slack_hours.loc[g.index]
        over = int((local_slack > 0).sum())
        pct_over = 100.0 * over / shipments if shipments else np.nan
        over_vals = local_slack.where(local_slack > 0).dropna()
        avg_over = over_vals.mean() if len(over_vals) else 0.0
        return pd.Series({
            "Shipments": shipments,
            "Shipments Over Free Time": over,
            "% Over Free Time": pct_over,
            col_avg_det: disp(avg_det),
            col_med_det: disp(med_det),
            col_max_det: disp(max_det),
            col_avg_det_slack: disp(avg_over),
        })

    det_group = (
        df.groupby(["_POD Port", "_Carrier"])
          .apply(agg_detention_group)
          .reset_index()
          .rename(columns={"_POD Port": "POD Port", "_Carrier": "Carrier"})
    )

    for c in ["% Over Free Time", col_avg_det, col_med_det, col_max_det, col_avg_det_slack]:
        det_group[c] = pd.to_numeric(det_group[c], errors="coerce").round(2)

    if det_group.empty:
        st.info("No data to show for Detention at POD.")
    else:
        det_group = det_group.sort_values(col_avg_det, ascending=False)
        st.dataframe(det_group, use_container_width=True)

    st.download_button(
        "Download: Detention at POD by POD Port & Carrier (CSV)",
        data=det_group.to_csv(index=False).encode("utf-8"),
        file_name="detention_pod_by_pod_carrier.csv",
        mime="text/csv",
    )

# 3) BY LANE TAB
with tab_lane:
    st.subheader(f"D&D Time by Lane (POL → POD) ({dd_label})")

    def agg_lane_group(g: pd.DataFrame) -> pd.Series:
        shipments = len(g)

        # dem POD
        lfd_s = g["_LFD_Slack_hours"]
        over_pod = int((lfd_s > 0).sum())
        pct_over_pod = 100.0 * over_pod / shipments if shipments else np.nan
        pod_over_vals = lfd_s.where(lfd_s > 0).dropna()
        avg_hours_over_pod = pod_over_vals.mean() if len(pod_over_vals) else 0.0

        # dem POL
        ofd_s = g["_OFD_Slack_hours"]
        over_pol = int((ofd_s > 0).sum())
        pct_over_pol = 100.0 * over_pol / shipments if shipments else np.nan
        pol_over_vals = ofd_s.where(ofd_s > 0).dropna()
        avg_hours_over_pol = pol_over_vals.mean() if len(pol_over_vals) else 0.0

        # detention
        det_vals = det_hours_series.loc[g.index]
        avg_det = det_vals.mean()
        local_det_slack = det_slack_hours.loc[g.index]
        over_det = int((local_det_slack > 0).sum())
        pct_over_det = 100.0 * over_det / shipments if shipments else np.nan
        det_over_vals = local_det_slack.where(local_det_slack > 0).dropna()
        avg_hours_over_det = det_over_vals.mean() if len(det_over_vals) else 0.0

        dem_pod_vals = dem_pod_hours_series.loc[g.index]
        dem_pol_vals = dem_pol_hours_series.loc[g.index]

        return pd.Series({
            "Shipments": shipments,
            "% Over Free Time – Dem POD": pct_over_pod,
            "% Over Free Time – Dem POL": pct_over_pol,
            "% Over Free Time – Det POD": pct_over_det,
            f"Avg Demurrage POD ({dd_label})": disp(dem_pod_vals.mean()),
            f"Avg Demurrage POL ({dd_label})": disp(dem_pol_vals.mean()),
            f"Avg Detention POD ({dd_label})": disp(avg_det),
            f"Avg Positive Slack Dem POD ({dd_label})": disp(avg_hours_over_pod),
            f"Avg Positive Slack Dem POL ({dd_label})": disp(avg_hours_over_pol),
            f"Avg Positive Slack Det POD ({dd_label})": disp(avg_hours_over_det),
        })

    lane_group = (
        df.groupby(["_POL Port", "_POD Port"])
          .apply(agg_lane_group)
          .reset_index()
          .rename(columns={"_POL Port": "POL Port", "_POD Port": "POD Port"})
    )

    for c in [
        "% Over Free Time – Dem POD",
        "% Over Free Time – Dem POL",
        "% Over Free Time – Det POD",
        f"Avg Demurrage POD ({dd_label})",
        f"Avg Demurrage POL ({dd_label})",
        f"Avg Detention POD ({dd_label})",
        f"Avg Positive Slack Dem POD ({dd_label})",
        f"Avg Positive Slack Dem POL ({dd_label})",
        f"Avg Positive Slack Det POD ({dd_label})",
    ]:
        lane_group[c] = pd.to_numeric(lane_group[c], errors="coerce").round(2)

    if lane_group.empty:
        st.info("No data to show by lane.")
    else:
        lane_group = lane_group.sort_values("% Over Free Time – Dem POD", ascending=False)
        st.dataframe(lane_group, use_container_width=True)

    st.download_button(
        "Download: D&D Time by Lane (CSV)",
        data=lane_group.to_csv(index=False).encode("utf-8"),
        file_name="dd_time_by_lane.csv",
        mime="text/csv",
    )

# 4) SHIPMENT EXPLORER TAB
with tab_shipments:
    st.subheader(f"Shipment Explorer – Demurrage & Detention per Container ({dd_label})")

    only_over_any = st.checkbox(
        "Show only shipments over free time on **any** side",
        value=True,
        help="Keep rows where Dem POD, Dem POL, or Det POD has slack > 0 (over free time)."
    )

    dem_pod_status = np.where(LFD_slack > 0, "Over Free Time", "Within Free Time")
    dem_pol_status = np.where(OFD_slack > 0, "Over Free Time", "Within Free Time")
    det_status     = np.where(det_slack_hours > 0, "Over Free Time", "Within Free Time")

    diag_dd = pd.DataFrame({
        "Carrier": df["_Carrier"],
        "POL Port": df["_POL Port"],
        "POD Port": df["_POD Port"],
        "Gate In (POL)": gate_in_f,
        "Container Loaded (POL)": container_loaded_f,
        "Discharge (POD)": discharge_f,
        "Gate Out (POD)": gate_out_f,
        "Empty Return (POD)": empty_return_f,
        f"Demurrage {dd_label} at POL (Gate In→Loaded)": disp(dem_pol_hours_series),
        f"Demurrage {dd_label} at POD (Discharge→Gate Out)": disp(dem_pod_hours_series),
        f"Detention {dd_label} at POD (Gate Out→Empty Return)": disp(det_hours_series),
        "Free Days POL (Origin, days)": df["_FreeDays_POL"],
        "Free Days POD (Destination, days)": df["_FreeDays_POD"],
        "Slack vs OFD at POL (hrs, + = over)": OFD_slack,
        "Slack vs LFD at POD (hrs, + = over)": LFD_slack,
        "Detention Slack at POD (hrs, + = over)": det_slack_hours,
        "Demurrage Status at POL": dem_pol_status,
        "Demurrage Status at POD": dem_pod_status,
        "Detention Status at POD": det_status,
        f"Detention Slack at POD ({dd_label}, + = over)": disp(det_slack_hours),
    }, index=idx)

    if only_over_any:
        mask_over_any = (
            (diag_dd["Demurrage Status at POL"] == "Over Free Time") |
            (diag_dd["Demurrage Status at POD"] == "Over Free Time") |
            (diag_dd["Detention Status at POD"] == "Over Free Time")
        )
        diag_view = diag_dd.loc[mask_over_any].copy()
    else:
        diag_view = diag_dd.copy()

    st.caption(
        "Slack > 0 means the shipment went **over** free time; slack ≤ 0 means it was **within** free time."
    )

    st.dataframe(diag_view, use_container_width=True)
    st.download_button(
        "Download: Shipment-level D&D Time & Slack (CSV)",
        data=diag_view.to_csv(index=False).encode("utf-8"),
        file_name="shipment_dd_time_slack_completed.csv",
        mime="text/csv",
    )
