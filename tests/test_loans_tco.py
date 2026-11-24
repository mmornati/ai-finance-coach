"""Tests for Asset Total Cost of Ownership (TCO) and cloud reference documents."""
from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from anhelpers import D, make_ds, tx
from coach.analytics.dataset import MemorySnapshot
from coach.loans import tco as TCO
from coach.memory import check as C, schemas
from coach.memory.store import MemoryStore
from memhelpers import make_world

TODAY = D("2026-10-05")


def make_tco_dataset():
    asset = schemas.Asset(
        id="car-novamobile",
        kind="vehicle",
        description="Test Electric Vehicle",
        purchase_price=35000.0,
        purchase_date=D("2024-01-01"),
    )
    liability = schemas.Liability(
        id="lease-novamobile",
        kind="lld",
        lender="Novabank",
        asset="car-novamobile",
        monthly_payment=450.0,
        first_payment=2000.0,
        term_months=36,
        start_date=D("2024-01-01"),
        end_date=D("2027-01-01"),
        payment_match="^NOVABANK LEASE",
        documents=["https://drive.google.com/file/d/test1/view"],
    )
    contract1 = schemas.Contract(
        id="ins-novamobile",
        kind="insurance_car",
        provider="SafeGuardInsurance",
        asset="car-novamobile",
        merchant_match="^SAFEGUARD ASSUR",
        billing=schemas.Billing(amount=120.0, period="monthly"),
        documents=["https://drive.google.com/file/d/test2/view"],
    )
    contract2 = schemas.Contract(
        id="serv-novamobile",
        kind="other",
        provider="ChargePass",
        asset="car-novamobile",
        merchant_match="^CHARGEPASS",
        billing=schemas.Billing(amount=240.0, period="yearly"),
    )
    txs = [
        # Financing payments matched by regex
        tx("2026-04-05", -450.0, entity="NOVABANK LEASE", desc="monthly lease", asset="car-novamobile"),
        tx("2026-05-05", -450.0, entity="NOVABANK LEASE", desc="monthly lease", asset="car-novamobile"),
        # Contract payments matched by regex
        tx("2026-05-02", -120.0, entity="SAFEGUARD ASSUR", desc="car insurance", asset="car-novamobile"),
        tx("2026-06-02", -120.0, entity="SAFEGUARD ASSUR", desc="car insurance", asset="car-novamobile"),
        # Operating expenses
        tx("2026-05-10", -65.0, category="transport.fuel", entity="TOTAL", desc="charging", asset="car-novamobile"),
        tx("2026-06-15", -55.0, category="transport.fuel", entity="IONITY", desc="charging", asset="car-novamobile"),
        tx("2026-07-20", -30.0, category="transport.toll", entity="SANEF", desc="toll", asset="car-novamobile"),
    ]
    mem = MemorySnapshot(
        assets=[asset],
        liabilities=[("liabilities/lease-novamobile.yaml", liability)],
        contracts=[
            ("contracts/ins-novamobile.yaml", contract1),
            ("contracts/serv-novamobile.yaml", contract2),
        ],
    )
    ds = make_ds(txs=txs, memory=mem, today=TODAY)
    return ds


def test_compute_asset_tco_aggregates_financing_contracts_and_expenses():
    ds = make_tco_dataset()
    res = TCO.compute_asset_tco(ds, "car-novamobile")

    assert res["asset_id"] == "car-novamobile"
    assert res["asset_kind"] == "vehicle"
    assert res["description"] == "Test Electric Vehicle"

    # Financing: 450/mo, first payment 2000, 36 months total = 2000 + 35 * 450 = 17750
    assert res["financing_monthly"] == "450.00"
    assert len(res["liabilities"]) == 1
    assert res["liabilities"][0]["total_financing"] == "17750.00"
    assert res["liabilities"][0]["documents"] == ["https://drive.google.com/file/d/test1/view"]

    # Contracts: 120/mo + 240/yr (20/mo) = 140/mo
    assert res["contracts_monthly"] == "140.00"
    assert len(res["contracts"]) == 2
    assert res["contracts"][0]["monthly"] == "120.00"
    assert res["contracts"][1]["monthly"] == "20.00"
    assert res["contracts"][0]["documents"] == ["https://drive.google.com/file/d/test2/view"]

    # Fixed monthly = 450 + 140 = 590
    assert res["fixed_monthly"] == "590.00"

    # Operating expenses: 65 + 55 + 30 = 150 total across 3 transactions
    # Note: lease and insurance debits are counted under financing/contracts, not operating expenses
    assert res["expenses_count"] == 3
    assert res["expenses_total"] == "150.00"
    assert "transport.fuel" in res["expenses_by_category"]
    assert "transport.toll" in res["expenses_by_category"]

    # Matched payments in bank data: 2 lease debits = 900, 2 insurance debits = 240
    assert res["financing_paid_to_date"] == "900.00"
    assert res["contracts_paid_to_date"] == "240.00"
    # Total spent to date: financing (900) + contracts (240) + operating (150) = 1290
    assert res["total_spent_to_date"] == "1290.00"

    # Total monthly includes average operating expenses
    assert float(res["total_monthly"]) >= 590.0
    # Projected total includes lifetime financing + contracts + operating
    assert res["projected_total"] is not None
    assert float(res["projected_total"]) > 17750.0
    assert res["projected_is_estimate"] is True


def test_compute_asset_tco_with_as_of_filters_payments():
    ds = make_tco_dataset()
    # As of 2026-05-01: only lease payment of 2026-04-05 has occurred
    res = TCO.compute_asset_tco(ds, "car-novamobile", as_of=D("2026-05-01"))
    assert res["financing_paid_to_date"] == "450.00"
    assert res["contracts_paid_to_date"] == "0.00"
    assert res["expenses_count"] == 0


def test_compute_asset_tco_unknown_financing_and_unknown_term():
    asset = schemas.Asset(
        id="car-novamobile",
        kind="vehicle",
        description="Test Electric Vehicle",
    )
    # Liability with no payment_match, no matching txs, and no schedule
    liability = schemas.Liability(
        id="lease-novamobile",
        kind="lld",
        lender="Novabank",
        asset="car-novamobile",
        monthly_payment=450.0,
        # No term_months!
    )
    from types import SimpleNamespace
    contract = schemas.Contract.model_construct(
        id="ins-novamobile",
        kind="insurance_car",
        provider="SafeGuardInsurance",
        asset="car-novamobile",
        billing=SimpleNamespace(amount=120.0, period="custom-unrecognised"),
        documents=[],
    )
    mem = MemorySnapshot(
        assets=[asset],
        liabilities=[("liabilities/lease-novamobile.yaml", liability)],
        contracts=[("contracts/ins-novamobile.yaml", contract)],
    )
    ds = make_ds(txs=[], memory=mem, today=TODAY)
    res = TCO.compute_asset_tco(ds, "car-novamobile")

    # Unknowns are listed, not guessed
    assert res["financing_paid_to_date"] is None
    assert res["total_spent_to_date"] is None
    assert res["projected_total"] is None
    assert res["contracts"][0]["monthly"] is None

    # Notes explain the unknowns
    notes = " ".join(res["notes"])
    assert "Financing paid-to-date for liability 'lease-novamobile' is unknown" in notes
    assert "unrecognised billing period 'custom-unrecognised'" in notes
    assert "Projected total is unknown (term is unknown; no assumed horizon is used)" in notes


def test_compute_asset_tco_contract_missing_period_is_unknown():
    asset = schemas.Asset(id="car-novamobile", kind="vehicle")
    from types import SimpleNamespace
    contract = schemas.Contract.model_construct(
        id="ins-novamobile",
        kind="insurance_car",
        provider="SafeGuardInsurance",
        asset="car-novamobile",
        billing=SimpleNamespace(amount=120.0, period=None),
        documents=[],
    )
    mem = MemorySnapshot(assets=[asset], liabilities=[], contracts=[("contracts/ins-novamobile.yaml", contract)])
    ds = make_ds(txs=[], memory=mem, today=TODAY)
    res = TCO.compute_asset_tco(ds, "car-novamobile")

    assert res["contracts"][0]["monthly"] is None
    assert res["contracts"][0]["period"] == "unknown"
    assert "missing billing period" in " ".join(res["notes"])


def test_compute_asset_tco_handles_missing_asset():
    ds = make_tco_dataset()
    res = TCO.compute_asset_tco(ds, "non-existent-asset")
    assert "error" in res
    assert "not found in memory" in res["error"]


def test_cloud_document_links_in_memory_check(cfg):
    make_world(cfg)
    store = MemoryStore(cfg.memory_dir)
    c_yaml = (
        "id: test-cloud-doc\n"
        "kind: other\n"
        "provider: CloudProvider\n"
        "documents:\n"
        "  - https://drive.google.com/file/d/xyz123/view\n"
    )
    (cfg.memory_dir / "contracts").mkdir(parents=True, exist_ok=True)
    (cfg.memory_dir / "contracts" / "test-cloud.yaml").write_text(c_yaml)

    issues = C.run_check(store, today=TODAY)
    doc_issues = [i for i in issues if "xyz123" in i.message or i.code in ("document_missing", "dangling_document")]
    assert not doc_issues


def test_document_schema_validation():
    # Valid https url alone passes
    d1 = schemas.Document(id="doc-url", added=TODAY, url="https://drive.google.com/file/d/123/view")
    assert d1.url == "https://drive.google.com/file/d/123/view"

    # Reject http:// url (must be https://)
    with pytest.raises(ValidationError) as exc:
        schemas.Document(id="doc-bad-url", added=TODAY, url="http://drive.google.com/file/d/123/view")
    assert "document url must start with https://" in str(exc.value)

    # Valid complete file fields alone pass
    d2 = schemas.Document(
        id="doc-file",
        added=TODAY,
        sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        filename="contract.pdf",
        stored_as="documents/contracts/contract.pdf",
        size=1024,
    )
    assert d2.filename == "contract.pdf"

    # Incomplete file fields without url fail
    with pytest.raises(ValidationError) as exc:
        schemas.Document(id="doc-incomplete", added=TODAY, filename="contract.pdf")
    assert "requires either a valid https:// url or complete file fields" in str(exc.value)

    # Neither url nor file fields fails
    with pytest.raises(ValidationError) as exc:
        schemas.Document(id="doc-empty", added=TODAY)
    assert "requires either a valid https:// url or complete file fields" in str(exc.value)
