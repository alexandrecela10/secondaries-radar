"""Offline, deterministic classifier. Regex rules over the headline.

Precedence: sale_or_advisor > allocation_above_target > liquidity_need > policy_review >
leadership_change > none. Text is only matched, never executed, so instructions inside a
headline cannot change rules or tools.
"""
from __future__ import annotations

import re

from ..models import Classification

PE = r"(private[- ]equity|PE|private[- ]markets?|private[- ]credit|alternatives?|buyouts?|fund stakes?|LP stakes?|stakes?|portfolio|real estate|venture)"

# Buyer-side news: a pension investing in a secondaries fund is not a sale signal.
BUYER_SIDE = re.compile(
    r"\b(commit\w*|invest\w*|backs?|allocat\w*|adds?|hires?|picks?|selects?|names?|taps?)\b.{0,50}\bsecondar(y|ies)\b.{0,20}\b(funds?|managers?|strateg\w*|vehicles?|program\w*|mandates?)\b"
    r"|\bsecondar(y|ies)\b.{0,10}\b(funds?|managers?)\b.{0,40}\b(raises?|closes?|closed|final close|launch\w*)\b",
    re.IGNORECASE,
)

# A sale that already happened: "sold", "sells", "closes sale", "Buyer acquires X from".
COMPLETED = re.compile(
    r"\b(clos(e|es|ed)|complet\w*|finali[sz]\w*|seal(s|ed)?|wrap(s|ped)? up)\b.{0,50}\b(sale|deal|transaction)\b"
    r"|\b(sale|deal|transaction)\b.{0,20}\b(closes|closed|completed|done)\b"
    r"|\b(was|were) (the )?(sole |lead |only )?buyers?\b"
    r"|\b(sold|sells?)\b"          # headline present tense reports a done deal; "to sell" is prospective
    r"|\b(acquires?|acquired|buys?|bought|purchases?|purchased)\b.{0,80}\bfrom\b",
    re.IGNORECASE,
)
# Words that say the sale is still ahead. They win over COMPLETED.
PROSPECTIVE = re.compile(
    r"\b(plans?|planning|weighs?|weighing|consider\w*|explor\w*|eyes?|mulls?|seeks?|looks? to|could|may|"
    r"might|to sell|gears? up|prepar\w*|hires?|hired|taps?|tapped|launch\w*|shops?|shopping|emerges?|"
    r"in talks|nears?|nearing|readies|readying)\b",
    re.IGNORECASE,
)


def is_completed_sale(headline: str) -> bool:
    return bool(COMPLETED.search(headline)) and not PROSPECTIVE.search(headline)


RULES: list[tuple[str, float, re.Pattern[str]]] = [
    ("sale_or_advisor", 0.85, re.compile(
        rf"\b(sells?|selling|sold|offload\w*|unload\w*|divest\w*|dispos\w*|sale of|sales? of)\b.{{0,60}}\b{PE}\b"
        rf"|\b{PE}\b.{{0,40}}\b(sale|sales|selloff|sell-off)\b"
        r"|\bsecondar(y|ies)[- ](market )?(sale|sales|deal|transaction|process)\b"
        r"|\bon (the )?(secondary|secondaries) market\b"
        r"|\b(acquires?|buys?|bought|purchas\w*)\b.{0,60}\b(portfolio|stakes?|interests|funds?)\b.{0,15}\bfrom\b"
        r"|\b(shops?|shopping)\b.{0,40}\b(stakes?|portfolio|interests|funds)\b"
        r"|\b(multi-?billion|\$[\d.,]+ ?(bn|billion|b|m|million|mn)) (secondary |portfolio |stake )?sale\b"
        r"|\b(hires?|hired|taps?|tapped|retains?|retained|selects?|selected|engages?|engaged)\b.{0,60}\b(advis[eo]r|Evercore|Jefferies|Lazard|Campbell Lutyens|PJT|Park Hill|Greenhill|Houlihan|UBS|Rede)\b.{0,60}\b(sale|sell|secondar\w*)\b"
        r"|\b(put|puts|putting|shops?|shopping|markets?|marketing)\b.{0,40}\b(stakes?|portfolio|interests)\b.{0,30}\b(for sale|on the block|on the market|to buyers)\b",
        re.IGNORECASE)),
    ("sale_or_advisor", 0.7, re.compile(
        r"\b(consider\w*|weigh\w*|explor\w*|eyes?|mulls?|plans?|seeks?|looks? to|could)\b.{0,30}\b(sell\w*|sale|secondar\w*)\b",
        re.IGNORECASE)),
    ("allocation_above_target", 0.8, re.compile(
        r"\b(over-?allocat\w*|over-?weight\w*|over the target|above (its |the )?(target|policy|limit|range|ceiling)|"
        r"exceed\w* (its |the )?(target|allocation|policy|limit|range)|denominator effect|over (its |the )?target|"
        r"breach\w*.{0,20}\b(limit|range|target|ceiling)|too much private equity)\b"
        r"|\d+(\.\d+)?%.{0,40}\b(vs\.?|versus|against|compared with|above|over)\b.{0,25}\d+(\.\d+)?%.{0,10}\btarget\b",
        re.IGNORECASE)),
    ("liquidity_need", 0.65, re.compile(
        r"\b(liquidity (needs?|crunch|squeeze|pressure|concerns?|shortfall|crisis|management)|cash crunch|"
        r"raise cash|need\w* cash|cash[- ]flow (needs?|shortfall|negative|crunch)|negative cash flow|"
        r"capital calls?.{0,20}\b(outpac\w*|exceed\w*|strain\w*)|distributions? (drought|slowdown|dry up|dried up|slow\w*)|"
        r"funding (gap|shortfall|crisis)|benefit payments.{0,30}\b(cash|liquidity|sell))\b",
        re.IGNORECASE)),
    ("policy_review", 0.6, re.compile(
        rf"\b(cuts?|cutting|reduc\w*|lower\w*|trims?|trimm\w*|pares?|paring|slash\w*|scal\w* back|pull\w* back|"
        rf"slow\w*|curb\w*|rein\w* in|dial\w* back|rebalanc\w*)\b.{{0,30}}\b{PE}\b"
        rf"|\b{PE}\b.{{0,40}}\b(cut|cuts|reduction|target cut|review|overhaul)\b"
        r"|\b(review\w*|reconsider\w*|rethink\w*|revisit\w*|overhaul\w*|reassess\w*)\b.{0,50}\b(private[- ]equity|private[- ]markets?|alternatives?|asset allocation|allocation|portfolio|pacing|target)\b"
        r"|\b(asset[- ]allocation study|asset[- ]liability study|adjusts? .{0,10}pacing|cull\w*|restructur\w*)\b",
        re.IGNORECASE)),
    ("leadership_change", 0.7, re.compile(
        r"\b(names?|named|appoints?|appointed|hires?|hired|taps?|tapped|selects?|selected|promotes?|promoted|picks?|tabs?)\b.{0,60}\b(CIO|chief investment officer|executive director|CEO|chief executive|head of private (equity|markets)|director of private (equity|markets))\b"
        r"|\b(CIO|chief investment officer|executive director|CEO|chief executive|head of private (equity|markets))\b.{0,50}\b(resign\w*|retir(e|es|ed|ing)\b|steps? down|stepping down|depart\w*|leav\w*|exits?|ousted|fired|out\b|to leave)"
        r"|\b(new|interim|incoming|outgoing|departing) (CIO|chief investment officer|executive director|CEO)\b"
        r"|\bprivate (markets|equity) (exec|executive|chief|head)\b.{0,60}\b(retir(e|es|ed|ing)\b|resign\w*|steps? down|depart\w*|leav\w*)",
        re.IGNORECASE)),
]


class KeywordClassifier:
    name = "keyword"
    version = "kw-3"     # bump on any rule change: stored labels from other versions are redone

    def classify(self, headline: str, pension_name: str | None = None) -> Classification:
        text = headline
        buyer = bool(BUYER_SIDE.search(text))
        for signal, conf, pattern in RULES:
            if signal == "sale_or_advisor" and buyer:
                continue
            m = pattern.search(text)
            if m:
                done = signal == "sale_or_advisor" and is_completed_sale(text)
                return Classification(signal_type=signal, confidence=conf, quote=headline[:300], completed=done,
                                      rationale=f"rule {signal}@{conf}{' completed' if done else ''}: "
                                                f"'{m.group(0)[:70]}'")
        return Classification(signal_type="none", confidence=0.9 if not buyer else 0.8, quote=headline[:300],
                              rationale="buyer-side secondaries news" if buyer else "no rule matched")
