from api.response_metadata import build_response_metadata


def test_build_response_metadata_collects_tools_and_trace_fields():
    result = {
        "task_results": {
            "task_1": {
                "data": {
                    "used_tools": ["tw_stock_price"],
                    "data_as_of": "2026-03-13T10:00:00Z",
                    "query_type": "price_lookup",
                    "resolved_market": "tw",
                    "policy_path": "market_lookup",
                },
                "quality_fail_reason": None,
            }
        }
    }

    metadata = build_response_metadata(result)

    assert metadata["used_tools"] == ["tw_stock_price"]
    assert metadata["data_as_of"] == "2026-03-13T10:00:00Z"
    assert metadata["query_type"] == "price_lookup"
    assert metadata["resolved_market"] == "tw"
    assert metadata["policy_path"] == "market_lookup"
    assert metadata["quality_fail_reason"] is None


def test_build_response_metadata_collects_quality_fail_reason():
    result = {
        "task_results": {
            "task_1": {
                "data": {
                    "used_tools": [],
                },
                "quality_fail_reason": "verified_tool_unavailable",
            }
        }
    }

    metadata = build_response_metadata(result)

    assert metadata["quality_fail_reason"] == "verified_tool_unavailable"
    assert metadata["used_tools"] == []


def test_build_response_metadata_uses_first_available_trace_fields():
    result = {
        "task_results": {
            "task_1": {
                "data": {
                    "query_type": "price_lookup",
                    "resolved_market": "us",
                },
                "quality_fail_reason": None,
            },
            "task_2": {
                "data": {
                    "policy_path": "discovery_lookup",
                },
                "quality_fail_reason": None,
            },
        }
    }

    metadata = build_response_metadata(result)

    assert metadata["query_type"] == "price_lookup"
    assert metadata["resolved_market"] == "us"
    assert metadata["policy_path"] == "discovery_lookup"
