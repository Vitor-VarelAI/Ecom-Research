"""TDD slice 1 — canonical product identity (config/products.json).

The committed `config/products.json` is the source of truth once wired; this
suite pins the loader contract, the bootstrap content, and the read-only
guarantee.
"""

import json
from pathlib import Path

import pytest

from pipeline.products import (
    Product,
    ProductNotFoundError,
    ProductRegistry,
    ProductRegistryError,
    load_registry,
    normalize,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PRODUCTS = REPO_ROOT / "config" / "products.json"
CONFIG_KEYWORDS = REPO_ROOT / "config" / "keywords.txt"

EXPECTED_IDS = [
    "e942e8bf7f30",
    "be385ea0e349",
    "a16e2196eb4d",
    "ef5cd634db44",
    "258053734570",
]
EXPECTED_KEYWORDS = [
    "mochila personalizada",
    "camisola portugal",
    "caneca personalizada",
    "tapete yoga",
    "velas aromáticas",
]


def _write(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _valid_payload() -> dict:
    return {
        "schema_version": 1,
        "products": [
            {
                "product_id": "e942e8bf7f30",
                "name": "mochila personalizada",
                "research_keyword": "mochila personalizada",
                "aliases": [],
                "active": True,
            },
            {
                "product_id": "258053734570",
                "name": "velas aromáticas",
                "research_keyword": "velas aromáticas",
                "aliases": ["velas aromaticas", "velas"],
                "active": True,
            },
        ],
    }


# ── Vertical slice 1: valid load, active order, normalized resolution ──────


def test_normalize_strips_collapses_whitespace_folds_accents_and_case():
    assert normalize("  VELAS   AROMÁTICAS\t ") == "velas aromaticas"
    assert normalize("Caneca Personalizada") == "caneca personalizada"
    assert normalize("tapete  yoga") == "tapete yoga"


def test_committed_registry_loads_in_order_and_resolves_variants():
    registry = load_registry(CONFIG_PRODUCTS)
    assert [p.product_id for p in registry.products] == EXPECTED_IDS
    assert [p.product_id for p in registry.active_products()] == EXPECTED_IDS
    assert all(p.active for p in registry.products)

    assert registry.resolve("e942e8bf7f30").product_id == "e942e8bf7f30"
    assert registry.resolve("  MOCHILA   Personalizada ").name == "mochila personalizada"
    assert registry.resolve("camisola portugal").product_id == "be385ea0e349"
    assert registry.resolve("CANECA PERSONALIZADA").product_id == "a16e2196eb4d"
    assert registry.resolve("tapete  yoga").product_id == "ef5cd634db44"
    assert registry.resolve("VELAS AROMÁTICAS").product_id == "258053734570"


# ── Validation family: schema / shape ──────────────────────────────────────


def test_top_level_value_must_be_an_object(tmp_path):
    for payload in ([1, 2], "products", 42):
        with pytest.raises(ProductRegistryError):
            load_registry(_write(tmp_path / "products.json", payload))


def test_top_level_keys_must_be_exact(tmp_path):
    path = _write(tmp_path / "products.json", {"schema_version": 1, "products": [], "extra": 1})
    with pytest.raises(ProductRegistryError):
        load_registry(path)
    path = _write(tmp_path / "products.json", {"schema_version": 1})
    with pytest.raises(ProductRegistryError):
        load_registry(path)
    path = _write(tmp_path / "products.json", {"products": []})
    with pytest.raises(ProductRegistryError):
        load_registry(path)


def test_schema_version_must_be_1(tmp_path):
    path = _write(tmp_path / "products.json", {"schema_version": 2, "products": []})
    with pytest.raises(ProductRegistryError):
        load_registry(path)
    path = _write(tmp_path / "products.json", {"schema_version": "1", "products": []})
    with pytest.raises(ProductRegistryError):
        load_registry(path)


def test_products_must_be_a_list(tmp_path):
    path = _write(tmp_path / "products.json", {"schema_version": 1, "products": {"product_id": "x"}})
    with pytest.raises(ProductRegistryError):
        load_registry(path)


def test_each_product_must_be_an_object(tmp_path):
    payload = _valid_payload()
    payload["products"].append("mochila")
    with pytest.raises(ProductRegistryError):
        load_registry(_write(tmp_path / "products.json", payload))


def test_each_product_must_have_exact_keys(tmp_path):
    payload = _valid_payload()
    del payload["products"][0]["active"]
    with pytest.raises(ProductRegistryError):
        load_registry(_write(tmp_path / "products.json", payload))
    payload = _valid_payload()
    payload["products"][0]["extra"] = True
    with pytest.raises(ProductRegistryError):
        load_registry(_write(tmp_path / "products.json", payload))


# ── Validation family: duplicate product_id ────────────────────────────────


def test_duplicate_product_id_is_rejected(tmp_path):
    payload = _valid_payload()
    payload["products"].append(dict(payload["products"][0]))
    with pytest.raises(ProductRegistryError):
        load_registry(_write(tmp_path / "products.json", payload))


# ── Validation family: normalized token collisions ─────────────────────────


def test_normalized_token_cannot_map_to_two_products(tmp_path):
    payload = _valid_payload()
    payload["products"].append(
        {
            "product_id": "be385ea0e349",
            "name": "Mochila Personalizada",
            "research_keyword": "mochila premium",
            "aliases": [],
            "active": True,
        }
    )
    with pytest.raises(ProductRegistryError):
        load_registry(_write(tmp_path / "products.json", payload))


def test_name_vs_research_keyword_collision_across_products(tmp_path):
    payload = _valid_payload()
    payload["products"][1]["research_keyword"] = "MOCHILA PERSONALIZADA"
    with pytest.raises(ProductRegistryError):
        load_registry(_write(tmp_path / "products.json", payload))


def test_alias_collision_across_products(tmp_path):
    payload = _valid_payload()
    payload["products"][1]["aliases"] = ["mochila personalizada"]
    with pytest.raises(ProductRegistryError):
        load_registry(_write(tmp_path / "products.json", payload))


def test_repeated_equivalent_token_within_product_is_deduplicated(tmp_path):
    payload = _valid_payload()
    payload["products"][1]["aliases"] = [
        "VELAS AROMÁTICAS",
        "velas aromaticas",
        "velas aromáticas",
        "velas",
    ]
    registry = load_registry(_write(tmp_path / "products.json", payload))
    assert registry.resolve("velas aromaticas").product_id == "258053734570"
    assert registry.resolve("velas").product_id == "258053734570"


# ── Validation family: product_id format ───────────────────────────────────


def test_product_id_must_match_identifier_regex(tmp_path):
    bad_ids = ["1", "a!bcd", "ab cd", "é942e8bf7f30", "x" * 65]
    for bad in bad_ids:
        payload = _valid_payload()
        payload["products"][0]["product_id"] = bad
        with pytest.raises(ProductRegistryError):
            load_registry(_write(tmp_path / "products.json", payload))


def test_product_id_must_be_a_string(tmp_path):
    payload = _valid_payload()
    payload["products"][0]["product_id"] = 123456789012
    with pytest.raises(ProductRegistryError):
        load_registry(_write(tmp_path / "products.json", payload))


# ── Validation family: field types ─────────────────────────────────────────


def test_name_must_be_a_nonblank_string(tmp_path):
    for bad in ("", "   ", 42, None, ["mochila"]):
        payload = _valid_payload()
        payload["products"][0]["name"] = bad
        with pytest.raises(ProductRegistryError):
            load_registry(_write(tmp_path / "products.json", payload))


def test_research_keyword_must_be_a_nonblank_string(tmp_path):
    for bad in ("", "\t\n", 42, None):
        payload = _valid_payload()
        payload["products"][0]["research_keyword"] = bad
        with pytest.raises(ProductRegistryError):
            load_registry(_write(tmp_path / "products.json", payload))


def test_aliases_must_be_a_list_of_nonblank_strings(tmp_path):
    for bad in ("velas", 42, None, ["velas", 7], ["velas", "   "]):
        payload = _valid_payload()
        payload["products"][1]["aliases"] = bad
        with pytest.raises(ProductRegistryError):
            load_registry(_write(tmp_path / "products.json", payload))


def test_active_must_be_a_boolean(tmp_path):
    for bad in ("true", 1, 0, None):
        payload = _valid_payload()
        payload["products"][0]["active"] = bad
        with pytest.raises(ProductRegistryError):
            load_registry(_write(tmp_path / "products.json", payload))


# ── Validation family: unknown resolution ──────────────────────────────────


def test_resolve_unknown_token_raises_product_not_found():
    registry = load_registry(CONFIG_PRODUCTS)
    for token in ("nao existe", "be385ea0e348", "", "   ", None, 42):
        with pytest.raises(ProductNotFoundError):
            registry.resolve(token)


# ── Validation family: missing / invalid JSON, sanitized errors ────────────


def test_missing_file_raises_product_registry_error(tmp_path):
    with pytest.raises(ProductRegistryError):
        load_registry(tmp_path / "ghost.json")


def test_directory_as_registry_raises_product_registry_error(tmp_path):
    with pytest.raises(ProductRegistryError):
        load_registry(tmp_path)


def test_invalid_json_raises_and_does_not_leak_body(tmp_path):
    path = tmp_path / "products.json"
    path.write_text("{ not json SECRET_PAYLOAD_XYZ }", encoding="utf-8")
    with pytest.raises(ProductRegistryError) as exc:
        load_registry(path)
    assert "SECRET_PAYLOAD_XYZ" not in str(exc.value)


def test_shape_error_messages_do_not_leak_file_contents(tmp_path):
    path = _write(
        tmp_path / "products.json",
        {
            "schema_version": 1,
            "products": [
                {
                    "product_id": "aaabbb",
                    "name": "SENSITIVE_VALUE_123",
                    "research_keyword": "x",
                    "aliases": [],
                    "active": True,
                    "extra": "LEAK_ME_TOO",
                }
            ],
        },
    )
    with pytest.raises(ProductRegistryError) as exc:
        load_registry(path)
    message = str(exc.value)
    assert "SENSITIVE_VALUE_123" not in message
    assert "LEAK_ME_TOO" not in message


# ── Validation family: read-only loader, immutability, committed config ────


def test_load_never_modifies_the_registry_file(tmp_path):
    path = _write(tmp_path / "products.json", _valid_payload())
    before = path.read_bytes()
    load_registry(path)
    assert path.read_bytes() == before


def test_load_works_from_a_readonly_directory(tmp_path):
    ro_dir = tmp_path / "ro"
    ro_dir.mkdir()
    path = _write(ro_dir / "products.json", _valid_payload())
    ro_dir.chmod(0o500)
    try:
        registry = load_registry(path)
        assert registry.resolve("velas").product_id == "258053734570"
        assert path.read_bytes()  # still readable, never rewritten
    finally:
        ro_dir.chmod(0o700)


def test_missing_file_is_not_created(tmp_path):
    path = tmp_path / "ghost.json"
    with pytest.raises(ProductRegistryError):
        load_registry(path)
    assert not path.exists()


def test_product_is_frozen():
    registry = load_registry(CONFIG_PRODUCTS)
    product = registry.products[0]
    with pytest.raises(Exception):
        product.name = "changed"
    with pytest.raises(Exception):
        product.aliases = ("changed",)


def test_registry_is_immutable():
    registry = load_registry(CONFIG_PRODUCTS)
    with pytest.raises(AttributeError):
        registry.products = ()  # type: ignore[misc]
    with pytest.raises(AttributeError):
        registry.active_products = lambda: ()  # type: ignore[misc]
    with pytest.raises(Exception):
        registry.products[0].name = "changed"


def test_committed_products_json_has_exact_bootstrap_content():
    data = json.loads(CONFIG_PRODUCTS.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert [p["product_id"] for p in data["products"]] == EXPECTED_IDS
    assert [p["research_keyword"] for p in data["products"]] == EXPECTED_KEYWORDS
    for product in data["products"]:
        assert product["name"] == product["research_keyword"]
        assert product["active"] is True
    # Historical camisola variants consolidate as aliases so their watchlist
    # history maps onto the canonical product instead of dropping out.
    camisola = data["products"][1]
    assert camisola["aliases"] == [
        "camisolas portugal",
        "camisola de portugal",
        "camisola portugal preta",
    ]
    candles = data["products"][4]
    assert candles["aliases"] == ["velas", "velas perfumadas"]
    assert all(
        p["aliases"] == []
        for p in data["products"]
        if p["product_id"] not in {camisola["product_id"], candles["product_id"]}
    )


def test_load_does_not_change_keywords_txt_bytes():
    before = CONFIG_KEYWORDS.read_bytes()
    load_registry(CONFIG_PRODUCTS)
    assert CONFIG_KEYWORDS.read_bytes() == before
