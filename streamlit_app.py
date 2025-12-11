# ============================================
# D&D HOURS UI – COMPLETED SHIPMENTS ONLY
# (Paste this after you compute _LFD_Slack_hours, _POD_dg_duration, _POD_ge_duration, etc.)
# ============================================

st.divider()
st.header("⏱ Demurrage & Detention Hours – Completed Shipments")

# --- Basic masks & helper columns (Demurrage at POD) ---

# Over free days = slack < 0 (LFD is a POD demurrage concept)
dem_over_mask = df["_LFD_Slack_hours"] < 0
dem_within_mask = ~dem_over_mask

# Hours over free days (only for shipments that went over)
hours_over_dem = (-df.loc[dem_over_mask, "_LFD_Slack_hours"]).clip(lower=0)

total_ship = len(df)
count_over = dem_over_mask.sum()
count_within = dem_within_mask.sum()
pct_over = 100.0 * count_over / total_ship if total_ship else 0.0

total_over_hours = hours_over_dem.sum()
avg_over_hours = hours_over_dem.mean() if len(hours_over_dem) else 0.0

# Demurrage hours (Discharge → Gate Out)
# NOTE: _POD_dg_duration is already in the user-selected unit ("hours" or "minutes")
dem_unit = unit  # from your earlier settings: "hours" or "minutes"

# Detention hours (Gate Out → Empty Return) – for visibility only
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
    st.metric("Avg Hours Over (overage only)", f"{avg_over_hours:,.1f} hrs" if count_over else "0.0 hrs")

st.caption(
    "Over Free Days is determined using slack vs LFD (Last Free Day) at POD. "
    "Only completed shipments are included in this view."
)

# --- Optional: quick view of total demurrage vs detention hours ---

colA, colB = st.columns(2)
with colA:
    total_dem_hours = df["_POD_dg_duration"].sum()
    avg_dem_hours = df["_POD_dg_duration"].mean()
    st.metric(f"Total Demurrage ({dem_unit})", f"{total_dem_hours:,.0f}")
    st.metric(f"Avg Demurrage per Shipment ({dem_unit})", f"{avg_dem_hours:,.1f}" if not np.isnan(avg_dem_hours) else "–")
with colB:
    st.metric(f"Total Detention ({det_unit})", f"{total_det_hours:,.0f}")
    st.metric(f"Avg Detention per Shipment ({det_unit})", f"{avg_det_hours:,.1f}" if not np.isnan(avg_det_hours) else "–")

st.divider()

# --- Tabs: Charts / Port & Carrier / Lane / Shipments ---

tab_overview, tab_port_carrier, tab_lane, tab_shipments = st.tabs(
    ["Charts", "By Port & Carrier", "By Lane (POL → POD)", "Shipment Explorer"]
)

# =========================
# 1) CHARTS TAB
# =========================
with tab_overview:
    st.subheader("D&D Hours Distribution")

    # Demurrage hours by POD Port (top 10)
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

    # Detention hours by POD Port (top 10)
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

# =========================
# 2) BY PORT & CARRIER TAB
# =========================
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

    # Round numeric columns for display
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

# =========================
# 3) BY LANE TAB
# =========================
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

# =========================
# 4) SHIPMENT EXPLORER TAB
# =========================
with tab_shipments:
    st.subheader("Shipment Explorer – Over vs Within Free Days (Demurrage at POD)")

    only_over = st.checkbox("Show only shipments over free days (demurrage)", value=True)

    diag = pd.DataFrame({
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

    diag["Demurrage Status"] = np.where(diag["Slack vs LFD (hrs)"] < 0, "Over Free Days", "Within Free Days")

    if only_over:
        diag_view = diag[diag["Demurrage Status"] == "Over Free Days"].copy()
    else:
        diag_view = diag.copy()

    st.dataframe(diag_view, use_container_width=True)

    st.download_button(
        "Download: Shipment-level D&D Hours (CSV)",
        data=diag_view.to_csv(index=False).encode("utf-8"),
        file_name="shipment_dd_hours_completed.csv",
        mime="text/csv",
    )
