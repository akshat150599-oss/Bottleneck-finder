import streamlit as st
import pandas as pd
import numpy as np

st.set_page_config(page_title="Ocean Bottleneck + LFD Analyzer", layout="wide")
st.title("📦 Ocean Bottleneck + LFD Analyzer")
st.caption("Compute POL/POD dwell stats and flag LFD risks using a single, estimated free-time period.")

@st.cache_data
def load_df(file):
    if file.name.lower().endswith(".csv"):
        df = pd.read_csv(file)
    else:
        df = pd.read_excel(file)
    df.columns = [str(c).strip() for c in df.columns]
    return df

def find_col(df, name_variants):
    cand = {c.lower(): c for c in df.columns}
    for v in name_variants:
        if v.lower() in cand:
            return cand[v.lower()]
    return None

def to_datetime(series, dayfirst=True):
    return pd.to_datetime(series, errors="coerce", dayfirst=dayfirst, infer_datetime_format=True)

def compute_duration_hours(df, start_col, end_col, dayfirst=True):
    if start_col is None or end_col is None:
        return pd.Series([np.nan] * len(df))
    start = to_datetime(df[start_col], dayfirst=dayfirst)
    end = to_datetime(df[end_col], dayfirst=dayfirst)
    return (end - start).dt.total_seconds() / 3600.0

def summarize(series, mode_round=1):
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) == 0:
        return pd.Series({"count": 0, "avg_hours": np.nan, "mean_hours": np.nan, "median_hours": np.nan, "mode_hours": np.nan})
    avg_val = s.mean()
    median_val = s.median()
    mode_vals = s.round(mode_round).mode()
    mode_val = mode_vals.iloc[0] if len(mode_vals) else np.nan
    return pd.Series({"count": len(s), "avg_hours": avg_val, "mean_hours": avg_val, "median_hours": median_val, "mode_hours": mode_val})

def build_summary(df, group_cols, value_col, label, mode_round=1):
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

# Simple weekend toggle (no holidays input to keep UI minimal)
def business_day_add(start_date, days, weekends_count=True):
    d = start_date
    added = 0
    while added < int(days):
        d += pd.Timedelta(days=1)
        is_weekend = d.weekday() >= 5  # Sat=5, Sun=6
        if weekends_count or not is_weekend:
            added += 1
    return d

def compute_lfd_from_estimate(base_ts, free_days, weekends_count=True, cutoff_rule="End of day", start_rule="Next day"):
    if pd.isna(base_ts) or pd.isna(free_days):
        return pd.NaT
    base_day = base_ts.normalize()
    start_count = base_day if start_rule == "Same day" else base_day + pd.Timedelta(days=1)
    if weekends_count:
        lfd_day = start_count + pd.Timedelta(days=int(free_days) - 1)
    else:
        lfd_day = business_day_add(start_count - pd.Timedelta(days=1), int(free_days), weekends_count=False)
    if cutoff_rule == "End of day":
        return lfd_day.replace(hour=23, minute=59, second=59)
    else:
        return base_ts + pd.Timedelta(days=float(free_days))

# ---------------- Upload ----------------
uploaded = st.file_uploader("Upload your file (.xlsx, .xls, .csv)", type=["xlsx", "xls", "csv"])
if not uploaded:
    st.info("Upload a file to begin the analysis.")
    st.stop()

df = load_df(uploaded)

# ---------------- Column Mapping ----------------
default_cols = {
    "shipment_id": find_col(df, ["Shipment ID","shipment_id","p44_shipment_id","P44_SHIPMENT_ID"]),
    "carrier": find_col(df, ["Carrier Name", "carrier", "Carrier"]),
    "pol": find_col(df, ["POL", "Port of Loading", "POL Name", "POL Port", "POL UNLOCODE", "Origin Port"]),
    "pod": find_col(df, ["POD", "Port of Discharge", "POD Name", "POD Port", "POD UNLOCODE", "Destination Port"]),
    "gate_in": find_col(df, ["2-Gate In Timestamp", "Gate In Timestamp", "2 - Gate In Timestamp"]),
    "container_loaded": find_col(df, ["3-Container Loaded Timestamp", "Container Loaded Timestamp", "3 - Container Loaded Timestamp"]),
    "discharge": find_col(df, ["6-Container Discharge Timestamp", "Container Discharge Timestamp", "6 - Container Discharge Timestamp"]),
    "gate_out": find_col(df, ["7-Gate Out Timestamp", "Gate Out Timestamp", "7 - Gate Out Timestamp"]),
    "empty_return": find_col(df, ["8-Empty Return Timestamp", "Empty Return Timestamp", "8 - Empty Return Timestamp"]),
    "availability": find_col(df, ["Availability Timestamp","Available Timestamp","Release Timestamp"]),
}

with st.expander("Column Mapping", expanded=True):
    c1, c2 = st.columns(2)
    with c1:
        shipment_col = st.selectbox("Shipment ID (optional)", options=["<None>"] + list(df.columns),
                                    index=(df.columns.get_loc(default_cols["shipment_id"]) + 1) if default_cols["shipment_id"] in df.columns else 0)
        carrier_col = st.selectbox("Carrier column", options=df.columns, index=df.columns.get_loc(default_cols["carrier"]) if default_cols["carrier"] in df.columns else 0)
        pol_col = st.selectbox("POL (Port of Loading)", options=df.columns, index=df.columns.get_loc(default_cols["pol"]) if default_cols["pol"] in df.columns else 0)
        gate_in_col = st.selectbox("2-Gate In Timestamp", options=df.columns, index=df.columns.get_loc(default_cols["gate_in"]) if default_cols["gate_in"] in df.columns else 0)
        container_loaded_col = st.selectbox("3-Container Loaded Timestamp", options=df.columns, index=df.columns.get_loc(default_cols["container_loaded"]) if default_cols["container_loaded"] in df.columns else 0)
    with c2:
        pod_col = st.selectbox("POD (Port of Discharge)", options=df.columns, index=df.columns.get_loc(default_cols["pod"]) if default_cols["pod"] in df.columns else 0)
        discharge_col = st.selectbox("6-Container Discharge Timestamp", options=df.columns, index=df.columns.get_loc(default_cols["discharge"]) if default_cols["discharge"] in df.columns else 0)
        gate_out_col = st.selectbox("7-Gate Out Timestamp", options=df.columns, index=df.columns.get_loc(default_cols["gate_out"]) if default_cols["gate_out"] in df.columns else 0)
        empty_return_col = st.selectbox("8-Empty Return Timestamp", options=df.columns, index=df.columns.get_loc(default_cols["empty_return"]) if default_cols["empty_return"] in df.columns else 0)
        availability_col = st.selectbox("Availability Timestamp (optional)", options=["<None>"] + list(df.columns),
                                        index=(df.columns.get_loc(default_cols["availability"]) + 1) if default_cols["availability"] in df.columns else 0)

# ---------------- Settings ----------------
st.subheader("Settings")
c1, c2, c3 = st.columns(3)
with c1:
    dayfirst = st.checkbox("Dates are day-first (DD/MM/YYYY)", value=True)
    unit = st.selectbox("Units", ["hours", "minutes"], index=0)
with c2:
    mode_round = st.slider("Mode rounding (units)", 0, 3, 1)
    negative_policy = st.selectbox("Negative durations", ["Keep (data-quality insight)", "Treat as NaN (drop from stats)"], index=1)
with c3:
    st.markdown("**Estimated LFD Settings**")
    free_days = st.number_input("Estimated free-time (days)", min_value=1, max_value=30, value=4, step=1)
    lfd_base = st.selectbox("Start counting from", ["Discharge (6→)", "Availability (if provided, else Discharge)"], index=0)
    weekends_count = st.checkbox("Count weekends in free-time?", value=True)
    cutoff_rule = st.selectbox("LFD cutoff rule", ["End of day", "Rolling 24h"], index=0)
    start_rule = st.selectbox("Free time starts", ["Next day", "Same day"], index=0)
    risk_horizon = st.slider("Approaching LFD (days to LFD)", 0, 7, 2)

def convert_units(series):
    return series * 60.0 if unit == "minutes" else series

# ---------------- Durations ----------------
pol_duration = compute_duration_hours(df, gate_in_col, container_loaded_col, dayfirst=dayfirst)
pod_dg = compute_duration_hours(df, discharge_col, gate_out_col, dayfirst=dayfirst)
pod_ge = compute_duration_hours(df, gate_out_col, empty_return_col, dayfirst=dayfirst)

if negative_policy == "Treat as NaN (drop from stats)":
    pol_duration = pol_duration.where(pol_duration >= 0)
    pod_dg = pod_dg.where(pod_dg >= 0)
    pod_ge = pod_ge.where(pod_ge >= 0)

df["_POL_duration"] = convert_units(pol_duration)
df["_POD_dg_duration"] = convert_units(pod_dg)
df["_POD_ge_duration"] = convert_units(pod_ge)

# ---------------- LFD estimation ----------------
gate_out_ts = to_datetime(df[gate_out_col], dayfirst=dayfirst)
discharge_ts = to_datetime(df[discharge_col], dayfirst=dayfirst)
availability_ts = to_datetime(df[availability_col], dayfirst=dayfirst) if availability_col != "<None>" else pd.Series([pd.NaT]*len(df))

base_ts = availability_ts.fillna(discharge_ts) if lfd_base.startswith("Availability") else discharge_ts
lfd_est = base_ts.apply(lambda ts: compute_lfd_from_estimate(ts, free_days, weekends_count=weekends_count, cutoff_rule=cutoff_rule, start_rule=start_rule))

lfd_margin_hours = (lfd_est - gate_out_ts).dt.total_seconds() / 3600.0
now_ts = pd.Timestamp.now()
days_to_lfd = (lfd_est - now_ts).dt.total_seconds() / 86400.0

df["_LFD_est"] = lfd_est
df["_LFD_margin_hours"] = lfd_margin_hours
df["_Days_to_LFD"] = days_to_lfd

# ---------------- Filters ----------------
st.subheader("Filters")
carriers = st.multiselect("Carriers", sorted(df[carrier_col].dropna().astype(str).unique().tolist()), default=None)
pods = st.multiselect("POD Ports", sorted(df[pod_col].dropna().astype(str).unique().tolist()), default=None)
if carriers:
    df = df[df[carrier_col].astype(str).isin(carriers)]
if pods:
    df = df[df[pod_col].astype(str).isin(pods)]

# ---------------- Dwell summaries ----------------
st.header("Dwell Summaries")
pol_summary = build_summary(df, [carrier_col, pol_col], "_POL_duration", "GateIn→ContainerLoaded (POL)", mode_round=mode_round)
pod_dg_summary = build_summary(df, [carrier_col, pod_col], "_POD_dg_duration", "Discharge→GateOut (POD)", mode_round=mode_round)
pod_ge_summary = build_summary(df, [carrier_col, pod_col], "_POD_ge_duration", "GateOut→EmptyReturn (POD)", mode_round=mode_round)

results = pd.concat([pol_summary, pod_dg_summary, pod_ge_summary], ignore_index=True)
unit_suffix = " (mins)" if unit == "minutes" else " (hrs)"
pretty = results[[carrier_col, "Metric", pol_col, pod_col, "count", "avg_hours", "mean_hours", "median_hours", "mode_hours"]].copy()
pretty = pretty.rename(columns={
    carrier_col: "Carrier",
    pol_col: "POL Port",
    pod_col: "POD Port",
    "avg_hours": "Average" + unit_suffix,
    "mean_hours": "Mean" + unit_suffix,
    "median_hours": "Median" + unit_suffix,
    "mode_hours": "Mode" + unit_suffix,
})
for c in ["Average" + unit_suffix, "Mean" + unit_suffix, "Median" + unit_suffix, "Mode" + unit_suffix]:
    pretty[c] = pd.to_numeric(pretty[c], errors="coerce").round(2)

st.dataframe(pretty, use_container_width=True)
st.download_button("Download dwell summary (CSV)",
                   data=pretty.to_csv(index=False).encode("utf-8"),
                   file_name="carrier_port_bottleneck_summary.csv",
                   mime="text/csv")

# ---------------- LFD Risk Board ----------------
st.header("LFD Risk Board (Estimated)")
ship_id_series = df[shipment_col] if shipment_col != "<None>" else pd.Series([np.nan]*len(df))

late_mask = (~gate_out_ts.isna()) & (~lfd_est.isna()) & (lfd_margin_hours < 0)
late = pd.DataFrame({
    "Shipment ID": ship_id_series.where(late_mask),
    "Carrier": df[carrier_col].where(late_mask),
    "POD Port": df[pod_col].where(late_mask),
    "Discharge": discharge_ts.where(late_mask),
    "Gate Out": gate_out_ts.where(late_mask),
    "LFD (est)": lfd_est.where(late_mask),
    "Lateness (hrs)": lfd_margin_hours.where(late_mask),
}).dropna(subset=["Carrier","POD Port","LFD (est)"], how="all")
late["Lateness (hrs)"] = pd.to_numeric(late["Lateness (hrs)"], errors="coerce").round(2)

approach_mask = (gate_out_ts.isna()) & (~lfd_est.isna()) & (days_to_lfd <= risk_horizon) & (days_to_lfd >= -30)
approaching = pd.DataFrame({
    "Shipment ID": ship_id_series.where(approach_mask),
    "Carrier": df[carrier_col].where(approach_mask),
    "POD Port": df[pod_col].where(approach_mask),
    "Discharge": discharge_ts.where(approach_mask),
    "LFD (est)": lfd_est.where(approach_mask),
    "Days to LFD": days_to_lfd.where(approach_mask),
}).dropna(subset=["Carrier","POD Port","LFD (est)"], how="all")
approaching["Days to LFD"] = pd.to_numeric(approaching["Days to LFD"], errors="coerce").round(2)

c1, c2 = st.columns(2)
with c1:
    st.subheader("Late after LFD (Gated Out)")
    st.dataframe(late.sort_values("Lateness (hrs)", ascending=True), use_container_width=True)
    st.download_button("Download late-after-LFD shipments (CSV)",
                       data=late.to_csv(index=False).encode("utf-8"),
                       file_name="lfd_late_shipments.csv",
                       mime="text/csv")
with c2:
    st.subheader(f"Approaching LFD (≤ {risk_horizon} days) — Still in Terminal")
    st.dataframe(approaching.sort_values("Days to LFD", ascending=True), use_container_width=True)
    st.download_button("Download approaching-LFD shipments (CSV)",
                       data=approaching.to_csv(index=False).encode("utf-8"),
                       file_name="lfd_approaching_shipments.csv",
                       mime="text/csv")

# ---------------- LFD summary by Carrier → POD ----------------
st.subheader("LFD Summary by Carrier → POD")
group_cols = [carrier_col, pod_col]
lfd_group = df.assign(
    __late = late_mask.astype(int),
    __approach = ((gate_out_ts.isna()) & (~lfd_est.isna()) & (days_to_lfd <= risk_horizon)).astype(int),
    __lateness_hrs = lfd_margin_hours.where(late_mask, np.nan),
).groupby(group_cols).agg(
    shipments=("__late", "count"),
    late_shipments=("__late", "sum"),
    approaching_shipments=("__approach", "sum"),
    median_lateness_hours=("__lateness_hrs", "median"),
).reset_index()

lfd_group["late_pct"] = (100.0 * lfd_group["late_shipments"] / lfd_group["shipments"]).round(2)
lfd_group["median_lateness_hours"] = lfd_group["median_lateness_hours"].round(2)

lfd_pretty = lfd_group.rename(columns={
    carrier_col: "Carrier",
    pod_col: "POD Port",
    "shipments": "Shipments",
    "late_shipments": "Late (count)",
    "late_pct": "Late (%)",
    "approaching_shipments": f"Approaching ≤{risk_horizon}d (count)",
    "median_lateness_hours": "Median Lateness (hrs)",
})

st.dataframe(lfd_pretty.sort_values(["Late (%)","Median Lateness (hrs)"], ascending=[False, True]), use_container_width=True)
st.download_button("Download LFD summary (CSV)",
                   data=lfd_pretty.to_csv(index=False).encode("utf-8"),
                   file_name="lfd_summary_by_carrier_pod.csv",
                   mime="text/csv")
