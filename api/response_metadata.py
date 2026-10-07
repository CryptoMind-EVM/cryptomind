def build_response_metadata(result: dict) -> dict:
    task_results = result.get("task_results", {}) if isinstance(result, dict) else {}
    used_tools = []
    quality_fail_reason = None
    data_as_of = None
    query_type = None
    resolved_market = None
    policy_path = None

    for task_result in task_results.values():
        if not isinstance(task_result, dict):
            continue
        task_data = task_result.get("data", {})
        if isinstance(task_data, dict):
            used_tools.extend(task_data.get("used_tools", []))
            data_as_of = data_as_of or task_data.get("data_as_of")
            query_type = query_type or task_data.get("query_type")
            resolved_market = resolved_market or task_data.get("resolved_market")
            policy_path = policy_path or task_data.get("policy_path")
        quality_fail_reason = quality_fail_reason or task_result.get(
            "quality_fail_reason"
        )

    return {
        "quality_fail_reason": quality_fail_reason,
        "used_tools": sorted({tool for tool in used_tools if tool}),
        "data_as_of": data_as_of,
        "query_type": query_type,
        "resolved_market": resolved_market,
        "policy_path": policy_path,
        # Trustworthy AI HITL 場景 B：詐騙判定證據鏈（claw_loop Phase D 附加）
        "scam_evidence": result.get("scam_evidence") if isinstance(result, dict) else None,
    }
