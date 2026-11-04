def review_reasons(review_policy, scope, results, strategic):
    reasons = []
    if review_policy.get("review_all"):
        reasons.append("Every estimate is reviewed before it goes to the AE")
    if strategic and review_policy.get("review_when_strategic", True):
        reasons.append("AE marked the deal as strategic")
    threshold = review_policy.get("fee_threshold_usd")
    if threshold is not None and results.price >= float(threshold):
        reasons.append(f"Price is at or above ${float(threshold):,.0f}")
    if scope.conflicts:
        reasons.append(f"{len(scope.conflicts)} answer(s) differed between the two reads")
    if scope.not_covered:
        reasons.append(f"{len(scope.not_covered)} sentence(s) of the requirement are not priced")
    if scope.custom_items and review_policy.get("review_when_custom_items", True):
        reasons.append("Includes custom items sized from the Item sizes table")
    reasons.extend(scope.flags)
    if (scope.value("Q12") or 0) > 10000:
        reasons.append("More than 10,000 SKUs; the template's item setup assumes up to 10,000")
    if scope.form_flags:
        reasons.append("Requirement includes areas that were not selected in the form")
    return reasons
