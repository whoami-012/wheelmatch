from app.core.idempotency import canonical_request_hash


def test_canonical_request_hash_is_stable_across_mapping_order() -> None:
    first = canonical_request_hash(method="post", path="/v1/example", payload={"b": 2, "a": 1})
    second = canonical_request_hash(method="POST", path="/v1/example", payload={"a": 1, "b": 2})

    assert first == second


def test_canonical_request_hash_changes_with_payload() -> None:
    first = canonical_request_hash(method="POST", path="/v1/example", payload={"a": 1})
    second = canonical_request_hash(method="POST", path="/v1/example", payload={"a": 2})

    assert first != second
