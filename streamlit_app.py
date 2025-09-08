# =========================
# SUGGESTIONS (NEW SECTION)
# =========================
st.divider()
st.header("🧠 Suggestions")

# Shared knobs
st.subheader("Settings for Suggestions")
colA, colB, colC, colD = st.columns([1,1,1,1])
with colA:
    min_count_side = st.number_input("Min shipments per side", 1, 100, 10, 1)
with colB:
    improve_pct = st.slider("Required improvement vs lane avg (%)", 0, 50, 10, 1,
                            help="Carrier must beat lane average by this % on BOTH POL & POD.")
with colC:
    w_pol = st.number_input("Weight: POL dwell", 0.0, 1.0, 0.5, 0.1)
with colD:
    w_pod = st.number_input("Weight: POD dwell", 0.0, 1.0, 0.5, 0.1)

# Normalize weights safely
w_sum = w_pol + w_pod
if w_sum == 0:
    w_pol, w_pod = 0.5, 0.5
else:
    w_pol, w_pod = w_pol / w_sum, w_pod / w_sum

# Convenience: current unit suffix (same as earlier tables)
unit_suffix = "mins" if unit == "minutes" else "hrs"

# Helper to compute means safely with count filter
def mean_and_count(s):
    s = pd.to_numeric(s, errors="coerce").dropna()
    return pd.Series({"mean": s.mean(), "count": len(s)})

# -------------------------------------------------
# 1) Best Carrier for Lane (POL→POD) — using AVERAGE
# -------------------------------------------------
st.subheader("1) Best Carrier for Lane (POL → POD) — using AVERAGE")

# Carrier × Lane (POL,POD): side means and counts
lane_car = (
    df.groupby(["_Carrier", "_POL Port", "_POD Port"])
      .agg(POL_mean = ("_POL_duration", "mean"),
           POL_cnt  = ("_POL_duration", lambda s: pd.to_numeric(s, errors="coerce").notna().sum()),
           POD_mean = ("_POD_dg_duration", "mean"),
           POD_cnt  = ("_POD_dg_duration", lambda s: pd.to_numeric(s, errors="coerce").notna().sum()))
      .reset_index()
)

# Lane baselines (across all carriers & shipments)
lane_base = (
    df.groupby(["_POL Port", "_POD Port"])
      .agg(lane_POL_mean=(" _POL_duration".strip(), "mean"),
           lane_POD_mean=(" _POD_dg_duration".strip(), "mean"))
      .reset_index()
)

best_lane = (
    lane_car.merge(lane_base, on=["_POL Port", "_POD Port"], how="left")
            .rename(columns={"_Carrier":"Carrier","_POL Port":"POL","_POD Port":"POD"})
)

# Eligibility: enough shipments on BOTH sides, and beats lane avg by improve_pct on BOTH
eps = 1e-9
thr = (100 - improve_pct) / 100.0  # carrier mean must be <= lane_mean * thr
eligible = (
    (best_lane["POL_cnt"] >= min_count_side) &
    (best_lane["POD_cnt"] >= min_count_side) &
    (best_lane["POL_mean"] <= best_lane["lane_POL_mean"] * thr) &
    (best_lane["POD_mean"] <= best_lane["lane_POD_mean"] * thr)
)

cand = best_lane.loc[eligible].copy()

# Composite score (higher is better) — weighted % improvement at both ends
cand["imp_POL"] = (best_lane["lane_POL_mean"] - cand["POL_mean"]) / (best_lane["lane_POL_mean"] + eps)
cand["imp_POD"] = (best_lane["lane_POD_mean"] - cand["POD_mean"]) / (best_lane["lane_POD_mean"] + eps)
cand["Score"]   = w_pol * cand["imp_POL"] + w_pod * cand["imp_POD"]

# Display
if cand.empty:
    st.info("No carrier qualifies yet under current thresholds. Try lowering 'Min shipments' or 'Required improvement'.")
else:
    out = cand[["POL","POD","Carrier","POL_cnt","POL_mean","lane_POL_mean","POD_cnt","POD_mean","lane_POD_mean","imp_POL","imp_POD","Score"]].copy()
    out = out.rename(columns={
        "POL_cnt": f"Count POL",
        "POD_cnt": f"Count POD",
        "POL_mean": f"Avg POL ({unit_suffix})",
        "lane_POL_mean": f"Lane Avg POL ({unit_suffix})",
        "POD_mean": f"Avg POD ({unit_suffix})",
        "lane_POD_mean": f"Lane Avg POD ({unit_suffix})",
        "imp_POL": "Improvement POL",
        "imp_POD": "Improvement POD",
    })
    # Pretty formatting
    for c in [f"Avg POL ({unit_suffix})", f"Lane Avg POL ({unit_suffix})",
              f"Avg POD ({unit_suffix})", f"Lane Avg POD ({unit_suffix})",
              "Improvement POL", "Improvement POD", "Score"]:
        out[c] = pd.to_numeric(out[c], errors="coerce").round(3)
    out = out.sort_values("Score", ascending=False).reset_index(drop=True)
    st.dataframe(out, use_container_width=True)
    st.download_button("Download: Best Carrier for Lane (CSV)", out.to_csv(index=False).encode("utf-8"),
                       file_name="best_carrier_for_lane_avg.csv", mime="text/csv")

# -----------------------------------------------
# 2) Alternate Lane Suggestions (same-country only)
# -----------------------------------------------
st.subheader("2) Alternate Lane Suggestions (same country, from your data)")

# Optional Port Master CSV for geodesic distances
pm_file = st.file_uploader(
    "Optional Port Master CSV (columns like: Port Name / POL Port / POD Port / UNLOCODE, Country, Latitude, Longitude)",
    type=["csv"], key="portmaster"
)

col1, col2, col3 = st.columns([1,1,1])
with col1:
    max_km = st.number_input("Max neighbor distance (km)", 10, 2000, 400, 10)
with col2:
    alt_improve_pct = st.slider("Min improvement to suggest (%)", 0, 50, 15, 1)
with col3:
    alt_min_count = st.number_input("Min shipments on suggested lane", 1, 100, 10, 1)

def to_float(x):
    try:
        return float(x)
    except Exception:
        return np.nan

if pm_file is None:
    st.info("Upload a Port Master CSV to enable alternate lane suggestions.")
else:
    pm = pd.read_csv(pm_file)
    # Flexible column mapping
    lc = {c.lower(): c for c in pm.columns}
    def pick(*opts):
        for o in opts:
            if o.lower() in lc:
                return lc[o.lower()]
        return None
    col_port = pick("Port Name","Port","POD Port","POL Port","UNLOCODE")
    col_ctry = pick("Country","Country Code","ISO2","ISO3")
    col_lat  = pick("Latitude","Lat","lat")
    col_lon  = pick("Longitude","Lon","Long","lng")

    if not (col_port and col_ctry and col_lat and col_lon):
        st.warning("Port Master CSV missing required columns (Port, Country, Latitude, Longitude).")
    else:
        # Normalize master
        pm["pm_port"] = pm[col_port].astype(str).str.strip()
        pm["pm_ctry"] = pm[col_ctry].astype(str).str.strip()
        pm["pm_lat"]  = pm[col_lat].apply(to_float)
        pm["pm_lon"]  = pm[col_lon].apply(to_float)
        pm = pm.dropna(subset=["pm_port","pm_ctry","pm_lat","pm_lon"])

        # Build small lookup for ports present in data
        ports_pol = pd.Series(df["_POL Port"].unique(), dtype=str).str.strip()
        ports_pod = pd.Series(df["_POD Port"].unique(), dtype=str).str.strip()
        pm_pol = pm[pm["pm_port"].isin(ports_pol)]
        pm_pod = pm[pm["pm_port"].isin(ports_pod)]

        # Haversine (km)
        def haversine(lat1, lon1, lat2, lon2):
            R = 6371.0
            lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
            dlat = lat2 - lat1
            dlon = lon2 - lon1
            a = np.sin(dlat/2)**2 + np.cos(lat1)*np.cos(lat2)*np.sin(dlon/2)**2
            c = 2*np.arcsin(np.sqrt(a))
            return R * c

        # Precompute lane-level means and counts for both sides
        lane_stats = (
            df.groupby(["_POL Port","_POD Port"])
              .agg(POL_mean=(" _POL_duration".strip(), "mean"),
                   POL_cnt =(" _POL_duration".strip(), lambda s: pd.to_numeric(s, errors="coerce").notna().sum()),
                   POD_mean=(" _POD_dg_duration".strip(), "mean"),
                   POD_cnt =(" _POD_dg_duration".strip(), lambda s: pd.to_numeric(s, errors="coerce").notna().sum()))
              .reset_index()
              .rename(columns={"_POL Port":"POL","_POD Port":"POD"})
        )

        # Helper: find nearest same-country candidates for a given port name in pm_df
        def nearest_same_country(pm_df, port_name, maxkm):
            row = pm_df[pm_df["pm_port"].str.lower() == str(port_name).strip().lower()]
            if row.empty:
                return pd.DataFrame(columns=pm_df.columns)
            lat0, lon0, ctry = row.iloc[0]["pm_lat"], row.iloc[0]["pm_lon"], row.iloc[0]["pm_ctry"]
            same = pm_df[pm_df["pm_ctry"] == ctry].copy()
            same["dist_km"] = haversine(lat0, lon0, same["pm_lat"], same["pm_lon"])
            same = same[same["pm_port"].str.lower() != str(port_name).strip().lower()]
            return same[same["dist_km"] <= maxkm].sort_values("dist_km")

        rows = []
        # Iterate existing lanes to propose alternatives on the "slower" side
        for _, r in lane_stats.iterrows():
            pol, pod = r["POL"], r["POD"]
            cur_pol_mean, cur_pod_mean = r["POL_mean"], r["POD_mean"]

            # If POL side looks slow relative to other observed lanes to same POD, propose alt POL
            if not pd.isna(cur_pol_mean) and pm_pol[pm_pol["pm_port"].str.lower() == pol.lower()].shape[0] == 1:
                cand_pol = nearest_same_country(pm_pol, pol, max_km)
                for _, cpol in cand_pol.iterrows():
                    alt_pol = cpol["pm_port"]; dist = cpol["dist_km"]
                    # Only consider if the alt lane exists in our data with the same POD
                    alt_row = lane_stats[(lane_stats["POL"] == alt_pol) & (lane_stats["POD"] == pod)]
                    if alt_row.empty: 
                        continue
                    alt_mean = alt_row.iloc[0]["POL_mean"]; alt_cnt = alt_row.iloc[0]["POL_cnt"]
                    if pd.isna(alt_mean) or alt_cnt < alt_min_count:
                        continue
                    imp = (cur_pol_mean - alt_mean) / (cur_pol_mean + 1e-9)
                    if imp*100 >= alt_improve_pct:
                        rows.append({
                            "Current POL": pol, "Suggested POL": alt_pol, "POD (fixed)": pod,
                            "Side Changed": "POL",
                            f"Distance (km)": round(float(dist), 1),
                            f"Current Avg POL ({unit_suffix})": round(float(cur_pol_mean), 2),
                            f"Suggested Avg POL ({unit_suffix})": round(float(alt_mean), 2),
                            "Improvement %": round(float(imp*100), 1),
                            "Suggested Count": int(alt_cnt)
                        })

            # If POD side looks slow relative to other observed lanes from same POL, propose alt POD
            if not pd.isna(cur_pod_mean) and pm_pod[pm_pod["pm_port"].str.lower() == pod.lower()].shape[0] == 1:
                cand_pod = nearest_same_country(pm_pod, pod, max_km)
                for _, cpod in cand_pod.iterrows():
                    alt_pod = cpod["pm_port"]; dist = cpod["dist_km"]
                    alt_row = lane_stats[(lane_stats["POL"] == pol) & (lane_stats["POD"] == alt_pod)]
                    if alt_row.empty:
                        continue
                    alt_mean = alt_row.iloc[0]["POD_mean"]; alt_cnt = alt_row.iloc[0]["POD_cnt"]
                    if pd.isna(alt_mean) or alt_cnt < alt_min_count:
                        continue
                    imp = (cur_pod_mean - alt_mean) / (cur_pod_mean + 1e-9)
                    if imp*100 >= alt_improve_pct:
                        rows.append({
                            "POL (fixed)": pol, "Current POD": pod, "Suggested POD": alt_pod,
                            "Side Changed": "POD",
                            f"Distance (km)": round(float(dist), 1),
                            f"Current Avg POD ({unit_suffix})": round(float(cur_pod_mean), 2),
                            f"Suggested Avg POD ({unit_suffix})": round(float(alt_mean), 2),
                            "Improvement %": round(float(imp*100), 1),
                            "Suggested Count": int(alt_cnt)
                        })

        alt_df = pd.DataFrame(rows)
        if alt_df.empty:
            st.info("No alternate lane met the distance, improvement, and sample-size thresholds.")
        else:
            st.dataframe(alt_df.sort_values("Improvement %", ascending=False).reset_index(drop=True),
                         use_container_width=True)
            st.download_button("Download: Alternate Lane Suggestions (CSV)",
                               data=alt_df.to_csv(index=False).encode("utf-8"),
                               file_name="alternate_lane_suggestions.csv",
                               mime="text/csv")

# -----------------------------------------------
# 3) Bottleneck Points (choke points)
# -----------------------------------------------
st.subheader("3) Bottleneck Points (choke points)")

topN = st.slider("Show Top N per list", 5, 50, 10, 1)

# Carrier × Lane averages for key gaps
lane_car_full = (
    df.groupby(["_Carrier","_POL Port","_POD Port"])
      .agg(Avg_POL = ("_POL_duration", "mean"),
           Cnt_POL = ("_POL_duration", lambda s: pd.to_numeric(s, errors="coerce").notna().sum()),
           Avg_POD = ("_POD_dg_duration", "mean"),
           Cnt_POD = ("_POD_dg_duration", lambda s: pd.to_numeric(s, errors="coerce").notna().sum()),
           Avg_RET = ("_POD_ge_duration", "mean"),
           Cnt_RET = ("_POD_ge_duration", lambda s: pd.to_numeric(s, errors="coerce").notna().sum()))
      .reset_index()
      .rename(columns={"_Carrier":"Carrier","_POL Port":"POL","_POD Port":"POD"})
)

# Worst dwell on POL side
worst_pol = lane_car_full[lane_car_full["Cnt_POL"] >= min_count_side].copy()
worst_pol = worst_pol.sort_values("Avg_POL", ascending=False).head(topN)
worst_pol = worst_pol.rename(columns={"Avg_POL": f"Avg POL ({unit_suffix})", "Cnt_POL":"Count POL"})

# Worst dwell on POD side
worst_pod = lane_car_full[lane_car_full["Cnt_POD"] >= min_count_side].copy()
worst_pod = worst_pod.sort_values("Avg_POD", ascending=False).head(topN)
worst_pod = worst_pod.rename(columns={"Avg_POD": f"Avg POD ({unit_suffix})", "Cnt_POD":"Count POD"})

# Worst return step (optional signal for empty congestion)
worst_ret = lane_car_full[lane_car_full["Cnt_RET"] >= min_count_side].copy()
worst_ret = worst_ret.sort_values("Avg_RET", ascending=False).head(topN)
worst_ret = worst_ret.rename(columns={"Avg_RET": f"Avg Return ({unit_suffix})", "Cnt_RET":"Count Return"})

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
