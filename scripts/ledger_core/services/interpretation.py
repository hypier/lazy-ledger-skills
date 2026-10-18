"""Natural-language event interpretation; no persistence."""
import re
from ledger_core.domain.model import default_currency
from ledger_core.domain.metrics import tx_day, compact_candidate

from ledger_core.domain.vocabulary import (ACCOUNT_KEYWORDS, ADVANCE_KEYWORDS, AMOUNT_RE, CATEGORY_KEYWORDS, CHINESE_DIGITS, DATE_TOKEN_RE, FALLBACK_MERCHANTS, INCOME_ONLY_CATEGORIES, KNOWN_MERCHANTS, METHOD_KEYWORDS, ORIGINAL_PRICE_RE, PAID_AMOUNT_RE, RED_PACKET_EXPENSE_KEYWORDS, RED_PACKET_INCOME_KEYWORDS, REIMBURSE_EXPECTED_KEYWORDS, REIMBURSE_INCOME_KEYWORDS, REPAYMENT_INCOME_KEYWORDS, TAG_KEYWORDS, TOTAL_HINTS, TRANSFER_VERBS, TYPE_KEYWORDS, TYPE_LABELS, TYPE_LINE_MAP)
from ledger_core.services.common import (apply_account_fields, apply_aliases_to_text, infer_transfer_route, parse_when, preferred_category, safe_amount, unique_tags)
from ledger_core.services.habits import (apply_habit_to_proposal, apply_usage_defaults, match_habit, seed_habits_if_needed)


def classify_event_detailed(text):
    """Decide the economic event once and say *why*.

    Returns a dict with ``event``, ``type``, ``category``, ``flags``,
    ``confidence`` and ``reasons``. Type and category are derived together so
    they cannot contradict each other, and every branch records the evidence
    that fired, which lets the assistant explain a decision instead of
    asserting it.

    ``reasons`` is a list of ``{"rule": ..., "match": ...}`` entries in firing
    order; the last one is the decisive rule.
    """
    reasons = []

    def decide(event, tx_type, category, flags=None, confidence=1.0, rule="", match=""):
        if rule:
            reasons.append({"rule": rule, "match": match})
        return {
            "event": event,
            "type": tx_type,
            "category": category,
            "flags": flags or {},
            "confidence": confidence,
            "reasons": reasons,
        }

    for keyword in REPAYMENT_INCOME_KEYWORDS:
        if keyword in text:
            return decide("repayment_arrival", "income", "收入", {"reimburse": True}, 0.9,
                          "REPAYMENT_INCOME_KEYWORDS", keyword)
    for keyword in REIMBURSE_INCOME_KEYWORDS:
        if keyword in text:
            # Reimbursement arriving is income, and it settles an earlier advance.
            return decide("reimbursement_arrival", "income", "收入", {"reimburse": True}, 0.95,
                          "REIMBURSE_INCOME_KEYWORDS", keyword)
    for keyword in REIMBURSE_EXPECTED_KEYWORDS:
        if keyword in text:
            # "打车26，公司报销" is money going out that will come back: an
            # advance, not income. Getting this backwards moves the period total
            # in the wrong direction, which is worse than any mis-labelled row.
            return decide("advance_expected", "expense", "其他", {"advance": True}, 0.75,
                          "REIMBURSE_EXPECTED_KEYWORDS", keyword)
    for keyword in RED_PACKET_INCOME_KEYWORDS:
        if keyword in text:
            return decide("red_packet_received", "income", "红包", {}, 0.9,
                          "RED_PACKET_INCOME_KEYWORDS", keyword)
    for keyword in RED_PACKET_EXPENSE_KEYWORDS:
        if keyword in text:
            return decide("red_packet_sent", "expense", "红包", {}, 0.9,
                          "RED_PACKET_EXPENSE_KEYWORDS", keyword)
    for keyword in ADVANCE_KEYWORDS:
        if keyword in text:
            # Advance/loan: cash leaves now but is owed back, so it must not count
            # as ordinary spending.
            return decide("advance_out", "expense", "其他", {"advance": True}, 0.7,
                          "ADVANCE_KEYWORDS", keyword)
    # An explicit route between two of the user's own accounts is a transfer,
    # even when the wording is "转100到" rather than the literal "还信用卡".
    route = infer_transfer_route(text)
    if route:
        return decide("own_account_transfer", "transfer", "转账", {}, 0.9,
                      "TRANSFER_ROUTE_RE", "->".join(str(part) for part in route if part))
    for tx_type, keywords in TYPE_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text:
                if tx_type == "income":
                    return decide("income", tx_type, ("红包" if "红包" in text else "收入"), {}, 1.0,
                                  "TYPE_KEYWORDS[income]", keyword)
                if tx_type == "transfer":
                    return decide("own_account_transfer", tx_type, "转账", {}, 1.0,
                                  "TYPE_KEYWORDS[transfer]", keyword)
                return decide(tx_type, tx_type, None, {}, 1.0, f"TYPE_KEYWORDS[{tx_type}]", keyword)
    for verb in TRANSFER_VERBS:
        if verb in text:
            return decide("own_account_transfer", "transfer", "转账", {}, 0.8, "TRANSFER_VERBS", verb)
    for category in CATEGORY_KEYWORDS:
        if category in text:
            return decide("spending", "expense", category, {}, 1.0, "CATEGORY_KEYWORDS[exact]", category)
    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text:
                # A category-only cue such as "报销" without arrival language is an
                # advance awaiting settlement, not a finished expense.
                if category in INCOME_ONLY_CATEGORIES:
                    return decide("advance_expected", "expense", "其他", {"advance": True}, 0.7,
                                  "CATEGORY_KEYWORDS[income-only]", keyword)
                return decide("spending", "expense", category, {}, 1.0, f"CATEGORY_KEYWORDS[{category}]", keyword)
    # Nothing matched: the row is still spending, but we say so honestly.
    return decide("unknown_spending", "expense", "其他", {}, 0.6, "fallback", "no keyword matched")


def classify_event(text):
    """Backwards-compatible tuple form of :func:`classify_event_detailed`."""
    decision = classify_event_detailed(text)
    return decision["type"], decision["category"], decision["flags"], decision["confidence"]


def infer_type(text):
    tx_type, _category, _flags, _confidence = classify_event(text)
    return tx_type


def infer_category(text, tx_type="expense"):
    _event_type, category, _flags, _confidence = classify_event(text)
    if category is None:
        category = "其他"
    if tx_type == "income":
        return "红包" if "红包" in text else "收入"
    # Never let a type/category mismatch leak out through this legacy helper.
    if category in INCOME_ONLY_CATEGORIES and tx_type != "income":
        return "其他"
    return category


def infer_method(text):
    for method, keywords in METHOD_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return method
    return None


def infer_account(text, method=None):
    for account, keywords in ACCOUNT_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return account
    return None


def infer_tags(text):
    tags = []
    for tag, keywords in TAG_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            tags.append(tag)
    return tags


def extract_date_token(text):
    match = DATE_TOKEN_RE.search(text)
    return match.group(0) if match else None


def parse_amount_line(line):
    stripped = line.replace(",", "").replace(" ", "").replace("元", "")
    stripped = stripped.replace("¥", "").replace("￥", "")
    sign = "+" if stripped.startswith("+") else ("-" if stripped.startswith("-") else None)
    if stripped[:1] in "+-":
        stripped = stripped[1:]
    if re.fullmatch(r"\d+(?:\.\d{1,2})?", stripped):
        return sign, stripped
    return None


def chinese_amount(value):
    """Convert the common short Chinese money forms used in chat."""
    if value.isdigit():
        return float(value)
    total = 0
    current = 0
    units = {"十": 10, "百": 100, "千": 1000}
    for char in value:
        if char in CHINESE_DIGITS:
            current = CHINESE_DIGITS[char]
        elif char in units:
            total += (current or 1) * units[char]
            current = 0
        else:
            return None
    return float(total + current) if total or current else None


def extract_amount(text):
    chinese = re.search(r"(?:实付|券后|合计|一共|总计|总共|共计|花了|用了)\s*(?:[为是:：]?\s*)?([零〇一二两三四五六七八九十百千]+)\s*(?:元|块|块钱)", text)
    if chinese:
        value = chinese_amount(chinese.group(1))
        if value is not None:
            cleaned = f"{text[:chinese.start()]} {text[chinese.end():]}"
            return round(value, 2), cleaned
    # A labeled total is semantic evidence, so prefer its number over a
    # nearby quantity (for example, “合计38元，买了2杯咖啡”).
    labeled_total = re.search(
        r"(?:实付|券后|合计|一共|总计|总共|共计)(?:花了|用了)?\s*(?:[为是:：]?\s*)?(\d+(?:\.\d{1,2})?)\s*(?:元|块|块钱)?",
        text,
    )
    if labeled_total and not re.search(r"(?:实付|券后)", text[: labeled_total.start()]):
        value = round(float(labeled_total.group(1)), 2)
        cleaned = f"{text[:labeled_total.start()]} {text[labeled_total.end():]}"
        # Remove other numeric tokens only after retaining the labeled total.
        cleaned = AMOUNT_RE.sub(" ", cleaned)
        return value, cleaned
    paid = PAID_AMOUNT_RE.search(text)
    cleaned = ORIGINAL_PRICE_RE.sub(" ", text)
    if paid:
        cleaned = PAID_AMOUNT_RE.sub(" ", cleaned)
        cleaned = AMOUNT_RE.sub(" ", cleaned)
        return round(float(paid.group(1)), 2), cleaned
    matches = list(AMOUNT_RE.finditer(cleaned))
    if not matches:
        return None, cleaned
    values = [match.group(1) for match in matches]
    if len(values) > 1:
        if any(hint in text for hint in TOTAL_HINTS):
            match = matches[-1]
            cleaned = f"{cleaned[:match.start()]} {cleaned[match.end():]}"
            return round(float(match.group(1)), 2), cleaned
        raise SystemExit(f"Multiple amounts found: {', '.join(values)}")
    match = matches[0]
    cleaned = f"{cleaned[:match.start()]} {cleaned[match.end():]}"
    return round(float(match.group(1)), 2), cleaned


def strip_known_tokens(text):
    for category_name, keywords in CATEGORY_KEYWORDS.items():
        text = text.replace(category_name, " ")
        for keyword in keywords:
            if keyword in KNOWN_MERCHANTS:
                continue
            text = text.replace(keyword, " ")
    for keywords in list(TYPE_KEYWORDS.values()) + list(METHOD_KEYWORDS.values()) + list(TAG_KEYWORDS.values()):
        for keyword in keywords:
            text = text.replace(keyword, " ")
    for word in ("支出", "收入", "退款", "转账", "记账", "从", "转到", "转给", "转入"):
        text = text.replace(word, " ")
    return text


def infer_merchant(cleaned_text, category, original_text=""):
    haystack = f"{cleaned_text} {original_text}"
    for merchant in KNOWN_MERCHANTS:
        if merchant in haystack:
            return merchant
    text = strip_known_tokens(cleaned_text)
    text = re.sub(r"[，,。；;:：()（）\[\]【】]", " ", text)
    parts = [part.strip() for part in text.split() if part.strip()]
    parts = [part for part in parts if part != category]
    # A single leftover character is a fragment of the original phrase (给, 的,
    # 买), not a merchant name; prefer the fallback list over it.
    parts = [part for part in parts if len(part) > 1]
    if parts:
        return parts[0]
    for merchant in FALLBACK_MERCHANTS:
        if merchant in original_text:
            return merchant
    return None


def split_line_by_amounts(line):
    line = line.strip()
    if not line:
        return []
    if PAID_AMOUNT_RE.search(line) or any(hint in line for hint in TOTAL_HINTS):
        return [line]
    date_token = extract_date_token(line)
    working = line.replace(date_token, " ", 1) if date_token else line
    working = ORIGINAL_PRICE_RE.sub(" ", working)
    matches = list(AMOUNT_RE.finditer(working))
    if len(matches) <= 1:
        return [line]
    items = []
    for index, match in enumerate(matches):
        start = 0 if index == 0 else matches[index - 1].end()
        piece = working[start:match.end()].strip()
        if piece:
            items.append(f"{date_token} {piece}".strip() if date_token else piece)
    return items or [line]


def split_text_items(text):
    chunks = [chunk.strip() for chunk in re.split(r"[\n；;]+", text) if chunk.strip()]
    items = []
    for chunk in chunks:
        items.extend(split_line_by_amounts(chunk))
    return items


def is_date_line(line):
    token = extract_date_token(line)
    if not token:
        return False
    remainder = line.replace(token, "").strip()
    remainder = re.sub(r"[\sT:：-]", "", remainder)
    return remainder == "" or bool(re.fullmatch(r"\d{2}", remainder))


def try_parse_stacked_rows(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return None
    amount_lines = [line for line in lines if parse_amount_line(line)]
    if len(amount_lines) < 1:
        return None
    rows = []
    buf = {"merchant": None, "tx_type": None, "amount": None, "date": None, "sign": None}

    def flush():
        if buf["amount"] is None:
            return
        tx_type = buf["tx_type"]
        if not tx_type:
            tx_type = "income" if buf["sign"] == "+" else "expense"
        type_word = TYPE_LABELS.get(tx_type, "")
        parts = [buf["date"] or "", buf["merchant"] or "", buf["amount"], type_word]
        rows.append(" ".join(part for part in parts if part))
        buf.update(merchant=None, tx_type=None, amount=None, date=None, sign=None)

    for line in lines:
        if line in TYPE_LINE_MAP:
            buf["tx_type"] = TYPE_LINE_MAP[line]
            continue
        if is_date_line(line):
            buf["date"] = extract_date_token(line)
            continue
        parsed_amount = parse_amount_line(line)
        if parsed_amount is not None:
            sign, amount = parsed_amount
            if buf["amount"] is not None:
                flush()
            buf["amount"] = amount
            buf["sign"] = sign
            continue
        if buf["amount"] is not None and buf["merchant"]:
            flush()
        buf["merchant"] = line
    flush()
    if len(rows) >= 1 and len(amount_lines) >= 1:
        return rows
    return None


def parse_text_transaction(text, ledger=None, currency="CNY", source="text", confidence=None, require_amount=True):
    """Build a proposal from casual text.

    `require_amount=False` is for lookup paths such as `find`, where the caller
    only needs the merchant/category/date signals and has no amount to give.
    """
    raw_text = text.strip()
    if not raw_text:
        raise SystemExit("Text is required")
    raw_text = apply_aliases_to_text(ledger, raw_text)
    if ledger is not None:
        seed_habits_if_needed(ledger)
    date_token = extract_date_token(raw_text)
    date_text_removed = raw_text.replace(date_token, " ", 1) if date_token else raw_text
    amount, amount_removed = extract_amount(date_text_removed)
    decision = classify_event_detailed(raw_text)
    tx_type = decision["type"]
    event_category = decision["category"]
    event_flags = decision["flags"]
    merchant = infer_merchant(amount_removed, infer_category(raw_text, tx_type), raw_text)
    category = preferred_category(ledger, merchant, event_category or infer_category(raw_text, tx_type))
    if tx_type == "expense" and re.search(r"(?:转账-?转给|转账给|转给)", raw_text):
        category = "人情往来"
    method = infer_method(raw_text)
    account = infer_account(raw_text, method)
    tags = infer_tags(raw_text)
    habit = match_habit(ledger, raw_text, merchant)
    amount_from_habit = False
    if amount is None:
        can_fill = (
            habit
            and habit.get("amount") is not None
            and habit.get("source") == "manual"
            and habit.get("amount_locked", False)
        )
        if can_fill:
            amount = round(float(habit["amount"]), 2)
            amount_from_habit = True
        elif require_amount:
            raise SystemExit("Amount is required")
    if confidence is None:
        confidence = 1.0
        if category == "其他":
            confidence = 0.75
        if not merchant and category == "其他":
            confidence = 0.6
        if amount_from_habit:
            confidence = min(confidence, 0.9)
    proposal = {
        "type": tx_type,
        "amount": amount,
        "currency": default_currency(currency or (ledger or {}).get("currency")),
        "category": category,
        "occurred_at": parse_when(date_token),
        "source": source,
        "confidence": confidence,
        "tags": tags,
        "note": raw_text,
    }
    # Say why this event was read the way it was. Without this a user who meant
    # "a taxi fare my company will reimburse" gets silently booked as income.
    proposal["event"] = {
        "name": decision["event"],
        "confidence": decision["confidence"],
        "reasons": decision["reasons"],
    }
    if decision["reasons"]:
        last = decision["reasons"][-1]
        proposal["explained_by"] = f"{last['rule']}: {last['match']}"
    if merchant:
        proposal["merchant"] = merchant
    if method:
        proposal["method"] = method
    if account:
        proposal["account"] = account
        # Remember that the account came from the sentence, not from habit: a
        # stated account is a fact and must not be second-guessed later.
        proposal["account_from_text"] = True
    if event_flags.get("advance"):
        proposal["advance"] = "open"
    apply_habit_to_proposal(proposal, habit, amount_from_habit=amount_from_habit)
    apply_usage_defaults(proposal, ledger)
    return apply_account_fields(proposal, ledger)


def parse_text_transactions(text, ledger=None, currency="CNY", source="text"):
    raw_text = (text or "").strip()
    if not raw_text:
        raise SystemExit("Text is required")
    aliased = apply_aliases_to_text(ledger, raw_text)
    stacked = try_parse_stacked_rows(aliased)
    if stacked:
        items = stacked
        item_source = source if source != "text" else "import"
        confidence = 0.9
    else:
        items = split_text_items(aliased)
        item_source = source
        confidence = None
    return [
        parse_text_transaction(item, ledger=ledger, currency=currency, source=item_source, confidence=confidence)
        for item in items
    ]


def normalized_text(value):
    return re.sub(r"\s+", "", str(value or "")).lower()


def likely_duplicate_candidates(transactions, tx):
    candidates = []
    if not isinstance(tx, dict):
        return candidates
    tx_external_id = normalized_text(tx.get("wechat_transaction_id"))
    tx_amount = round(safe_amount(tx.get("amount")), 2)
    tx_merchant = normalized_text(tx.get("merchant"))
    tx_note = normalized_text(tx.get("note"))
    for existing in transactions:
        if not isinstance(existing, dict):
            continue
        existing_external_id = normalized_text(existing.get("wechat_transaction_id"))
        # Distinct authoritative statement references are separate source rows,
        # even when date, amount, and merchant happen to match.
        tx_statement_ref = normalized_text(tx.get("credit_statement_ref") or tx.get("bank_statement_ref"))
        existing_statement_ref = normalized_text(existing.get("credit_statement_ref") or existing.get("bank_statement_ref"))
        if tx_statement_ref and existing_statement_ref and tx_statement_ref != existing_statement_ref:
            continue
        if tx_external_id and existing_external_id:
            if tx_external_id == existing_external_id:
                candidates.append(compact_candidate(existing))
            continue
        if tx.get("type") and existing.get("type") and tx.get("type") != existing.get("type"):
            continue
        existing_amount = round(safe_amount(existing.get("amount")), 2)
        if existing_amount != tx_amount:
            continue
        if tx_day(existing) != tx_day(tx):
            continue
        existing_merchant = normalized_text(existing.get("merchant"))
        existing_note = normalized_text(existing.get("note"))
        same_merchant = tx_merchant and existing_merchant and tx_merchant == existing_merchant
        same_note = tx_note and existing_note and (tx_note == existing_note or tx_note in existing_note or existing_note in tx_note)
        same_attachment = tx.get("attachment") and tx.get("attachment") == existing.get("attachment")
        if same_merchant or same_note or same_attachment:
            candidates.append(compact_candidate(existing))
    return candidates


def merge_overrides(proposal, args):
    merged = dict(proposal)
    if args.type:
        merged["type"] = args.type
    if args.category:
        merged["category"] = args.category
    if args.merchant is not None:
        merged["merchant"] = args.merchant
    if args.note is not None:
        merged["note"] = args.note
    if args.method:
        merged["method"] = args.method
    if args.account is not None:
        merged["account"] = args.account
    if getattr(args, "related_transaction_id", None) is not None:
        merged["related_transaction_id"] = args.related_transaction_id
    if getattr(args, "relation", None) is not None:
        merged["relation"] = args.relation
    if getattr(args, "to_account", None):
        merged["to_account"] = args.to_account
    if args.tags:
        merged["tags"] = unique_tags(merged.get("tags"), args.tags)
    if args.date or args.occurred_at:
        merged["occurred_at"] = parse_when(args.date or args.occurred_at)
    if args.amount is not None:
        merged["amount"] = args.amount
    if args.source:
        merged["source"] = args.source
    if args.confidence is not None:
        merged["confidence"] = args.confidence
    if getattr(args, "exclude_from_totals", False) or getattr(args, "not_mine", False):
        merged["excluded_from_totals"] = True
    if getattr(args, "include_in_totals", False):
        merged.pop("excluded_from_totals", None)
    if getattr(args, "advance", None):
        merged["advance"] = args.advance
    elif getattr(args, "not_mine", False):
        # Fronting money for someone else is not a receivable in the ledger's
        # sense: it is simply not the user's spending, so it must not also be
        # listed as an open advance.
        merged.pop("advance", None)
    return merged
