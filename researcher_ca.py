import io
import json
import math
import os
import re
import warnings

import yfinance as yf
from anthropic import Anthropic
from dotenv import load_dotenv
from tavily import TavilyClient

load_dotenv()
warnings.filterwarnings("ignore")


def _get_secret(key: str) -> str:
    try:
        import streamlit as st
        return st.secrets[key]
    except Exception:
        return os.getenv(key, "")


tavily = TavilyClient(api_key=_get_secret("TAVILY_API_KEY"))
claude = Anthropic(api_key=_get_secret("ANTHROPIC_API_KEY"))


def _fmt(val):
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f):
            return None
        return round(f / 1_000_000, 1)
    except Exception:
        return None


def fetch_financials(ticker: str) -> dict:
    ticker_bk = f"{ticker.upper()}.BK"
    t = yf.Ticker(ticker_bk)
    info = t.info or {}

    if not info.get("longName") and not info.get("shortName"):
        return {"error": f"No financial data found for {ticker_bk}"}

    fin = t.financials
    bs = t.balance_sheet

    years, revenue, ebitda, net_income, operating_income = [], [], [], [], []
    if fin is not None and not fin.empty:
        cols = list(fin.columns[:4])
        years = [str(c.year) for c in cols]
        revenue         = [_fmt(fin.loc["Total Revenue", c])      if "Total Revenue"      in fin.index else None for c in cols]
        ebitda          = [_fmt(fin.loc["EBITDA", c])             if "EBITDA"             in fin.index else None for c in cols]
        net_income      = [_fmt(fin.loc["Net Income", c])         if "Net Income"         in fin.index else None for c in cols]
        operating_income= [_fmt(fin.loc["Operating Income", c])   if "Operating Income"   in fin.index else None for c in cols]

    total_debt, total_assets, total_equity = [], [], []
    if bs is not None and not bs.empty:
        cols_bs = list(bs.columns[:4])
        total_debt   = [_fmt(bs.loc["Total Debt", c])                            if "Total Debt"                            in bs.index else None for c in cols_bs]
        total_assets = [_fmt(bs.loc["Total Assets", c])                          if "Total Assets"                          in bs.index else None for c in cols_bs]
        total_equity = [_fmt(bs.loc["Total Equity Gross Minority Interest", c])  if "Total Equity Gross Minority Interest"  in bs.index else None for c in cols_bs]

    roe = info.get("returnOnEquity")
    ebitda_margin = info.get("ebitdaMargins")
    profit_margin = info.get("profitMargins")

    return {
        "ticker_bk": ticker_bk,
        "years": years,
        "income": {
            "revenue":          revenue,
            "ebitda":           ebitda,
            "net_income":       net_income,
            "operating_income": operating_income,
        },
        "balance": {
            "total_debt":   total_debt,
            "total_assets": total_assets,
            "total_equity": total_equity,
        },
        "ratios": {
            "debt_to_equity": info.get("debtToEquity"),
            "current_ratio":  info.get("currentRatio"),
            "roe":            round(roe * 100, 1)          if roe           else None,
            "ebitda_margin":  round(ebitda_margin * 100, 1) if ebitda_margin else None,
            "profit_margin":  round(profit_margin * 100, 1) if profit_margin else None,
            "free_cash_flow": _fmt(info.get("freeCashflow")),
        },
        "source": f"Yahoo Finance / SET — {ticker_bk}",
    }


def fetch_company_research_ca(company: dict) -> str:
    name = company.get("english_name") or company.get("thai_name", "")
    ticker = company.get("ticker", "")
    search_name = f"{name} {ticker}".strip()

    results = []
    queries = [
        f"{search_name} company profile business overview Thailand",
        f"{search_name} news risk outlook 2024 2025",
        f"{search_name} industry Thailand market position",
    ]
    for q in queries:
        try:
            r = tavily.search(query=q, search_depth="basic", max_results=4, days=365)
            for item in r["results"]:
                results.append(f"[{item.get('title', '')}]\n{item['content']}\nURL: {item['url']}")
        except Exception:
            pass

    return "\n---\n".join(results[:10])


def draft_credit_memo(company: dict, financials: dict, research: str, facility: dict) -> dict:
    name = company.get("english_name") or company.get("thai_name", "")
    years = financials.get("years", [])
    income = financials.get("income", {})
    balance = financials.get("balance", {})
    ratios = financials.get("ratios", {})

    fin_lines = f"Financial Summary (M THB) | Years: {', '.join(years)}\n"
    fin_lines += f"Revenue:          {income.get('revenue')}\n"
    fin_lines += f"EBITDA:           {income.get('ebitda')}\n"
    fin_lines += f"Net Income:       {income.get('net_income')}\n"
    fin_lines += f"Total Debt:       {balance.get('total_debt')}\n"
    fin_lines += f"Total Assets:     {balance.get('total_assets')}\n"
    fin_lines += f"Total Equity:     {balance.get('total_equity')}\n"
    fin_lines += f"D/E: {ratios.get('debt_to_equity')}%  Current Ratio: {ratios.get('current_ratio')}x  ROE: {ratios.get('roe')}%  EBITDA Margin: {ratios.get('ebitda_margin')}%  FCF: {ratios.get('free_cash_flow')} M THB\n"

    amount = facility.get("amount", 0)
    collateral_val = facility.get("collateral_value", 0)
    ltv = f"{round(amount / collateral_val * 100, 1)}%" if collateral_val else "N/A"

    facility_lines = (
        f"Facility Type: {facility.get('facility_type')}\n"
        f"Amount: {amount / 1_000_000:,.0f} M THB\n"
        f"Tenor: {facility.get('tenor')} years\n"
        f"Collateral: {facility.get('collateral_type')} | Value: {collateral_val / 1_000_000:,.0f} M THB | LTV: {ltv}\n"
    )

    prompt = f"""You are a Thai bank credit analyst. Draft a credit application memo.

Company: {name} | Industry: {company.get("industry", "")}

{facility_lines}
{fin_lines}

Research:
{research[:3000]}

Draft these 4 sections in professional banking English. Return JSON only (no markdown):
{{
  "borrower_profile": "2-3 paragraphs: business description, ownership, market position, recent developments",
  "repayment_analysis": "primary repayment source, cash flow adequacy vs debt service, stress test at 20% revenue decline",
  "key_risks": [
    {{"risk": "...", "mitigant": "..."}},
    {{"risk": "...", "mitigant": "..."}},
    {{"risk": "...", "mitigant": "..."}}
  ],
  "recommendation": "Approve / Approve with conditions / Decline — with clear rationale and proposed conditions if any"
}}"""

    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
    return {"error": text}


def generate_ca_word_doc(company: dict, financials: dict, memo: dict, facility: dict) -> io.BytesIO:
    from docx import Document

    doc = Document()
    name = company.get("english_name") or company.get("thai_name", "")
    ticker = company.get("ticker", "—")

    doc.add_heading("CREDIT APPLICATION", 0)
    doc.add_heading(f"{name} ({ticker})", 1)

    # Facility summary
    doc.add_heading("Facility Summary", level=2)
    amount = facility.get("amount", 0)
    collateral_val = facility.get("collateral_value", 0)
    ltv = f"{round(amount / collateral_val * 100, 1)}%" if collateral_val else "N/A"

    fs = doc.add_table(rows=3, cols=4)
    fs.style = "Table Grid"
    fs.rows[0].cells[0].text = "Facility Type";  fs.rows[0].cells[1].text = facility.get("facility_type", "—")
    fs.rows[0].cells[2].text = "Amount (THB)";   fs.rows[0].cells[3].text = f"{amount:,.0f}"
    fs.rows[1].cells[0].text = "Tenor";          fs.rows[1].cells[1].text = f"{facility.get('tenor', '—')} years"
    fs.rows[1].cells[2].text = "Industry";       fs.rows[1].cells[3].text = company.get("industry", "—")
    fs.rows[2].cells[0].text = "Collateral";     fs.rows[2].cells[1].text = facility.get("collateral_type", "—")
    fs.rows[2].cells[2].text = "LTV";            fs.rows[2].cells[3].text = ltv
    doc.add_paragraph()

    # 1. Borrower Profile
    doc.add_heading("1. Borrower Profile", level=1)
    doc.add_paragraph(memo.get("borrower_profile", "—"))
    doc.add_paragraph()

    # 2. Financial Summary
    doc.add_heading("2. Financial Summary", level=1)
    years = financials.get("years", [])
    income = financials.get("income", {})
    balance = financials.get("balance", {})
    ratios = financials.get("ratios", {})

    if years:
        ft = doc.add_table(rows=1, cols=len(years) + 1)
        ft.style = "Table Grid"
        hdr = ft.rows[0].cells
        hdr[0].text = "M THB"
        for i, y in enumerate(years):
            hdr[i + 1].text = y

        for label, vals in [
            ("Revenue",       income.get("revenue", [])),
            ("EBITDA",        income.get("ebitda", [])),
            ("Net Income",    income.get("net_income", [])),
            ("Total Debt",    balance.get("total_debt", [])),
            ("Total Assets",  balance.get("total_assets", [])),
            ("Total Equity",  balance.get("total_equity", [])),
        ]:
            row = ft.add_row().cells
            row[0].text = label
            for i, v in enumerate(vals[: len(years)]):
                row[i + 1].text = f"{v:,.1f}" if v is not None else "—"

    doc.add_paragraph()
    rp = doc.add_paragraph()
    rp.add_run("Key Ratios: ").bold = True
    rp.add_run(
        f"D/E {ratios.get('debt_to_equity', '—')}%  |  "
        f"Current Ratio {ratios.get('current_ratio', '—')}x  |  "
        f"ROE {ratios.get('roe', '—')}%  |  "
        f"EBITDA Margin {ratios.get('ebitda_margin', '—')}%  |  "
        f"FCF {ratios.get('free_cash_flow', '—')} M THB"
    )
    sp = doc.add_paragraph()
    sp.add_run(f"Source: {financials.get('source', 'Yahoo Finance / SET')}").italic = True
    doc.add_paragraph()

    # 3. Facility Details
    doc.add_heading("3. Facility Details", level=1)
    purpose = "Working Capital" if "Working" in facility.get("facility_type", "") else "Capital Expenditure / General Corporate"
    for label, val in [
        ("Facility Type",     facility.get("facility_type", "—")),
        ("Requested Amount",  f"THB {amount / 1_000_000:,.0f}M"),
        ("Tenor",             f"{facility.get('tenor', '—')} years"),
        ("Purpose",           purpose),
        ("Collateral Type",   facility.get("collateral_type", "—")),
        ("Collateral Value",  f"THB {collateral_val / 1_000_000:,.0f}M"),
        ("LTV",               ltv),
    ]:
        p = doc.add_paragraph()
        p.add_run(f"{label}: ").bold = True
        p.add_run(val)
    doc.add_paragraph()

    # 4. Repayment Analysis
    doc.add_heading("4. Repayment Analysis", level=1)
    doc.add_paragraph(memo.get("repayment_analysis", "—"))
    doc.add_paragraph()

    # 5. Key Risks
    doc.add_heading("5. Key Risks", level=1)
    risks = memo.get("key_risks", [])
    if risks:
        rt = doc.add_table(rows=1, cols=3)
        rt.style = "Table Grid"
        rt.rows[0].cells[0].text = "#"
        rt.rows[0].cells[1].text = "Risk"
        rt.rows[0].cells[2].text = "Mitigant"
        for i, r in enumerate(risks, 1):
            row = rt.add_row().cells
            row[0].text = str(i)
            row[1].text = r.get("risk", "")
            row[2].text = r.get("mitigant", "")
    doc.add_paragraph()

    # 6. RM Recommendation
    doc.add_heading("6. RM Recommendation", level=1)
    doc.add_paragraph(memo.get("recommendation", "—"))

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf
