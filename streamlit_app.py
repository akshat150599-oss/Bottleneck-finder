# streamlit_app.py
import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO

st.set_page_config(page_title="Ocean Bottleneck Analyzer", layout="wide")
st.title("📦 Ocean Bottleneck Analyzer")
st.caption("Identify bottlenecks between key port milestones at Carrier → Port level")

# --- Optional dependency checks (so we don't crash if engines aren't installed)
def has_openpyxl() -> bool:
    try:
        import openpyxl  # noqa: F401
        return True
    except Exception:
        return False

def has_xlrd_12() -> bool:
    try:
        import xlrd  # noqa: F401
        # xlrd >= 2.0 removed .xls support; we want 1.2.0 specifically
        import pkg_resources
        ver = pkg_resources.get_distribution("xlrd").version
        return ver.startswith("1.2")
    except Exception:
        return False

HAS_OPENPYXL = has_openpyxl()
HAS_XLRD12 = has_xlrd_12()

# Let users know what's supported in THIS environment
support_msg = []
if HAS_OPENPYXL:
    support_msg.append("✅ .xlsx (openpyxl)")
else:
    support_msg.append("❌ .xlsx (install `openpyxl`)")

if HAS_XLRD12:
    support_msg.append("✅ .xls (xlrd==1.2.0)")
else:
    support_msg.append("❌ .xls (install `xlrd==1.2.0` if needed)")

st.info("File support in this environment: " + " | ".join(support_msg))

# Allowed types reflect what can actually be read right now
allowed_types = ["csv"]
if HAS_OPENPYXL:
    allowed_types.append("xlsx")
if HAS_XLRD12:
    allowed_types.append("xls")

st.markdown("""
**What it computes**
- **POL**: Gate In → Container Loaded  
- **POD**: Discharge → Gate Out, Gate Out → Empty Return  
For each **Carrier → Port**: **count, average (avg/mean), median, mode**.  
Mode is taken on durations rounded to 1 decimal for stability.
""")

# -----------------------
# File upload
# -----------------------
uploaded = st.file_uploader("Upload your file", type=allowed_types)
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
    except ImportError as e:
        if is_xlsx:
            st.error("`.xlsx` reading needs **openpyxl**. Add to requirements.txt or run: `pip install openpyxl`")
        else:
            st.error("`.xls` reading needs **xlrd==1.2.0**. Add to requirements.txt or run: `pip install xlrd==1.2.0`")
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

def to_datetime(series: pd.Series, dayfirst: bool = True) -> pd.Series:
    # Robust for "DD/MM/YY HH:MM:SS AM/PM"
    return pd.to_datetime(series, errors="coerce", dayfirst=dayfirst)

def compute_duration_hours(start: pd.Series, end: pd.Series) -> pd.Series:
    return (end - start).dt.total_seconds() / 3600.0

def summarize(series: pd.Series, mode_round: int = 1) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) == 0:
        return pd.Series({
            "count": 0, "avg_hours": np.nan, "mean_hours": np.nan,
            "median_hours": np.nan, "mode_hours": np.nan
        })
    avg_val = s.mean()
    median_val = s.median()
    mode_vals = s.round(mode_round).mode()
    mode_val = mode_vals.iloc[0] if len(mode_vals) else np.nan
    return pd.Series({
        "count": len(s),
        "avg_hours": avg_val,
        "mean_hours": avg_val,
        "median_hours": median_val,
        "mode_hours": mode_val
    })

def build_summary(df: pd.DataFrame, group_cols, value_col: str, label: str, mode_round: int = 1) -> pd.DataFrame:
    g = (
        df.groupby(group_cols)[value_col]
          .apply(lambda s: summarize(s, mode_round=mode_round))
          .reset_index()
          .rename(columns={value_col: "value", "level_2": "measure"})
          .pivot_table(index=group_cols, columns="measure", values="value", aggfunc="first")
          .reset_index()
          .assign(Metric=label)
    )
    for need in ["count", "avg_hours", "mean_hours", "median_hours", "mode_hours"]:
        if need not in g.columns:
            g[need] = np.nan
    return g

# -----------------------
# Read file into DataFrame
# -----------------------
if name.endswith(".csv"):
    df_raw = load_csv(uploaded)
    sheet_name = None
else:
    is_xlsx = name.endswith(".xlsx")
    if is_xlsx and not HAS_OPENPYXL:
        st.error("This environment can't read `.xlsx` yet. Install **openpyxl** or upload a CSV.")
        st.stop()
    if (not is_xlsx) and (not HAS_XLRD12):
        st.error("This environment can't read legacy `.xls`. Install **xlrd==1.2.0** or upload a CSV.")
        st.stop()

    excel_bytes = load_excel_bytes(uploaded)
    xls, engine = excel_file(excel_bytes, is_xlsx=is_xlsx)
    sheet_name = st.selectbox("Choose sheet", xls.sheet_names, index=0)
    df_raw = read_sheet(excel_bytes, sheet_name, engine=engine)

df_raw = normalize_columns(df_raw)

# -----------------------
# Column mapping
# -----------------------
default_cols = {
    "carrier": find_col(df_raw, ["Carrier Name", "carrier", "Carrier"]),
    "gate_in": find_col(df_raw, ["2-Gate In Timestamp", "Gate In Timestamp", "2 - Gate In Timestamp"]),
    "container_loaded": find_col(df_raw, ["3-Container Loaded Timestamp", "Container Loaded Timestamp", "3 - Container Loaded Timestamp"]),
    "discharge": find_col(df_raw, ["6-Container Discharge Timestamp", "Container Discharge Timestamp", "6 - Container Discharge Timestamp"]),
    "gate_out": find_col(df_raw, ["7-Gate Out Timestamp", "Gate Out Timestamp", "7 - Gate Out Timestamp"]),
    "empty_return": find_col(df_raw, ["8-Empty Return Timestamp", "Empty Return Timestamp", "8 - Empty Return Timestamp"]),
    "pol": find_col(df_raw, ["POL", "Port of Loading", "POL Name", "POL Port", "POL UNLOCODE", "Origin Port", "Origin UNLOCODE"]),
    "pod": find_col(df_raw, ["POD", "Port of Discharge", "POD Name", "POD Port", "POD UNLOCODE", "Destination Port", "Destination UNLOCODE"]),
}

with st.expander("Column Mapping", expanded=True):
    c1, c2 = st.columns(2)
    with c1:
        carrier_col = st.selectbox("Carrier column", options=df_raw.columns,
                                   index=df_raw.columns.get_loc(default_cols["carrier"]) if default_cols["carrier"] in df_raw.columns else 0)
        pol_col = st.selectbox("POL (Port of Loading) column", options=df_raw.columns,
                               index=df_raw.columns.get_loc(default_cols["pol"]) if default_cols["pol"] in df_raw.columns else 0)
        gate_in_col = st.selectbox("2-Gate In Timestamp", options=df_raw.columns,
                                   index=df_raw.columns.get_loc(default_cols["gate_in"]) if default_cols["gate_in"] in df_raw.columns else 0)
        container_loaded_col = st.selectbox("3-Container Loaded Timestamp", options=df_raw.columns,
                                            index=df_raw.columns.get_loc(default_cols["container_loaded"]) if default_cols["container_loaded"] in df_raw.columns else 0)
    with c2:
        pod_col = st.selectbox("POD (Port of Discharge) column", options=df_raw.columns,
                               index=df_raw.columns.get_loc(default_cols["pod"]) if default_cols["pod"] in df_raw.columns else 0)
        discharge_col = st.selectbox("6-Container Discharge Timestamp", options=df_raw.columns,
                                     index=df_raw.columns.get_loc(default_cols["discharge"]) if default_cols["discharge"] in df_raw.columns else 0)
        gate_out_col = st.selectbox("7-Gate Out Timestamp", options=df_raw.columns,
                                    index=df_raw.columns.get_loc(default_cols["gate_out"]) if default_cols["gate_out"] in df_raw.columns else 0)
        empty_return_col = st.selectbox("8-Empty Return Timestamp", options=df_raw.columns,
                                        index=df_raw.columns.get_loc(default_cols["empty_return"]) if default_cols["empty_return"] in df_raw.columns else 0)

# -----------------------
# Settings
# -----------------------
st.divider()
st.subheader("Settings")
dayfirst = st.checkbox("Dates are day-first (DD/MM/YYYY)", value=True)
unit = st.selectbox("Units", ["hours", "minutes"], index=0)
mode_round = st.slider("Mode rounding (units)", 0, 3, 1)
negative_policy = st.selectbox("How to handle negative durations",
                               ["Keep (could indicate data error)", "Treat as NaN (drop from stats)"], index=1)

def convert_units(series: pd.Series) -> pd.Series:
    return series * 60.0 if unit == "minutes" else series

# -----------------------
# Compute durations
# -----------------------
gate_in_dt = to_datetime(df_raw[gate_in_col], dayfirst=dayfirst)
container_loaded_dt = to_datetime(df_raw[container_loaded_col], dayfirst=dayfirst)
discharge_dt = to_datetime(df_raw[discharge_col], dayfirst=dayfirst)
gate_out_dt = to_datetime(df_raw[gate_out_col], dayfirst=dayfirst)
empty_return_dt = to_datetime(df_raw[empty_return_col], dayfirst=dayfirst)

pol_gap = compute_duration_hours(gate_in_dt, container_loaded_dt)
pod_dg_gap = compute_duration_hours(discharge_dt, gate_out_dt)
pod_ge_gap = compute_duration_hours(gate_out_dt, empty_return_dt)

if negative_policy == "Treat as NaN (drop from stats)":
    pol_gap = pol_gap.where(pol_gap >= 0)
    pod_dg_gap = pod_dg_gap.where(pod_dg_gap >= 0)
    pod_ge_gap = pod_ge_gap.where(pod_ge_gap >= 0)

df = df_raw.copy()
df["_Carrier"] = df[carrier_col].astype(str)
df["_POL Port"] = df[pol_col].astype(str) if pol_col else "UNKNOWN_POL"
df["_POD Port"] = df[pod_col].astype(str) if pod_col else "UNKNOWN_POD"
df["_POL_duration"] = convert_units(pol_gap)
df["_POD_dg_duration"] = convert_units(pod_dg_gap)
df["_POD_ge_duration"] = convert_units(pod_ge_gap)

# -----------------------
# Filters
# -----------------------
st.subheader("Filters")
carriers = st.multiselect("Carriers", sorted(df["_Carrier"].dropna().astype(str).unique().tolist()), default=None)
if carriers:
    df = df[df["_Carrier"].isin(carriers)]

# -----------------------
# Summaries
# -----------------------
pol_summary = build_summary(df, ["_Carrier", "_POL Port"], "_POL_duration", "GateIn→ContainerLoaded (POL)", mode_round=mode_round)
pod_dg_summary = build_summary(df, ["_Carrier", "_POD Port"], "_POD_dg_duration", "Discharge→GateOut (POD)", mode_round=mode_round)
pod_ge_summary = build_summary(df, ["_Carrier", "_POD Port"], "_POD_ge_duration", "GateOut→EmptyReturn (POD)", mode_round=mode_round)

pol_summary = pol_summary.rename(columns={"_Carrier": "Carrier", "_POL Port": "POL Port"})
pod_dg_summary = pod_dg_summary.rename(columns={"_Carrier": "Carrier", "_POD Port": "POD Port"})
pod_ge_summary = pod_ge_summary.rename(columns={"_Carrier": "Carrier", "_POD Port": "POD Port"})
pol_summary["POD Port"] = np.nan
pod_dg_summary["POL Port"] = np.nan
pod_ge_summary["POL Port"] = np.nan

results = pd.concat([
    pol_summary[["Carrier", "Metric", "POL Port", "POD Port", "count", "avg_hours", "mean_hours", "median_hours", "mode_hours"]],
    pod_dg_summary[["Carrier", "Metric", "POL Port", "POD Port", "count", "avg_hours", "mean_hours", "median_hours", "mode_hours"]],
    pod_ge_summary[["Carrier", "Metric", "POL Port", "POD Port", "count", "avg_hours", "mean_hours", "median_hours", "mode_hours"]],
], ignore_index=True)

unit_suffix = " (mins)" if unit == "minutes" else " (hrs)"
pretty = results.rename(columns={
    "avg_hours": "Average" + unit_suffix,
    "mean_hours": "Mean" + unit_suffix,
    "median_hours": "Median" + unit_suffix,
    "mode_hours": "Mode" + unit_suffix,
})
for c in ["Average" + unit_suffix, "Mean" + unit_suffix, "Median" + unit_suffix, "Mode" + unit_suffix]:
    pretty[c] = pd.to_numeric(pretty[c], errors="coerce").round(2)

st.subheader("Results")
st.dataframe(pretty, use_container_width=True)

st.download_button(
    "Download results as CSV",
    data=pretty.to_csv(index=False).encode("utf-8"),
    file_name="carrier_port_bottleneck_summary.csv",
    mime="text/csv"
)
