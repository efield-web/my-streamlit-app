
import base64
import io
import re
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="efield | Inc. 5000 × Industry Risk",
    page_icon="🇺🇸",
    layout="wide",
)

# --- Custom fonts: Raleway for titles/headings, Montserrat for body text ---
st.markdown(
    """
    <link href="https://fonts.googleapis.com/css2?family=Raleway:wght@400;600;700;800&family=Montserrat:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
    h1, h2, h3, h4, h5, h6,
    [data-testid="stMarkdownContainer"] h1,
    [data-testid="stMarkdownContainer"] h2,
    [data-testid="stMarkdownContainer"] h3 {
        font-family: 'Raleway', sans-serif !important;
    }

    .stApp,
    [data-testid="stMarkdownContainer"] p,
    [data-testid="stMarkdownContainer"] li,
    .stCaption, [data-testid="stMetricLabel"],
    [data-testid="stMetricValue"] {
        font-family: 'Montserrat', sans-serif !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

SUPPORTED_YEARS = [2019, 2023]

# Damodaran archive naming convention:
# betas18.xls = Jan-2020 dataset, betas19.xls = Jan-2021, ... betas24.xls = Jan-2026.
DAMODARAN_BETA_URLS = {
    2019: "https://pages.stern.nyu.edu/~adamodar/pc/archives/betas17.xls",
    2020: "https://pages.stern.nyu.edu/~adamodar/pc/archives/betas18.xls",
    2021: "https://pages.stern.nyu.edu/~adamodar/pc/archives/betas19.xls",
    2022: "https://pages.stern.nyu.edu/~adamodar/pc/archives/betas20.xls",
    2023: "https://pages.stern.nyu.edu/~adamodar/pc/archives/betas21.xls",
    2024: "https://pages.stern.nyu.edu/~adamodar/pc/archives/betas22.xls",
    2025: "https://pages.stern.nyu.edu/~adamodar/pc/archives/betas23.xls",
    2026: "https://pages.stern.nyu.edu/~adamodar/pc/archives/betas24.xls",
}

# Inc. industry labels are not identical to Damodaran's industry taxonomy.
# This mapping is deliberately conservative: unmapped categories remain visible
# rather than being assigned a misleading beta.
INDUSTRY_MAP = {
    "Software": "Software (System & Application)",
    "IT Services": "Computer Services",
    "Computer Hardware": "Computers/Peripherals",
    "Telecommunications": "Telecom. Services",
    "Energy": "Oil/Gas (Production and Exploration)",
    "Environmental Services": "Environmental & Waste Services",
    "Engineering": "Engineering/Construction",
    "Construction": "Engineering/Construction",
    "Manufacturing": "Diversified",
    "Financial Services": "Financial Svcs. (Non-bank & Insurance)",
    "Insurance": "Insurance (General)",
    "Health": "Healthcare Support Services",
    "Healthcare": "Healthcare Support Services",
    "Education": "Education",
    "Real Estate": "Real Estate (Development)",
    "Retail": "Retail (General)",
    "Food & Beverage": "Food Processing",
    "Food & Beverages": "Food Processing",
    "Consumer Products & Services": "Household Products",
    "Business Products & Services": "Business & Consumer Services",
    "Business Products & Services": "Business & Consumer Services",
    "Government Services": "Business & Consumer Services",
    "Advertising & Marketing": "Advertising",
    "Media": "Publishing & Newspapers",
    "Travel & Hospitality": "Hotel/Gaming",
    "Logistics & Transportation": "Transportation",
    "Transportation": "Transportation",
    "Security": "Business & Consumer Services",
    "Human Resources": "Business & Consumer Services",
    "Apparel": "Apparel",
    "Retail": "Retail (General)",
}

def clean_col(x):
    return re.sub(r"[^a-z0-9]+", "_", str(x).strip().lower()).strip("_")

def find_col(df, candidates):
    cols = {clean_col(c): c for c in df.columns}
    for candidate in candidates:
        key = clean_col(candidate)
        if key in cols:
            return cols[key]
    # fuzzy fallback
    for key, original in cols.items():
        if any(clean_col(candidate) in key or key in clean_col(candidate) for candidate in candidates):
            return original
    return None

def normalize_inc(df, year):
    if df is None or df.empty:
        return pd.DataFrame()

    rank_col = find_col(df, ["rank", "ranking"])
    company_col = find_col(df, ["company", "company_name", "name"])
    industry_col = find_col(df, ["industry"])
    growth_col = find_col(df, ["growth", "3yr_growth", "growth_rate", "% growth", "growth_percentage"])
    revenue_col = find_col(df, ["revenue", "revenue_usd"])
    state_col = find_col(df, ["state", "state_s"])
    city_col = find_col(df, ["city"])
    founded_col = find_col(df, ["founded", "founded_year"])

    out = pd.DataFrame(index=df.index)
    out["rank"] = pd.to_numeric(df[rank_col], errors="coerce") if rank_col else pd.NA
    out["company"] = df[company_col].astype(str) if company_col else ""
    out["industry"] = df[industry_col].astype(str) if industry_col else "Unknown"
    out["year"] = year

    if growth_col:
        out["growth_pct"] = (
            df[growth_col].astype(str)
            .str.replace(",", "", regex=False)
            .str.replace("%", "", regex=False)
            .str.extract(r"([-+]?\d*\.?\d+)")[0]
        )
        out["growth_pct"] = pd.to_numeric(out["growth_pct"], errors="coerce")
    else:
        out["growth_pct"] = pd.NA

    if revenue_col:
        # Supports numeric revenue and common "$123.4m/$1.2b" strings.
        s = df[revenue_col].astype(str).str.lower().str.replace(",", "", regex=False)
        nums = pd.to_numeric(s.str.extract(r"([-+]?\d*\.?\d+)")[0], errors="coerce")
        out["revenue_raw"] = df[revenue_col]
        out["revenue_usd"] = nums
        out.loc[s.str.contains("b", na=False), "revenue_usd"] *= 1_000_000_000
        out.loc[s.str.contains("m", na=False), "revenue_usd"] *= 1_000_000
        out.loc[s.str.contains("k", na=False), "revenue_usd"] *= 1_000
    else:
        out["revenue_raw"] = pd.NA
        out["revenue_usd"] = pd.NA

    out["state"] = df[state_col].astype(str) if state_col else ""
    out["city"] = df[city_col].astype(str) if city_col else ""
    out["founded"] = df[founded_col] if founded_col else pd.NA
    return out

def fetch_inc_api(year):
    url = f"https://api.inc.com/rest/i5list/{year}"
    r = requests.get(url, timeout=30, headers={"User-Agent": "RACC-IT research dashboard"})
    r.raise_for_status()
    payload = r.json()
    companies = payload.get("companies", payload)
    if isinstance(companies, dict):
        companies = list(companies.values())
    df = pd.json_normalize(companies)
    return normalize_inc(df, year)

def parse_beta_workbook(source, source_label):
    """Parse a Damodaran beta workbook (local path, file-like, or BytesIO).

    Scans each sheet for the header row (it isn't always row 0 in Damodaran's
    files — there's often a title/date row above it), then locates the
    industry and beta columns from that header.
    """
    xls = pd.ExcelFile(source)
    for sheet in xls.sheet_names:
        raw = pd.read_excel(xls, sheet_name=sheet, header=None)
        header_row = None
        for i in range(min(40, len(raw))):
            row_text = re.sub(r"\s+", " ", " ".join(str(v) for v in raw.iloc[i].tolist()).lower())
            if "industry name" in row_text:
                header_row = i
                break
        if header_row is None:
            continue
        df = pd.read_excel(xls, sheet_name=sheet, header=header_row)
        industry_col = find_col(df, ["industry name", "industry"])
        beta_col = find_col(df, ["beta", "levered beta", "average beta"])
        if industry_col and beta_col:
            out = df[[industry_col, beta_col]].copy()
            out.columns = ["damodaran_industry", "beta"]
            out["beta"] = pd.to_numeric(out["beta"], errors="coerce")
            out = out.dropna(subset=["damodaran_industry", "beta"])
            return out
    raise ValueError(f"Could not identify beta table in {source_label}")

@st.cache_data(ttl=86400)
def fetch_beta(year):
    url = DAMODARAN_BETA_URLS[year]
    r = requests.get(url, timeout=30, headers={"User-Agent": "RACC-IT research dashboard"})
    r.raise_for_status()
    return parse_beta_workbook(io.BytesIO(r.content), url)

DATA_DIR = Path("data")

@st.cache_data(ttl=86400)
def load_local_beta(year):
    """Load a Damodaran beta file bundled in the repo's data/ folder.

    Looks for data/betas_{year}.xls first, then .xlsx.
    """
    for suffix in (".xls", ".xlsx"):
        path = DATA_DIR / f"betas_{year}{suffix}"
        if path.exists():
            return parse_beta_workbook(path, str(path))
    raise FileNotFoundError(
        f"No local beta file found for {year} in {DATA_DIR}/ "
        f"(expected betas_{year}.xls or betas_{year}.xlsx)."
    )

@st.cache_data(ttl=86400)
def load_local_inc5000(year):
    """Load the Inc. 5000 file for `year` bundled in the repo's data/ folder.

    Looks for data/inc5000_{year}.parquet first, then .xlsx, then .csv.
    """
    for suffix, reader in ((".parquet", pd.read_parquet), (".xlsx", pd.read_excel), (".csv", pd.read_csv)):
        path = DATA_DIR / f"inc5000_{year}{suffix}"
        if path.exists():
            df = reader(path)
            return normalize_inc(df, year)
    raise FileNotFoundError(
        f"No Inc. 5000 data file found for {year} in {DATA_DIR}/ "
        f"(expected inc5000_{year}.parquet, .xlsx, or .csv)."
    )

def classify_industry(label):
    label = str(label).strip()
    if label in INDUSTRY_MAP:
        return INDUSTRY_MAP[label]
    # Helpful matching for common variants.
    for k, v in INDUSTRY_MAP.items():
        if k.lower() in label.lower():
            return v
    return pd.NA

# Full US state (and DC) names mapped to their 2-letter postal abbreviation.
US_STATE_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC", "washington d.c.": "DC",
}
VALID_STATE_ABBRS = set(US_STATE_ABBR.values())

def to_state_abbr(value):
    """Normalize a state value (full name or abbreviation) to its 2-letter code."""
    s = str(value).strip()
    if not s or s.lower() in ("nan", "none", "<na>"):
        return pd.NA
    if len(s) == 2 and s.upper() in VALID_STATE_ABBRS:
        return s.upper()
    return US_STATE_ABBR.get(s.lower(), pd.NA)

header_left, header_right = st.columns([4, 1])
with header_left:
    st.title("US Private Companies Growth × Industry Risk")
    st.caption("efield Solutions research dashboard inspired by the US Equity Market bivariate analysis")
with header_right:
    logo_path = Path("assets/logo.png")
    if logo_path.exists():
        logo_b64 = base64.b64encode(logo_path.read_bytes()).decode()
        st.markdown(
            f"""
            <div style="margin-top:-2.4rem; padding-right:1cm; text-align:right;">
                <img src="data:image/png;base64,{logo_b64}" width="80">
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        # Placeholder until assets/logo.png is added to the repo.
        st.markdown(
            """
            <div style="height:70px; width:80px; margin-top:-2.4rem; margin-right:1cm; margin-left:auto;
                        border:1px dashed #999; border-radius:6px;
                        display:flex; align-items:center; justify-content:center;
                        color:#999; font-family:'Montserrat', sans-serif; font-size:0.65rem;">
                LOGO
            </div>
            """,
            unsafe_allow_html=True,
        )

st.markdown(
    """
This dashboard combines **Inc. 5000 high-growth private-company data** with
**Aswath Damodaran / NYU Stern US industry beta data**.

The key chart asks the same strategic question as the previous [efield Solutions analysis](https://racc-it.com/us-equity-market/) for years 2015 to 2017 :
**Are high-growth private companies concentrated in industries with above-market systematic risk?**
"""
)

with st.sidebar:
    st.header("Data")
    selected_year = st.selectbox("Year", SUPPORTED_YEARS, index=len(SUPPORTED_YEARS) - 1)
    min_companies = st.number_input("Minimum companies per industry", min_value=1, value=5)

try:
    inc = load_local_inc5000(selected_year)
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

if inc.empty:
    st.info("No Inc. 5000 data found for this year. The app intentionally does not fabricate missing data.")
    st.stop()

selected_years = [selected_year]
inc = inc[inc["year"].isin(selected_years)].copy()
inc["damodaran_industry"] = inc["industry"].map(classify_industry)

try:
    beta = load_local_beta(selected_year)
except FileNotFoundError:
    try:
        beta = fetch_beta(selected_year)
    except Exception as e:
        st.error(
            f"Could not load Damodaran beta data for {selected_year}. "
            f"Add data/betas_{selected_year}.xls to the repo, or check the network. ({e})"
        )
        st.stop()

merged = inc.merge(beta, on="damodaran_industry", how="left")
merged["high_risk"] = merged["beta"] > 1.0

summary = (
    merged.dropna(subset=["beta", "growth_pct"])
    .groupby(["year", "industry", "damodaran_industry"], as_index=False)
    .agg(
        avg_growth_pct=("growth_pct", "mean"),
        median_growth_pct=("growth_pct", "median"),
        companies=("company", "nunique"),
        total_revenue=("revenue_usd", "sum"),
        beta=("beta", "first"),
    )
)
summary = summary[summary["companies"] >= min_companies].copy()
summary["high_risk"] = summary["beta"] > 1.0

st.subheader("1. Risk × growth profile")
year_for_chart = selected_year
chart = summary[summary["year"] == year_for_chart].copy()

if chart.empty:
    st.warning("No matched industries for this year. Check the Inc. industry labels and mapping.")
else:
    import plotly.express as px

    fig = px.scatter(
        chart,
        x="beta",
        y="avg_growth_pct",
        size="companies",
        hover_name="industry",
        hover_data={
            "damodaran_industry": True,
            "beta": ":.2f",
            "avg_growth_pct": ":.1f",
            "median_growth_pct": ":.1f",
            "companies": True,
        },
        labels={
            "beta": "Damodaran levered beta",
            "avg_growth_pct": "Average Inc. 5000 3 year growth (%)",
            "companies": "Companies",
        },
        title=f"{year_for_chart}: Inc. 5000 growth vs. industry risk",
    )
    fig.update_traces(marker=dict(color="#368184"))
    
    fig.add_vline(
        x=1.0,
        line=dict(color="green", width=2, dash="dot"),
        annotation_text="Beta = 1",
    )

    # Average growth across the mapped industries shown in THIS chart/year
    # (previously referenced an undefined `trend` variable defined later in the file).
    average_growth = pd.to_numeric(chart["avg_growth_pct"], errors="coerce")
    avg_growth_value = average_growth.mean()

    if pd.notna(avg_growth_value):
        fig.add_hline(
            y=float(avg_growth_value),
            line=dict(color="green", width=2, dash="dot"),
            annotation_text=f"Avg growth: {avg_growth_value:.1f}%",
            annotation_position="top left",
        )

    fig.update_layout(height=620)

    st.plotly_chart(fig, use_container_width=True)

st.subheader("2. Average growth and number of companies by state")

state_data = inc[inc["year"] == year_for_chart].copy()
state_data["state_abbr"] = state_data["state"].map(to_state_abbr)
state_data = state_data.dropna(subset=["state_abbr", "growth_pct"])

state_summary = (
    state_data.groupby("state_abbr", as_index=False)
    .agg(
        avg_growth_pct=("growth_pct", "mean"),
        companies=("company", "nunique"),
    )
)
state_summary = state_summary[state_summary["companies"] >= min_companies].copy()

if state_summary.empty:
    st.warning(
        "No states met the minimum-companies threshold for this year. "
        "Try lowering **Minimum companies per industry** in the sidebar."
    )
else:
    import plotly.express as px

    fig2 = px.scatter(
        state_summary,
        x="avg_growth_pct",
        y="companies",
        size="companies",
        color="avg_growth_pct",
        color_continuous_scale=["#3E9C82", "#CFE08A"],  # teal-green -> yellow-green
        text="state_abbr",
        hover_name="state_abbr",
        hover_data={
            "avg_growth_pct": ":.1f",
            "companies": True,
        },
        labels={
            "avg_growth_pct": "Average Inc. 5000 growth (%)",
            "companies": "Number of companies",
        },
        title=f"{year_for_chart}: Average growth and number of companies by state",
        size_max=60,
    )
    fig2.update_traces(textposition="middle center", textfont=dict(color="white", size=10))
    fig2.update_layout(height=620)

    st.plotly_chart(fig2, use_container_width=True)

st.subheader("3. High-risk industries")
risk_table = summary[summary["high_risk"]].sort_values(
    ["year", "avg_growth_pct"], ascending=[False, False]
)
st.dataframe(
    risk_table[
        [
            "year",
            "industry",
            "damodaran_industry",
            "beta",
            "avg_growth_pct",
            "median_growth_pct",
            "companies",
        ]
    ],
    use_container_width=True,
    hide_index=True,
)

st.subheader("Methodology and limitations")
st.markdown(
    """
- **Inc. 5000** is a ranking of privately held, for-profit, US-based, independent companies. Inc. ranks companies by percentage revenue growth over a multi-year window; the window changes with the list year.
- **Damodaran beta** is a public-company industry measure. It is not a beta calculated for the private Inc. 5000 companies themselves.
- **Beta > 1.0** is used here as the definition of **above-market systematic risk**, matching the conceptual split used by the article published on [RACC-IT](https://racc-it.com/).
- Inc. and Damodaran use different industry taxonomies. The mapping in this prototype is therefore an **analytical crosswalk** and is displayed in section 3.High risk industries table.
- Averages are calculated from the Inc. 5000 companies available in the selected list. They should not be interpreted as investment returns or as a representative sample of all private US companies.

"""
)

with st.expander("📈 Beyond the free view"):
    st.markdown("""
    **Currently available:** 2019 and 2023 Inc. 5000 cohorts mapped to Damodaran industry betas, free to explore.

    **On the roadmap:**
    - **Additional historical years** (2015–2022) — available as single-year reports (**$69/year**) 
    - **Full industry coverage** — closing current gaps in Legal, Automotive, Consumer Products, and E-Commerce
    - **Custom crosswalk requests** — need an industry mapped that isn't here? Bespoke crosswalks starting at **$500**

    Interested in early access or a custom report? [Get in touch](contact@efield.eu).
    """)

st.caption(
    "Sources: Inc. 5000 annual lists/methodology; Aswath Damodaran, NYU Stern industry beta archives."
)
