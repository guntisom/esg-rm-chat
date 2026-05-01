import os
import json
import re
from dotenv import load_dotenv
from tavily import TavilyClient
from anthropic import Anthropic

load_dotenv()

tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
claude = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def resolve_company(query: str) -> dict:
    searches = [
        f"{query} SET Thailand stock exchange company",
        f"{query} Thailand company annual report",
        f'"{query}" Thailand บริษัท',
    ]
    all_snippets = []
    for q in searches:
        try:
            result = tavily.search(query=q, search_depth="basic", max_results=3)
            all_snippets += [r["content"] for r in result["results"]]
        except Exception:
            pass

    snippets = "\n---\n".join(all_snippets[:8])
    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": f"""The user typed: "{query}"

This is likely a Thai company name (Thai or English), a SET ticker, or an abbreviation.
For example: "ratch" = RATCH Group, "ptt" = PTT, "scg" = SCG, "gulf" = Gulf Energy.

Search results:
{snippets}

Using your knowledge of Thai listed and non-listed companies AND the search results,
identify the company and return JSON only (no markdown, no explanation):
{{
  "thai_name": "...",
  "english_name": "...",
  "ticker": "...",
  "listed": true or false,
  "industry": "...",
  "website": "..."
}}

If you truly cannot identify any company matching the query, return:
{{"error": "Company not found"}}"""
        }]
    )
    text = response.content[0].text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
        return {"error": "Could not resolve company", "raw": text}


def fetch_company_research(company: dict) -> dict:
    name = company.get("english_name") or company.get("thai_name", "")
    ticker = company.get("ticker", "")
    search_name = f"{name} {ticker}".strip()

    def search(query, depth="advanced", max_results=5, **kwargs):
        r = tavily.search(query=query, search_depth=depth, max_results=max_results, **kwargs)
        return [{"content": x["content"], "url": x["url"], "title": x.get("title", "")}
                for x in r["results"]]

    sources = {}
    sources["annual_report"] = search(f"{search_name} annual report 2023 2024")
    sources["sustainability_report"] = search(f"{search_name} sustainability report ESG SD report 2023 2024")
    sources["investor_presentation"] = search(f"{search_name} investor presentation analyst day 2024 2025", depth="basic", max_results=3)
    sources["corporate_announcement"] = search(
        f"{search_name} corporate announcement AGM annual general meeting board resolution 2024 2025",
        depth="advanced", max_results=5,
    )
    sources["news"] = search(
        f"{search_name} ESG green sustainability carbon renewable 2024 2025",
        max_results=8,
        include_domains=["bangkokpost.com", "thestandard.co", "krungthepturakij.com",
                         "set.or.th", "reuters.com", "bloomberg.com", "thansettakij.com",
                         "nationthailand.com", "prachachat.net"],
    )
    return sources


def _call_claude(messages: list, max_tokens: int = 4096) -> str:
    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        messages=messages,
    )
    text = response.content[0].text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text


def _parse_json(text: str):
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r'\[.*\]|\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
    return None


def analyze_esg_opportunities(company: dict, research: dict) -> dict:
    name = company.get("english_name") or company.get("thai_name", "")

    all_content = ""
    idx = 1
    for section, items in research.items():
        for item in items:
            all_content += f"\n[{idx}] ({section}) {item['title']}\nURL: {item['url']}\n{item['content']}\n"
            idx += 1

    context = f"Company: {name}\nProfile: {json.dumps(company)}\n\nSource documents:\n{all_content}"

    # ── Call 1: Opportunities with size calculation ──────────────────────────
    opps_text = _call_claude([{
        "role": "user",
        "content": f"""{context}

You are an ESG banking advisor for a Thai bank RM. Identify the top 3–5 ESG banking product opportunities for this company.

Return a JSON array only (no markdown, no extra text):
[
  {{
    "product": "product name e.g. Green Loan, SLL, Transition Bond, Green Bond",
    "rationale": "2–3 sentences on why this product fits, referencing specific company facts from the sources",
    "size_thb": "deal size range e.g. 5,000–10,000M THB",
    "size_calculation": "step-by-step calculation showing how you arrived at the size: cite the financial metric used (revenue/capex/total debt/EBITDA), the ratio applied, and the bank's expected share. Example: Total capex plan THB 20B × 70% debt portion = THB 14B debt need × bank share 30% = THB 4,200M",
    "assumptions": ["assumption 1", "assumption 2", "assumption 3"]
  }}
]

Rank by fit. Ground size estimates in actual financials from the sources (revenue, capex, total debt, EBITDA). Be specific."""
    }])

    opportunities = _parse_json(opps_text) or []

    # ── Call 2: Signals filtered to identified opportunities ─────────────────
    opp_names = ", ".join(o.get("product", "") for o in opportunities) if opportunities else "ESG banking products"

    signals_text = _call_claude([{
        "role": "user",
        "content": f"""{context}

You are an ESG banking advisor. The following ESG banking opportunities have been identified for {name}:
{opp_names}

Extract 5–7 pieces of evidence from the sources above that DIRECTLY support one or more of these specific opportunities.
Prioritise: corporate announcements, AGM resolutions, board decisions, financial targets, capex plans, and ESG commitments with hard numbers.
Only include findings that are highly relevant to the identified opportunities above — skip generic ESG statements.

Return a JSON array only (no markdown, no extra text):
[
  {{
    "date": "YYYY-MM-DD or YYYY-MM or YYYY (extract from source; use null if not found)",
    "finding": "specific fact, number, commitment, or announcement from the source",
    "related_opportunity": "which opportunity above this supports e.g. Green Loan",
    "source_title": "title of the source document or article",
    "source_url": "URL from the source list"
  }}
]"""
    }])

    signals = _parse_json(signals_text) or []

    if not signals and not opportunities:
        return {"error": opps_text}

    return {"esg_signals": signals, "opportunities": opportunities}
