"""
Tally Prime MCP Server
──────────────────────
Exposes Tally accounting data as MCP tools for LLM consumption.

Tested against: Tally Prime (Educational Mode)
Company: loaded from .env
No TDL used anywhere — only built-in Collection and Report exports.
"""

import requests
import re
import xml.etree.ElementTree as ET
import os
from dotenv import load_dotenv
from collections import Counter, defaultdict
from mcp.server.fastmcp import FastMCP
import snapshot

load_dotenv()
TALLY_URL = os.getenv("TALLY_URL", "http://localhost:9000")
COMPANY = os.getenv("TALLY_COMPANY", "Test BI Corp")

mcp = FastMCP("TallyBI")

# ─────────────────────────────────────────────────
#  Live-or-Cache Wrapper
# ─────────────────────────────────────────────────

_tally_is_alive = None  # Track connection state

def _live_or_cache(cache_key: str, fetch_fn, format_fn) -> str:
    """
    Try live Tally query. If it fails, return cached snapshot.
    On success, update the snapshot.
    
    Args:
        cache_key:  unique name for this data (e.g. "ledgers", "trial_balance")
        fetch_fn:   function that fetches raw XML from Tally
        format_fn:  function that parses XML and returns formatted string
    """
    global _tally_is_alive
    
    # Try live
    try:
        raw = fetch_fn()
        
        # Check if Tally returned an error
        if "<ERROR>" in raw:
            raise ConnectionError(raw)
        
        result = format_fn(raw)
        
        # Success — save snapshot and return
        snapshot.save(cache_key, result)
        _tally_is_alive = True
        return result
        
    except Exception as e:
        _tally_is_alive = False
        
        # Try cache
        cached = snapshot.load(cache_key)
        if cached:
            age = snapshot.age_str(cache_key)
            return (
                f"⚠️ Tally is offline. Showing cached data from {age}:\n"
                f"─────────────────────────────\n"
                f"{cached['data']}"
            )
        else:
            return f"❌ Tally is offline and no cached data available for this query."

# ─────────────────────────────────────────────────────────
#  XML Sanitization
#  Tally embeds illegal XML chars like &#4; (ASCII control)
#  XML 1.0 allows: #x9 | #xA | #xD | [#x20-#xD7FF]
# ─────────────────────────────────────────────────────────

_ILLEGAL_XML_CHARS = re.compile(
    r'&#([0-8]|1[0-1]|1[4-9]|2[0-9]|3[01]);'
)
_ILLEGAL_RAW_CHARS = re.compile(
    r'[\x00-\x08\x0b\x0c\x0e-\x1f]'
)

def _clean_xml(raw: str) -> str:
    raw = _ILLEGAL_XML_CHARS.sub('', raw)
    raw = _ILLEGAL_RAW_CHARS.sub('', raw)
    return raw

def _parse_xml(raw: str) -> ET.Element:
    return ET.fromstring(_clean_xml(raw))


# ─────────────────────────────────────────────────────────
#  Tally HTTP Communication
#  Two request shapes: Collection and Report
#  Both use streaming to handle large responses (151KB+)
# ─────────────────────────────────────────────────────────

def _tally_request(xml_payload: str) -> str:
    """Send XML to Tally, return full response as string.
    Uses streaming to handle large responses."""
    try:
        with requests.post(
            TALLY_URL,
            data=xml_payload.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8"},
            timeout=30,
            stream=True
        ) as resp:
            resp.raise_for_status()
            chunks = []
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    chunks.append(chunk)
            return b"".join(chunks).decode("utf-8", errors="ignore")
    except requests.Timeout:
        return "<ENVELOPE><ERROR>Tally request timed out</ERROR></ENVELOPE>"
    except requests.ConnectionError:
        return "<ENVELOPE><ERROR>Cannot connect to Tally at " + TALLY_URL + "</ERROR></ENVELOPE>"
    except Exception as e:
        return f"<ENVELOPE><ERROR>{e}</ERROR></ENVELOPE>"


def tally_collection(collection_type: str) -> str:
    """Export a built-in Tally collection: Ledger, Group, StockItem"""
    return _tally_request(f"""<ENVELOPE>
    <HEADER>
        <VERSION>1</VERSION>
        <TALLYREQUEST>Export</TALLYREQUEST>
        <TYPE>Collection</TYPE>
        <ID>{collection_type}</ID>
    </HEADER>
    <BODY>
        <DESC>
            <STATICVARIABLES>
                <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
                <SVCURRENTCOMPANY>{COMPANY}</SVCURRENTCOMPANY>
            </STATICVARIABLES>
        </DESC>
    </BODY>
</ENVELOPE>""")


def tally_report(report_name: str, from_date: str = "", to_date: str = "") -> str:
    """Export a standard Tally report: Trial Balance, Day Book, Profit and Loss, Balance Sheet"""
    date_vars = ""
    if from_date and to_date:
        date_vars = (
            f"<SVFROMDATE>{from_date}</SVFROMDATE>"
            f"<SVTODATE>{to_date}</SVTODATE>"
        )
    return _tally_request(f"""<ENVELOPE>
    <HEADER>
        <VERSION>1</VERSION>
        <TALLYREQUEST>Export</TALLYREQUEST>
        <TYPE>Data</TYPE>
        <ID>{report_name}</ID>
    </HEADER>
    <BODY>
        <DESC>
            <STATICVARIABLES>
                <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
                <SVCURRENTCOMPANY>{COMPANY}</SVCURRENTCOMPANY>
                {date_vars}
            </STATICVARIABLES>
        </DESC>
    </BODY>
</ENVELOPE>""")


# ─────────────────────────────────────────────────────────
#  Parsers
#  Each matches the exact XML structure from YOUR Tally
# ─────────────────────────────────────────────────────────

def _parse_ledgers(raw: str) -> list[dict]:
    """
    Source: Collection:Ledger
    Structure: <LEDGER NAME="Cash" RESERVEDNAME="">
                 <PARENT TYPE="String">Cash-in-Hand</PARENT>
                 <CLOSINGBALANCE TYPE="Amount">...</CLOSINGBALANCE>
    """
    root = _parse_xml(raw)
    results = []
    for el in root.iter("LEDGER"):
        name = el.get("NAME")
        if not name:
            continue
        parent_el = el.find("PARENT")
        closing_el = el.find("CLOSINGBALANCE")
        results.append({
            "name": name,
            "group": parent_el.text.strip() if parent_el is not None and parent_el.text else "",
            "balance": float(closing_el.text.strip()) if closing_el is not None and closing_el.text and closing_el.text.strip() else 0.0,
        })
    return results


def _parse_groups(raw: str) -> list[str]:
    """
    Source: Collection:Group
    Structure: <GROUP NAME="Bank Accounts" RESERVEDNAME="...">
    """
    root = _parse_xml(raw)
    return [el.get("NAME") for el in root.iter("GROUP") if el.get("NAME")]


def _parse_stock_items(raw: str) -> list[str]:
    """
    Source: Collection:StockItem
    Structure: <STOCKITEM NAME="Item_1" RESERVEDNAME="">
    """
    root = _parse_xml(raw)
    return [el.get("NAME") for el in root.iter("STOCKITEM") if el.get("NAME")]


def _parse_display_report(raw: str, amount_tag: str) -> list[dict]:
    """
    Generic parser for Trial Balance, P&L, Balance Sheet.
    All use <DSPDISPNAME> for account names, paired with amount tags.

    Trial Balance: DSPCLDRAMTA / DSPCLCRAMTA
    P&L:           BSMAINAMT / PLSUBAMT
    Balance Sheet:  BSMAINAMT / BSSUBAMT
    """
    root = _parse_xml(raw)
    names = [el.text for el in root.iter("DSPDISPNAME") if el.text]

    if amount_tag == "trial_balance":
        dr_els = list(root.iter("DSPCLDRAMTA"))
        cr_els = list(root.iter("DSPCLCRAMTA"))
        rows = []
        for i, name in enumerate(names):
            dr = dr_els[i].text.strip() if i < len(dr_els) and dr_els[i].text and dr_els[i].text.strip() else ""
            cr = cr_els[i].text.strip() if i < len(cr_els) and cr_els[i].text and cr_els[i].text.strip() else ""
            rows.append({"name": name, "debit": dr, "credit": cr})
        return rows

    elif amount_tag == "pnl":
        main_els = list(root.iter("BSMAINAMT"))
        sub_els = list(root.iter("PLSUBAMT"))
        rows = []
        for i, name in enumerate(names):
            main = main_els[i].text.strip() if i < len(main_els) and main_els[i].text and main_els[i].text.strip() else ""
            sub = sub_els[i].text.strip() if i < len(sub_els) and sub_els[i].text and sub_els[i].text.strip() else ""
            rows.append({"name": name, "amount": main or sub or "0"})
        return rows

    elif amount_tag == "balance_sheet":
        main_els = list(root.iter("BSMAINAMT"))
        rows = []
        for i, name in enumerate(names):
            amt = main_els[i].text.strip() if i < len(main_els) and main_els[i].text and main_els[i].text.strip() else "0"
            rows.append({"name": name, "amount": amt})
        return rows

    return []


def _parse_vouchers(raw: str) -> list[dict]:
    """
    Source: Report:Day Book
    Structure:
      <VOUCHER VCHTYPE="Sales">
        <DATE>20250701</DATE>
        <PARTYLEDGERNAME>Debtor_2</PARTYLEDGERNAME>
        <VOUCHERNUMBER>1</VOUCHERNUMBER>
        <NARRATION>...</NARRATION>
        ...
        <ALLLEDGERENTRIES.LIST>
          <LEDGERNAME>Debtor_2</LEDGERNAME>
          <ISPARTYLEDGER>Yes</ISPARTYLEDGER>
          <AMOUNT>-200.00</AMOUNT>        ← this is the voucher total
        </ALLLEDGERENTRIES.LIST>
    """
    root = _parse_xml(raw)
    results = []
    for v in root.iter("VOUCHER"):
        # Extract amount from the party's ledger entry
        amount = _extract_voucher_amount(v)

        results.append({
            "type": v.get("VCHTYPE", "?"),
            "date": v.findtext("DATE", ""),
            "party": v.findtext("PARTYLEDGERNAME", ""),
            "amount": amount,
            "narration": v.findtext("NARRATION", ""),
            "number": v.findtext("VOUCHERNUMBER", ""),
        })
    return results


def _extract_voucher_amount(voucher_el: ET.Element) -> float:
    """Extract the total amount from a voucher element.
    Tries multiple locations in order of reliability."""

    # 1. ALLLEDGERENTRIES.LIST where ISPARTYLEDGER = Yes
    for entry in voucher_el.iter("ALLLEDGERENTRIES.LIST"):
        if entry.findtext("ISPARTYLEDGER", "No") == "Yes":
            amt = entry.findtext("AMOUNT", "")
            if amt:
                try:
                    return abs(float(amt))
                except ValueError:
                    pass

    # 2. LEDGERENTRIES.LIST where ISPARTYLEDGER = Yes
    for entry in voucher_el.iter("LEDGERENTRIES.LIST"):
        if entry.findtext("ISPARTYLEDGER", "No") == "Yes":
            amt = entry.findtext("AMOUNT", "")
            if amt:
                try:
                    return abs(float(amt))
                except ValueError:
                    pass

    # 3. Sum from INVENTORYENTRIES.LIST
    inv_total = 0.0
    for inv in voucher_el.iter("INVENTORYENTRIES.LIST"):
        amt = inv.findtext("AMOUNT", "")
        if amt:
            try:
                inv_total += abs(float(amt))
            except ValueError:
                pass
    if inv_total > 0:
        return inv_total

    # 4. First non-zero AMOUNT anywhere in the voucher
    for amt_el in voucher_el.iter("AMOUNT"):
        if amt_el.text and amt_el.text.strip():
            try:
                val = abs(float(amt_el.text))
                if val > 0:
                    return val
            except ValueError:
                pass

    return 0.0


# ─────────────────────────────────────────────────────────
#  Formatting Helpers
# ─────────────────────────────────────────────────────────

def _fmt_currency(value: float) -> str:
    """Format as Indian currency: ₹1,23,456.00"""
    return f"₹{abs(value):,.2f}"


# ─────────────────────────────────────────────────────────
#  MCP Tools — 11 tools
# ─────────────────────────────────────────────────────────

@mcp.tool()
def get_all_ledgers() -> str:
    """Get all ledger accounts with their group and closing balance."""
    
    def fetch():
        return tally_collection("Ledger")
    
    def format(raw):
        ledgers = _parse_ledgers(raw)
        if not ledgers:
            return "No ledgers found."
        lines = []
        for l in ledgers:
            bal_str = _fmt_currency(l["balance"]) if l["balance"] != 0 else "0"
            lines.append(f"{l['name']} | Group: {l['group']} | Balance: {bal_str}")
        return "\n".join(lines)
    
    return _live_or_cache("ledgers", fetch, format)


@mcp.tool()
def get_account_groups() -> str:
    """Get all account groups."""
    
    def fetch():
        return tally_collection("Group")
    
    def format(raw):
        groups = _parse_groups(raw)
        return "\n".join(groups) if groups else "No groups found."
    
    return _live_or_cache("groups", fetch, format)


@mcp.tool()
def get_stock_items() -> str:
    """Get all inventory/stock items."""
    
    def fetch():
        return tally_collection("StockItem")
    
    def format(raw):
        items = _parse_stock_items(raw)
        return "\n".join(items) if items else "No stock items found."
    
    return _live_or_cache("stock_items", fetch, format)


@mcp.tool()
def get_trial_balance() -> str:
    """Get trial balance with debit/credit for all account groups."""
    
    def fetch():
        return tally_report("Trial Balance")
    
    def format(raw):
        rows = _parse_display_report(raw, "trial_balance")
        if not rows:
            return "Empty trial balance."
        lines = []
        for r in rows:
            parts = [r["name"] + ":"]
            if r["debit"]:
                parts.append(f"Dr {r['debit']}")
            if r["credit"]:
                parts.append(f"Cr {r['credit']}")
            lines.append(" ".join(parts))
        return "\n".join(lines)
    
    return _live_or_cache("trial_balance", fetch, format)


@mcp.tool()
def get_profit_and_loss() -> str:
    """Get P&L: sales, costs, expenses, net profit."""
    
    def fetch():
        return tally_report("Profit and Loss")
    
    def format(raw):
        rows = _parse_display_report(raw, "pnl")
        if not rows:
            return "Empty P&L."
        lines = []
        for r in rows:
            amt = r["amount"] if r["amount"] != "0" else "-"
            lines.append(f"{r['name']}: {amt}")
        return "\n".join(lines)
    
    return _live_or_cache("pnl", fetch, format)


@mcp.tool()
def get_balance_sheet() -> str:
    """Get Balance Sheet: capital, loans, liabilities, assets."""
    
    def fetch():
        return tally_report("Balance Sheet")
    
    def format(raw):
        rows = _parse_display_report(raw, "balance_sheet")
        if not rows:
            return "Empty balance sheet."
        lines = []
        for r in rows:
            amt = r["amount"] if r["amount"] != "0" else "-"
            lines.append(f"{r['name']}: {amt}")
        return "\n".join(lines)
    
    return _live_or_cache("balance_sheet", fetch, format)


@mcp.tool()
def get_sundry_debtors() -> str:
    """Get customers who owe us money (Sundry Debtors), sorted by amount."""
    
    def fetch():
        return tally_collection("Ledger")
    
    def format(raw):
        ledgers = _parse_ledgers(raw)
        debtors = [l for l in ledgers if l["group"] == "Sundry Debtors"]
        if not debtors:
            return "No sundry debtors found."
        debtors.sort(key=lambda d: abs(d["balance"]), reverse=True)
        lines = ["RECEIVABLES (customers who owe us):\n"]
        total = 0.0
        for d in debtors:
            amt = abs(d["balance"])
            total += amt
            lines.append(f"  {d['name']}: {_fmt_currency(amt)}")
        lines.append(f"\nTotal Receivable: {_fmt_currency(total)}")
        return "\n".join(lines)
    
    return _live_or_cache("debtors", fetch, format)


@mcp.tool()
def get_sundry_creditors() -> str:
    """Get suppliers we owe money to (Sundry Creditors), sorted by amount."""
    
    def fetch():
        return tally_collection("Ledger")
    
    def format(raw):
        ledgers = _parse_ledgers(raw)
        creditors = [l for l in ledgers if l["group"] == "Sundry Creditors"]
        if not creditors:
            return "No sundry creditors found."
        creditors.sort(key=lambda c: abs(c["balance"]), reverse=True)
        lines = ["PAYABLES (we owe them):\n"]
        total = 0.0
        for c in creditors:
            amt = abs(c["balance"])
            total += amt
            lines.append(f"  {c['name']}: {_fmt_currency(amt)}")
        lines.append(f"\nTotal Payable: {_fmt_currency(total)}")
        return "\n".join(lines)
    
    return _live_or_cache("creditors", fetch, format)


@mcp.tool()
def search_ledger(partial_name: str) -> str:
    """Search for a ledger by partial name (case-insensitive)."""
    
    def fetch():
        return tally_collection("Ledger")
    
    def format(raw):
        ledgers = _parse_ledgers(raw)
        query = partial_name.lower()
        matches = [l for l in ledgers if query in l["name"].lower()]
        if not matches:
            all_names = [l["name"] for l in ledgers]
            return f"No match for '{partial_name}'. Available: {', '.join(all_names)}"
        lines = []
        for m in matches:
            bal_str = _fmt_currency(m["balance"]) if m["balance"] != 0 else "0"
            lines.append(f"{m['name']} | Group: {m['group']} | Balance: {bal_str}")
        return "\n".join(lines)
    
    # Dynamic cache key so different searches are cached separately
    return _live_or_cache(f"search_{partial_name.lower()}", fetch, format)


@mcp.tool()
def get_transactions_for_date(date: str) -> str:
    """Get all transactions for a single date. date format: YYYYMMDD"""
    
    def fetch():
        return tally_report("Day Book", from_date=date, to_date=date)
    
    def format(raw):
        vouchers = _parse_vouchers(raw)
        if not vouchers:
            return f"No transactions on {date}."
        lines = [f"Transactions on {date}: ({len(vouchers)} vouchers)\n"]
        for v in vouchers:
            party = v["party"] or "-"
            amt = _fmt_currency(v["amount"])
            line = f"  {v['type']} | {party} | {amt}"
            if v["narration"]:
                line += f" | {v['narration']}"
            lines.append(line)
        return "\n".join(lines)
    
    return _live_or_cache(f"txn_{date}", fetch, format)


@mcp.tool()
def get_transactions_for_period(from_date: str, to_date: str) -> str:
    """Get transactions summary for a date range. Max 7 days. Dates: YYYYMMDD"""
    
    def fetch():
        return tally_report("Day Book", from_date=from_date, to_date=to_date)
    
    def format(raw):
        vouchers = _parse_vouchers(raw)
        if not vouchers:
            return f"No transactions from {from_date} to {to_date}."
        by_type = Counter()
        amounts_by_type = defaultdict(float)
        for v in vouchers:
            by_type[v["type"]] += 1
            amounts_by_type[v["type"]] += v["amount"]
        lines = [
            f"Period: {from_date} to {to_date}",
            f"Total vouchers: {len(vouchers)}\n",
        ]
        for vtype, count in by_type.most_common():
            lines.append(f"  {vtype}: {count} vouchers, {_fmt_currency(amounts_by_type[vtype])}")
        lines.append("\nFirst 15:")
        for v in vouchers[:15]:
            party = v["party"] or "-"
            lines.append(f"  {v['date']} | {v['type']} | {party} | {_fmt_currency(v['amount'])}")
        if len(vouchers) > 15:
            lines.append(f"  ... and {len(vouchers) - 15} more")
        return "\n".join(lines)
    
    return _live_or_cache(f"txn_{from_date}_{to_date}", fetch, format)


@mcp.tool()
def get_tally_status() -> str:
    """Check if Tally is currently online and responding.
    Also shows age of cached data for each report type."""
    
    # Try a quick ping
    try:
        raw = tally_collection("Group")
        if "<ERROR>" not in raw:
            status = "🟢 Tally is ONLINE and responding"
        else:
            status = "🔴 Tally returned an error"
    except Exception:
        status = "🔴 Tally is OFFLINE"
    
    lines = [status, ""]
    
    # Show cache ages
    cache_keys = [
        ("Ledgers", "ledgers"),
        ("Trial Balance", "trial_balance"),
        ("P&L", "pnl"),
        ("Balance Sheet", "balance_sheet"),
        ("Debtors", "debtors"),
        ("Creditors", "creditors"),
    ]
    
    lines.append("Cached data ages:")
    for label, key in cache_keys:
        age = snapshot.age_str(key)
        lines.append(f"  {label}: {age}")
    
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()   