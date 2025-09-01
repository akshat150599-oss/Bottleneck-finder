# streamlit_app.py
import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
from pandas.tseries.offsets import BDay

st.set_page_config(page_title="Ocean Bottleneck Analyzer", layout="wide")
st.title("📦 Ocean Bottleneck Analyzer")
st.caption("Identify bottlenecks and LFD risk at Carrier → Port level")

# --- Optional dependency checks (to avoid crashing if engines aren't installed)
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

# Show what this environment can read
support_msg = []
support_msg.append("✅ .xlsx (openpyxl)" if HAS_OPENPYXL else "❌ .xlsx (install `openpyxl`)")
support_msg.append("✅ .xls (xlrd==1.2.0)" if HAS_XLRD12 else "❌ .xls (install `xlrd==1.2.0` if needed)")
st.info("File support in this environment: " + " | ".join(support_msg))

# Allowed upload types reflect available engines
allowed_types = ["csv"]
if HAS_OPENPYXL:
    allowed_types.append("xlsx")
if HAS_XLRD12:
    allowed_types.append("xls")

st.markdown("""
### What it computes
**Durations**
- **POL:** Gate In → Container Loaded  
- **POD:** Discharge → Gate Out, Gate Out → Empty Return  
For each **Carrier → Port**: **count, average (avg/mean), median, mode** (mode rounded to 1 decimal).

**Estimated LFD**
- **Estimated LFD** = Container Discharge (POD) + **Free Days** (calendar or business days).  
- **Slack vs LFD (hours)** = _Estimated LFD − Gate Out_  
- **Late** if Slack < 0; **On-time/Early** otherwise.

You can upload an **optional Free-Days mapping CSV** to override the default (by Carrier + POD, POD only, or Carrier only).
""")

# -----------------------
# Upload
# -----------------------
uploaded = st.file_uploader("Upload your movement/export file", type=allowed_types)
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
# Read file → DataFrame
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
# Estimated LFD (NEW)
# -----------------------
st.divider()
st.subheader("Estimated LFD (Last Free Day)")

c1, c2, c3 = st.columns([1,1,2])
with c1:
    default_free_days = st.number_input("Default Free Days (calendar/business)", min_value=0, max_value=60, value=5, step=1)
with c2:
    business_days = st.checkbox("Use Business Days (skip Sat/Sun)", value=False)
with c3:
    free_map_file = st.file_uploader(
        "Optional Free-Days mapping CSV (columns like: POD Port, Carrier Name, Free Days)",
        type=["csv"], accept_multiple_files=False
    )

# build a flexible mapper if a CSV is provided
def norm(s): 
    return str(s).strip().lower() if pd.notna(s) else ""

def get_mapper(df_map: pd.DataFrame):
    # Accept flexible column names
    cols = {c.lower(): c for c in df_map.columns}
    def pick(*opts):
        for o in opts:
            if o.lower() in cols: 
                return cols[o.lower()]
        return None

    col_pod = pick("POD Port", "POD", "Port of Discharge")
    col_car = pick("Carrier Name", "Carrier")
    col_days = pick("Free Days", "FreeDays", "free_days", "Days", "Demurrage Free Days")

    if not col_days:
        st.warning("Free-Days mapping CSV missing a 'Free Days' column. Ignoring mapping.")
        return None, None, None

    pod_car_days = {}
    pod_days = {}
    car_days = {}

    for _, r in df_map.iterrows():
        days = r.get(col_days, None)
        try:
            days = int(days)
        except Exception:
            continue
        car = norm(r.get(col_car, "")) if col_car else ""
        pod = norm(r.get(col_pod, "")) if col_pod else ""

        if car and pod:
            pod_car_days[(car, pod)] = days
        elif pod:
            pod_days[pod] = days
        elif car:
            car_days[car] = days

    return pod_car_days, pod_days, car_days

pod_car_days = pod_days = car_days = None
if free_map_file is not None:
    try:
        df_map = pd.read_csv(free_map_file)
        pod_car_days, pod_days, car_days = get_mapper(df_map)
        st.success("Loaded Free-Days mapping.")
    except Exception as e:
        st.warning(f"Could not read mapping CSV: {e}")

# assign free days
df["_car_key"] = df["_Carrier"].map(norm)
df["_pod_key"] = df["_POD Port"].map(norm)
df["_FreeDays"] = default_free_days

if pod_car_days or pod_days or car_days:
    # combo override
    if pod_car_days:
        combo = (df["_car_key"] + "||" + df["_pod_key"]).map(
            lambda k: pod_car_days.get(tuple(k.split("||", 1)), np.nan)
        )
        df["_FreeDays"] = np.where(~combo.isna(), combo, df["_FreeDays"])
    # pod-only override
    if pod_days:
        pod_only = df["_pod_key"].map(lambda k: pod_days.get(k, np.nan))
        df["_FreeDays"] = np.where(~pod_only.isna(), pod_only, df["_FreeDays"])
    # carrier-only override
    if car_days:
        car_only = df["_car_key"].map(lambda k: car_days.get(k, np.nan))
        df["_FreeDays"] = np.where(~car_only.isna(), car_only, df["_FreeDays"])

# compute Estimated LFD from Container Discharge + FreeDays
def add_days(discharge_ts: pd.Timestamp, n_days: int) -> pd.Timestamp:
    if pd.isna(discharge_ts) or pd.isna(n_days):
        return pd.NaT
    n = int(n_days)
    if business_days:
        return discharge_ts + BDay(n)
    else:
        return discharge_ts + pd.to_timedelta(n, unit="D")

df["_Estimated_LFD"] = [
    add_days(d, n) for d, n in zip(discharge_dt, df["_FreeDays"])
]

# Slack vs LFD (in hours): positive = before LFD; negative = after LFD (late)
df["_LFD_Slack_hours"] = (df["_Estimated_LFD"] - gate_out_dt).dt.total_seconds() / 3600.0
df["_LFD_Late"] = df["_LFD_Slack_hours"] < 0

# -----------------------
# Filters
# -----------------------
st.subheader("Filters")
carriers = st.multiselect("Carriers", sorted(df["_Carrier"].dropna().astype(str).unique().tolist()), default=None)
if carriers:
    df = df[df["_Carrier"].isin(carriers)]

# -----------------------
# Duration Summaries (same as before)
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

st.subheader("Milestone Gap Results")
st.dataframe(pretty, use_container_width=True)

st.download_button(
    "Download milestone-gap results (CSV)",
    data=pretty.to_csv(index=False).encode("utf-8"),
    file_name="carrier_port_bottleneck_summary.csv",
    mime="text/csv"
)

# -----------------------
# LFD Risk Summary (NEW)
# -----------------------
st.subheader("Estimated LFD Risk (Carrier → POD)")

def lfd_group_stats(g: pd.Series) -> pd.Series:
    s = pd.to_numeric(g, errors="coerce").dropna()
    if len(s) == 0:
        return pd.Series({
            "shipments": 0,
            "late_count": 0,
            "late_rate_%": np.nan,
            "median_slack_hours": np.nan,
            "avg_slack_hours": np.nan
        })
    late_count = (s < 0).sum()
    return pd.Series({
        "shipments": len(s),
        "late_count": late_count,
        "late_rate_%": round(100.0 * late_count / len(s), 2),
        "median_slack_hours": round(s.median(), 2),
        "avg_slack_hours": round(s.mean(), 2)
    })

lfd_summary = (
    df.groupby(["_Carrier", "_POD Port"])["_LFD_Slack_hours"]
      .apply(lfd_group_stats)
      .reset_index()
      .rename(columns={"_Carrier": "Carrier", "_POD Port": "POD Port"})
)

st.dataframe(lfd_summary, use_container_width=True)

st.download_button(
    "Download LFD summary (CSV)",
    data=lfd_summary.to_csv(index=False).encode("utf-8"),
    file_name="lfd_summary_by_carrier_pod.csv",
    mime="text/csv"
)
