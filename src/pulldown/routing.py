"""Routing, feature extraction, classifier scoring, and quality evaluation."""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from lxml import etree


LABEL_ORDER = [
    "article",
    "docs",
    "listing",
    "table_heavy",
    "app_shell",
    "generic",
]

FEATURE_ORDER = [
    "url_path_depth",
    "url_query_param_count",
    "url_has_blog",
    "url_has_post",
    "url_has_docs",
    "url_has_api",
    "url_has_search",
    "url_has_leaderboard",
    "has_main",
    "has_article",
    "has_nav",
    "has_aside",
    "has_search_form",
    "has_pagination",
    "doc_paragraphs",
    "doc_headings",
    "doc_list_items",
    "doc_links",
    "doc_code_blocks",
    "doc_tables",
    "doc_table_rows",
    "doc_forms",
    "doc_inputs",
    "doc_buttons",
    "main_paragraphs",
    "main_headings",
    "main_list_items",
    "main_links",
    "main_code_blocks",
    "main_tables",
    "main_rows",
    "main_words",
    "doc_link_density",
    "main_link_density",
    "avg_paragraph_len",
    "avg_list_item_len",
    "numeric_token_ratio",
    "short_paragraph_ratio",
    "rows_per_table",
    "paragraph_to_heading_ratio",
    "duplicate_link_text_ratio",
    "repeated_row_signature_count",
    "boilerplate_token_hits",
    "nav_footer_token_ratio",
    "title_has_docs",
    "title_has_api",
    "title_has_blog",
    "title_has_leaderboard",
]

STRATEGY_FOR_LABEL = {
    "article": "article",
    "docs": "article",
    "listing": "structured",
    "table_heavy": "structured",
    "app_shell": "full",
    "generic": "full",
}

_BOOLEAN_FEATURES = {
    "url_has_blog",
    "url_has_post",
    "url_has_docs",
    "url_has_api",
    "url_has_search",
    "url_has_leaderboard",
    "has_main",
    "has_article",
    "has_nav",
    "has_aside",
    "has_search_form",
    "has_pagination",
    "title_has_docs",
    "title_has_api",
    "title_has_blog",
    "title_has_leaderboard",
}

_BOILERPLATE_TOKENS = frozenset(
    {
        "nav",
        "navbar",
        "footer",
        "menu",
        "mobile",
        "sidebar",
        "drawer",
        "breadcrumb",
        "social",
        "share",
        "sharing",
        "pagination",
    }
)

_TITLE_FLAG_TOKENS = {
    "title_has_docs": ("docs", "reference", "guide"),
    "title_has_api": ("api",),
    "title_has_blog": ("blog", "post"),
    "title_has_leaderboard": ("leaderboard", "ranking", "rankings"),
}


@dataclass(frozen=True)
class RoutingPlan:
    page_type: str
    source: str
    confidence: float
    abstained: bool
    strategy: str
    render_recommended: bool
    features: dict[str, float]
    rule_label: str | None
    classifier_probabilities: dict[str, float]


def _squash_whitespace(text: str) -> str:
    return " ".join(text.split())


def _body_node(tree: Any) -> Any | None:
    bodies = tree.xpath("//body")
    return bodies[0] if bodies else None


def _node_tokens(node: Any) -> set[str]:
    values: list[str] = []
    for attr in ("class", "id", "role", "aria-label"):
        value = node.get(attr)
        if value:
            values.extend(re.split(r"[^a-z0-9]+", value.lower()))
    return {value for value in values if value}


def _count_code_blocks(node: Any) -> int:
    pre = node.xpath(".//pre")
    standalone_code = node.xpath(".//code[not(ancestor::pre)]")
    return len(pre) + len(standalone_code)


def _select_readable_landmark(tree: Any) -> Any | None:
    candidates = tree.xpath("//main | //*[@role='main'] | //article")
    if candidates:
        return max(
            candidates,
            key=lambda node: (
                len(node.xpath(".//section")) * 20
                + len(node.xpath(".//*[self::h2 or self::h3 or self::h4]")) * 10
                + len(node.xpath(".//ul/li | .//ol/li")) * 3
                + len(node.xpath(".//dt")) * 4
                + len(_squash_whitespace(" ".join(node.itertext()))),
            ),
        )
    return _body_node(tree)


def _text_length(node: Any) -> int:
    return len(_squash_whitespace(" ".join(node.itertext())))


def _link_text_length(node: Any) -> int:
    return sum(len(_squash_whitespace(" ".join(link.itertext()))) for link in node.xpath(".//a"))


def _average_length(nodes: list[Any]) -> float:
    lengths = [len(_squash_whitespace(" ".join(node.itertext()))) for node in nodes]
    lengths = [length for length in lengths if length > 0]
    if not lengths:
        return 0.0
    return float(sum(lengths) / len(lengths))


def _numeric_token_ratio(text: str) -> float:
    tokens = re.findall(r"\b\w+\b", text)
    if not tokens:
        return 0.0
    numeric = sum(1 for token in tokens if any(ch.isdigit() for ch in token))
    return numeric / len(tokens)


def _short_paragraph_ratio(paragraphs: list[Any]) -> float:
    if not paragraphs:
        return 0.0
    short = 0
    for paragraph in paragraphs:
        if len(_squash_whitespace(" ".join(paragraph.itertext()))) < 80:
            short += 1
    return short / len(paragraphs)


def _duplicate_link_text_ratio(node: Any) -> float:
    texts = [
        _squash_whitespace(" ".join(link.itertext())).lower()
        for link in node.xpath(".//a")
        if _squash_whitespace(" ".join(link.itertext()))
    ]
    if not texts:
        return 0.0
    counts = Counter(texts)
    duplicates = sum(count - 1 for count in counts.values() if count > 1)
    return duplicates / len(texts)


def _repeated_row_signature_count(node: Any) -> int:
    signatures: list[str] = []

    for row in node.xpath(".//table//tr[td]"):
        cells = [_squash_whitespace(" ".join(cell.itertext())).lower() for cell in row.xpath("./td")]
        cells = [cell for cell in cells if cell]
        if len(cells) >= 2:
            signatures.append("|".join(cells[:6]))

    for parent in node.xpath(".//*[count(*) >= 3]"):
        children = [child for child in parent if isinstance(child.tag, str) and child.tag in {"li", "article", "div", "section"}]
        if len(children) < 3:
            continue
        grouped: dict[tuple[str, tuple[str, ...]], list[Any]] = defaultdict(list)
        for child in children:
            grouped[(child.tag, tuple(sorted(_node_tokens(child))))].append(child)
        for grouped_children in grouped.values():
            if len(grouped_children) < 3:
                continue
            for child in grouped_children:
                text = _squash_whitespace(" ".join(child.itertext())).lower()
                if 10 <= len(text) <= 200:
                    signatures.append(text[:120])

    counts = Counter(signatures)
    return sum(count - 1 for count in counts.values() if count > 1)


def _boilerplate_token_hits(node: Any) -> int:
    hits = 0
    for candidate in node.xpath(".//*"):
        if not isinstance(candidate.tag, str):
            continue
        hits += len(_node_tokens(candidate) & _BOILERPLATE_TOKENS)
    return hits


def _nav_footer_token_ratio(node: Any) -> float:
    visible = _text_length(node)
    if visible <= 0:
        return 0.0
    chrome_nodes = node.xpath(".//nav | .//footer | .//aside")
    chrome_chars = sum(_text_length(candidate) for candidate in chrome_nodes)
    return chrome_chars / visible


def _has_search_form(node: Any) -> bool:
    if node.xpath(".//form[.//input[@type='search']]"):
        return True
    for candidate in node.xpath(".//form | .//*[@role='search']"):
        tokens = _node_tokens(candidate)
        if "search" in tokens:
            return True
    return False


def _has_pagination(node: Any) -> bool:
    if node.xpath(".//*[@rel='next' or @rel='prev']"):
        return True
    for candidate in node.xpath(".//*"):
        if not isinstance(candidate.tag, str):
            continue
        if "pagination" in _node_tokens(candidate):
            return True
        label = (candidate.get("aria-label") or "").lower()
        if "pagination" in label:
            return True
    return False


def _counts_for(node: Any) -> dict[str, float]:
    paragraphs = node.xpath(".//p")
    headings = node.xpath(".//*[self::h1 or self::h2 or self::h3 or self::h4]")
    list_items = node.xpath(".//ul/li | .//ol/li")
    links = node.xpath(".//a[@href]")
    tables = node.xpath(".//table")
    rows = node.xpath(".//table//tr")
    forms = node.xpath(".//form")
    inputs = node.xpath(".//input | .//textarea | .//select")
    buttons = node.xpath(".//button")
    visible_chars = max(_text_length(node), 1)
    link_chars = _link_text_length(node)

    return {
        "paragraphs": float(len(paragraphs)),
        "headings": float(len(headings)),
        "list_items": float(len(list_items)),
        "links": float(len(links)),
        "code_blocks": float(_count_code_blocks(node)),
        "tables": float(len(tables)),
        "table_rows": float(len(rows)),
        "forms": float(len(forms)),
        "inputs": float(len(inputs)),
        "buttons": float(len(buttons)),
        "words": float(len(_squash_whitespace(" ".join(node.itertext())).split())),
        "link_density": float(link_chars / visible_chars),
        "avg_paragraph_len": float(_average_length(paragraphs)),
        "avg_list_item_len": float(_average_length(list_items)),
        "short_paragraph_ratio": float(_short_paragraph_ratio(paragraphs)),
        "paragraph_to_heading_ratio": float(len(paragraphs) / max(len(headings), 1)),
        "duplicate_link_text_ratio": float(_duplicate_link_text_ratio(node)),
        "repeated_row_signature_count": float(_repeated_row_signature_count(node)),
        "boilerplate_token_hits": float(_boilerplate_token_hits(node)),
        "nav_footer_token_ratio": float(_nav_footer_token_ratio(node)),
        "numeric_token_ratio": float(_numeric_token_ratio(_squash_whitespace(" ".join(node.itertext())))),
        "rows_per_table": float(len(rows) / max(len(tables), 1)),
    }


def _title_text(tree: Any) -> str:
    titles = tree.xpath("//title/text()")
    return _squash_whitespace(str(titles[0])) if titles else ""


def extract_features(html: str, url: str) -> dict[str, float]:
    tree = etree.HTML(html)
    if tree is None:
        return {name: 0.0 for name in FEATURE_ORDER}

    body = _body_node(tree)
    if body is None:
        body = tree
    main = _select_readable_landmark(tree)
    if main is None:
        main = body

    parsed = urlparse(url)
    path = parsed.path.lower()
    title = _title_text(tree).lower()
    doc = _counts_for(body)
    main_counts = _counts_for(main)

    features = {
        "url_path_depth": float(len([segment for segment in path.split("/") if segment])),
        "url_query_param_count": float(len([param for param in parsed.query.split("&") if param])),
        "url_has_blog": 1.0 if any(token in path for token in ("/blog", "/blogs")) else 0.0,
        "url_has_post": 1.0 if any(token in path for token in ("/post", "/posts", "/article")) else 0.0,
        "url_has_docs": 1.0 if any(token in path for token in ("/docs", "/reference", "/guide")) else 0.0,
        "url_has_api": 1.0 if "/api" in path else 0.0,
        "url_has_search": 1.0 if "/search" in path or "search=" in parsed.query.lower() else 0.0,
        "url_has_leaderboard": 1.0 if any(token in path for token in ("leaderboard", "ranking")) else 0.0,
        "has_main": 1.0 if tree.xpath("//main | //*[@role='main']") else 0.0,
        "has_article": 1.0 if tree.xpath("//article") else 0.0,
        "has_nav": 1.0 if tree.xpath("//nav") else 0.0,
        "has_aside": 1.0 if tree.xpath("//aside") else 0.0,
        "has_search_form": 1.0 if _has_search_form(body) else 0.0,
        "has_pagination": 1.0 if _has_pagination(body) else 0.0,
        "doc_paragraphs": doc["paragraphs"],
        "doc_headings": doc["headings"],
        "doc_list_items": doc["list_items"],
        "doc_links": doc["links"],
        "doc_code_blocks": doc["code_blocks"],
        "doc_tables": doc["tables"],
        "doc_table_rows": doc["table_rows"],
        "doc_forms": doc["forms"],
        "doc_inputs": doc["inputs"],
        "doc_buttons": doc["buttons"],
        "main_paragraphs": main_counts["paragraphs"],
        "main_headings": main_counts["headings"],
        "main_list_items": main_counts["list_items"],
        "main_links": main_counts["links"],
        "main_code_blocks": main_counts["code_blocks"],
        "main_tables": main_counts["tables"],
        "main_rows": main_counts["table_rows"],
        "main_words": main_counts["words"],
        "doc_link_density": doc["link_density"],
        "main_link_density": main_counts["link_density"],
        "avg_paragraph_len": main_counts["avg_paragraph_len"],
        "avg_list_item_len": main_counts["avg_list_item_len"],
        "numeric_token_ratio": doc["numeric_token_ratio"],
        "short_paragraph_ratio": main_counts["short_paragraph_ratio"],
        "rows_per_table": doc["rows_per_table"],
        "paragraph_to_heading_ratio": main_counts["paragraph_to_heading_ratio"],
        "duplicate_link_text_ratio": doc["duplicate_link_text_ratio"],
        "repeated_row_signature_count": max(
            doc["repeated_row_signature_count"], main_counts["repeated_row_signature_count"]
        ),
        "boilerplate_token_hits": doc["boilerplate_token_hits"],
        "nav_footer_token_ratio": doc["nav_footer_token_ratio"],
        "title_has_docs": 0.0,
        "title_has_api": 0.0,
        "title_has_blog": 0.0,
        "title_has_leaderboard": 0.0,
    }

    for feature_name, tokens in _TITLE_FLAG_TOKENS.items():
        features[feature_name] = 1.0 if any(token in title for token in tokens) else 0.0

    for name in FEATURE_ORDER:
        features.setdefault(name, 0.0)

    return features


def _apply_rules(features: dict[str, float]) -> str | None:
    if (
        (features["url_has_docs"] or features["title_has_docs"] or features["title_has_api"])
        and features["main_headings"] >= 3
        and features["doc_code_blocks"] >= 1
    ):
        return "docs"

    if features["doc_tables"] >= 1 and features["doc_table_rows"] >= 8:
        return "table_heavy"

    if (
        features["doc_links"] >= 25
        and (
            features["has_pagination"]
            or features["duplicate_link_text_ratio"] >= 0.15
            or features["repeated_row_signature_count"] >= 4
        )
    ):
        return "listing"

    if (
        features["doc_forms"] + features["doc_inputs"] + features["doc_buttons"] >= 8
        or (
            features["has_search_form"]
            and features["doc_links"] >= 20
            and features["main_words"] < 250
        )
    ):
        return "app_shell"

    if (
        features["main_paragraphs"] >= 3
        and features["avg_paragraph_len"] >= 80
        and features["main_tables"] == 0
        and features["main_link_density"] < 0.25
    ):
        return "article"

    return None


@lru_cache(maxsize=1)
def _load_model() -> dict[str, Any]:
    with resources.files("pulldown").joinpath("routing_model.json").open("r", encoding="utf-8") as f:
        model = json.load(f)
    return model


def _softmax(scores: list[float]) -> list[float]:
    pivot = max(scores)
    exp_scores = [math.exp(score - pivot) for score in scores]
    total = sum(exp_scores) or 1.0
    return [score / total for score in exp_scores]


def _predict_probabilities(features: dict[str, float]) -> dict[str, float]:
    model = _load_model()
    vector = [float(features[name]) for name in FEATURE_ORDER]
    means = model["means"]
    scales = model["scales"]
    normalized = [
        (value - mean) / scale if scale not in (0, 0.0) else (value - mean)
        for value, mean, scale in zip(vector, means, scales, strict=True)
    ]

    scores: list[float] = []
    for coef, intercept in zip(model["coef"], model["intercept"], strict=True):
        score = intercept
        for weight, value in zip(coef, normalized, strict=True):
            score += weight * value
        scores.append(score)

    probs = _softmax(scores)
    return {label: prob for label, prob in zip(LABEL_ORDER, probs, strict=True)}


def _conservative_fallback_strategy(features: dict[str, float]) -> str:
    if (
        features["doc_tables"] >= 1
        or features["doc_list_items"] >= 20
        or features["doc_links"] >= 25
        or features["repeated_row_signature_count"] >= 4
    ):
        return "structured"
    return "full"


def plan_routing(
    html: str,
    url: str,
    *,
    requested_detail: str,
    render: bool,
) -> RoutingPlan:
    features = extract_features(html, url)
    rule_label = _apply_rules(features)
    classifier_probabilities: dict[str, float] = {}
    source = "rules"
    abstained = False
    confidence = 1.0

    if rule_label is not None:
        page_type = rule_label
    else:
        classifier_probabilities = _predict_probabilities(features)
        page_type, confidence = max(classifier_probabilities.items(), key=lambda item: item[1])
        if confidence < 0.55:
            page_type = "generic"
            abstained = True
            source = "hybrid"
        else:
            source = "classifier"

    if requested_detail == "readable":
        if rule_label is not None:
            strategy = STRATEGY_FOR_LABEL[page_type]
        elif abstained:
            strategy = _conservative_fallback_strategy(features)
        else:
            strategy = STRATEGY_FOR_LABEL[page_type]
    else:
        strategy = "article" if requested_detail == "readable" else requested_detail

    render_recommended = bool(
        page_type == "app_shell"
        or (
            not render
            and features["main_words"] < 120
            and (features["doc_forms"] + features["doc_inputs"] + features["doc_buttons"] >= 4)
        )
    )

    return RoutingPlan(
        page_type=page_type,
        source=source,
        confidence=float(confidence),
        abstained=abstained,
        strategy=strategy,
        render_recommended=render_recommended,
        features=features,
        rule_label=rule_label,
        classifier_probabilities=classifier_probabilities,
    )


def _paragraph_like_blocks(markdown: str) -> int:
    blocks = [block.strip() for block in markdown.split("\n\n") if block.strip()]
    count = 0
    for block in blocks:
        line = block.splitlines()[0].strip()
        if line.startswith(("#", "-", "*", ">", "|")):
            continue
        count += 1
    return count


def quality_grade(strategy: str, content: str) -> str:
    content = content or ""
    stripped = content.strip()
    if strategy == "raw":
        return "medium" if stripped else "low"

    nonempty_lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    heading_count = sum(1 for line in nonempty_lines if line.startswith("#"))
    bullet_count = sum(1 for line in nonempty_lines if line.startswith("- "))
    table_summary_count = sum(1 for line in nonempty_lines if line.startswith("- Table columns:"))

    if strategy in {"article", "minimal"}:
        paragraphs = _paragraph_like_blocks(stripped)
        table_lines = sum(1 for line in nonempty_lines if "|" in line)
        table_dominated = table_lines > max(len(nonempty_lines) // 3, 2)
        if len(stripped) >= 1200 and paragraphs >= 3 and not table_dominated:
            return "high"
        if len(stripped) >= 400 and paragraphs >= 2:
            return "medium"
        return "low"

    if strategy == "structured":
        if heading_count >= 2 and (bullet_count >= 4 or table_summary_count >= 1) and 300 <= len(stripped) <= 30000:
            return "high"
        if heading_count >= 1 and len(stripped) >= 200:
            return "medium"
        return "low"

    if strategy == "full":
        if len(stripped) >= 1000:
            return "high"
        if len(stripped) >= 300:
            return "medium"
        return "low"

    return "low"


def fallback_strategy(initial_strategy: str, quality: str, features: dict[str, float]) -> str | None:
    if quality != "low":
        return None
    if initial_strategy == "article":
        if (
            features["doc_tables"] >= 1
            or features["doc_list_items"] >= 20
            or features["main_link_density"] >= 0.35
        ):
            return "structured"
        return "full"
    if initial_strategy == "structured":
        return "full"
    return None


def public_routing_meta(
    plan: RoutingPlan,
    *,
    final_source: str,
    final_strategy: str,
    final_quality: str,
) -> dict[str, Any]:
    return {
        "page_type": plan.page_type,
        "source": final_source,
        "confidence": round(plan.confidence, 6),
        "abstained": plan.abstained,
        "strategy_used": final_strategy,
        "quality_grade": final_quality,
        "render_recommended": plan.render_recommended,
    }


def build_log_record(
    *,
    url: str,
    requested_detail: str,
    render: bool,
    status_code: int,
    ok: bool,
    from_cache: bool,
    error: str | None,
    plan: RoutingPlan,
    initial_strategy: str,
    final_strategy: str,
    initial_quality: str,
    final_quality: str,
    route_changed: bool,
    content_chars: int,
) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "url": url,
        "requested_detail": requested_detail,
        "render": render,
        "status_code": status_code,
        "ok": ok,
        "from_cache": from_cache,
        "features": plan.features,
        "rule_label": plan.rule_label,
        "classifier_used": plan.source != "rules",
        "classifier_probabilities": plan.classifier_probabilities,
        "chosen_label": plan.page_type,
        "confidence": round(plan.confidence, 6),
        "abstained": plan.abstained,
        "strategy_initial": initial_strategy,
        "strategy_final": final_strategy,
        "quality_grade_initial": initial_quality,
        "quality_grade_final": final_quality,
        "route_changed": route_changed,
        "content_chars": content_chars,
        "error": error,
    }


def write_routing_log(path: str | None, record: dict[str, Any]) -> None:
    if not path:
        return
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        f.write("\n")
