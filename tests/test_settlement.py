"""
이커머스 정산 시스템 - pytest 테스트 스위트
[AI 활용 CI/CD 교육] Day 1 · Part 4

실행:
  pytest tests/ -v --cov=settlement --cov-report=term-missing

AI 활용 포인트:
  이 파일을 Claude.ai에 붙여넣고 "테스트 케이스를 보강해줘" 라고 물어보세요
"""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from settlement.main import app
from settlement.models.models import Order, OrderStatus, SettlementStatus
from settlement.services.settlement_service import SettlementService

# ── 픽스처 ────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    # context manager로 사용해야 FastAPI lifespan(시작/종료 이벤트)이
    # 실제로 실행됩니다. 앱 시작 시 _seed_sample_data()가 호출되어
    # M-001, M-002 샘플 주문이 생성됩니다.
    with TestClient(app) as c:
        yield c


@pytest.fixture
def svc():
    return SettlementService()


@pytest.fixture
def sample_order():
    return Order(
        order_id=f"TEST-{uuid.uuid4().hex[:6]}",
        merchant_id="M-TEST",
        customer_id="C-001",
        amount=Decimal("100000"),
        fee_rate=Decimal("0.03"),
    )


# ── 모델 단위 테스트 ──────────────────────────────────────────────────


class TestOrderModel:
    def test_fee_amount(self):
        """수수료 3% 계산"""
        o = Order(order_id="T1", merchant_id="M", customer_id="C", amount=Decimal("100000"))
        assert o.fee_amount == Decimal("3000")  # 100,000 × 3%

    def test_net_amount(self):
        """실 정산액 = 매출 - 수수료"""
        o = Order(order_id="T2", merchant_id="M", customer_id="C", amount=Decimal("100000"))
        assert o.net_amount == Decimal("97000")

    def test_default_status_pending(self):
        o = Order(order_id="T3", merchant_id="M", customer_id="C", amount=Decimal("50000"))
        assert o.status == OrderStatus.PENDING

    def test_negative_amount_raises(self):
        with pytest.raises(Exception):
            Order(order_id="T4", merchant_id="M", customer_id="C", amount=Decimal("-1"))

    def test_fee_rounding(self):
        """소수점 수수료 반올림 (원 단위)"""
        o = Order(
            order_id="T5",
            merchant_id="M",
            customer_id="C",
            amount=Decimal("33333"),
            fee_rate=Decimal("0.03"),
        )
        # 33333 × 0.03 = 999.99 → 1000 (반올림)
        assert o.fee_amount == Decimal("1000")


# ── 서비스 단위 테스트 ────────────────────────────────────────────────


class TestSettlementService:
    def test_add_and_complete_order(self, svc, sample_order):
        svc.add_order(sample_order)
        done = svc.complete_order(sample_order.order_id)
        assert done is not None
        assert done.status == OrderStatus.COMPLETED
        assert done.completed_at is not None

    def test_complete_nonexistent_returns_none(self, svc):
        assert svc.complete_order("NONE-EXIST") is None

    def test_calculate_settlement_basic(self, svc):
        """3건 주문 정산 계산 기본 케이스"""
        merchant = "M-CALC"
        amounts = [Decimal("50000"), Decimal("100000"), Decimal("200000")]
        for i, amt in enumerate(amounts):
            o = Order(order_id=f"O-{i}", merchant_id=merchant, customer_id="C", amount=amt)
            svc.add_order(o)
            svc.complete_order(o.order_id)

        start = datetime.utcnow() - timedelta(hours=1)
        end = datetime.utcnow() + timedelta(hours=1)
        rec = svc.calculate_settlement(merchant, start, end)

        expected_sales = sum(amounts)
        expected_fee = sum(a * Decimal("0.03") for a in amounts)

        assert rec.order_count == 3
        assert rec.total_sales == expected_sales
        # 정수 비교 (양쪽 모두 quantize 결과)
        assert rec.total_fee.quantize(Decimal("1")) == expected_fee.quantize(Decimal("1"))
        assert rec.net_amount == expected_sales - rec.total_fee
        assert rec.status == SettlementStatus.PENDING

    def test_pending_orders_excluded(self, svc):
        """PENDING 상태 주문은 정산 제외"""
        o = Order(order_id="PEND-1", merchant_id="M-X", customer_id="C", amount=Decimal("100000"))
        svc.add_order(o)  # 완료 처리 안 함

        start = datetime.utcnow() - timedelta(hours=1)
        end = datetime.utcnow() + timedelta(hours=1)
        rec = svc.calculate_settlement("M-X", start, end)

        assert rec.order_count == 0
        assert rec.total_sales == Decimal("0")

    def test_process_settlement(self, svc, sample_order):
        svc.add_order(sample_order)
        svc.complete_order(sample_order.order_id)

        rec = svc.calculate_settlement(
            "M-TEST",
            datetime.utcnow() - timedelta(hours=1),
            datetime.utcnow() + timedelta(hours=1),
        )
        done = svc.process_settlement(rec.settlement_id)

        assert done.status == SettlementStatus.COMPLETED
        assert done.processed_at is not None

    def test_list_settlements_filter(self, svc):
        """merchant_id 필터 동작 확인"""
        for m in ["M-A", "M-B"]:
            o = Order(order_id=f"O-{m}", merchant_id=m, customer_id="C", amount=Decimal("10000"))
            svc.add_order(o)
            svc.complete_order(o.order_id)
            svc.calculate_settlement(
                m,
                datetime.utcnow() - timedelta(hours=1),
                datetime.utcnow() + timedelta(hours=1),
            )

        result = svc.list_settlements(merchant_id="M-A")
        assert all(r.merchant_id == "M-A" for r in result)

    def test_list_settlements_combined_filter(self, svc):
        """merchant_id + status 동시 필터: 교집합만 정확히 반환되어야 한다"""
        # M-COMBO-A: 정산까지 처리 완료 (COMPLETED)
        o1 = Order(
            order_id=f"O-{uuid.uuid4().hex[:6]}",
            merchant_id="M-COMBO-A",
            customer_id="C",
            amount=Decimal("10000"),
        )
        svc.add_order(o1)
        svc.complete_order(o1.order_id)
        rec_completed = svc.calculate_settlement(
            "M-COMBO-A",
            datetime.utcnow() - timedelta(hours=1),
            datetime.utcnow() + timedelta(hours=1),
        )
        svc.process_settlement(rec_completed.settlement_id)

        # M-COMBO-A: 정산 레코드는 있지만 처리(process)는 하지 않은 PENDING 건
        rec_pending = svc.calculate_settlement(
            "M-COMBO-A",
            datetime.utcnow() - timedelta(hours=1),
            datetime.utcnow() + timedelta(hours=1),
        )

        # M-COMBO-B: 다른 판매자의 COMPLETED 정산 (필터에 섞여 들어오면 안 됨)
        o2 = Order(
            order_id=f"O-{uuid.uuid4().hex[:6]}",
            merchant_id="M-COMBO-B",
            customer_id="C",
            amount=Decimal("20000"),
        )
        svc.add_order(o2)
        svc.complete_order(o2.order_id)
        rec_other = svc.calculate_settlement(
            "M-COMBO-B",
            datetime.utcnow() - timedelta(hours=1),
            datetime.utcnow() + timedelta(hours=1),
        )
        svc.process_settlement(rec_other.settlement_id)

        result = svc.list_settlements(merchant_id="M-COMBO-A", status=SettlementStatus.COMPLETED)

        assert len(result) == 1
        assert result[0].settlement_id == rec_completed.settlement_id
        assert result[0].merchant_id == "M-COMBO-A"
        assert result[0].status == SettlementStatus.COMPLETED
        # 같은 판매자의 PENDING 건은 결과에 섞이지 않아야 한다
        assert rec_pending.settlement_id not in [r.settlement_id for r in result]

    def test_process_settlement_unknown_id_returns_none(self, svc):
        """존재하지 않는 settlement_id로 조회하면 None을 반환해야 한다"""
        assert svc.process_settlement("STL-DOES-NOT-EXIST") is None
        assert svc.list_settlements() == []

    def test_calculate_settlement_no_orders_at_all(self, svc):
        """주문이 하나도 없는 상태에서 정산 계산 시 0건으로 처리되어야 한다"""
        rec = svc.calculate_settlement(
            "M-EMPTY",
            datetime.utcnow() - timedelta(days=30),
            datetime.utcnow(),
        )
        assert rec.order_count == 0
        assert rec.total_sales == Decimal("0")
        assert rec.total_fee == Decimal("0")
        assert rec.net_amount == Decimal("0")
        assert rec.status == SettlementStatus.PENDING

    def test_calculate_settlement_orders_outside_period_excluded(self, svc):
        """정산 기간 밖에서 완료된 주문은 대상에서 제외되어 0건이어야 한다"""
        o = Order(
            order_id=f"O-{uuid.uuid4().hex[:6]}",
            merchant_id="M-OUT",
            customer_id="C",
            amount=Decimal("100000"),
        )
        svc.add_order(o)
        svc.complete_order(o.order_id)
        # 완료 시각을 정산 기간 밖(1년 전)으로 강제 이동
        o.completed_at = datetime.utcnow() - timedelta(days=365)

        rec = svc.calculate_settlement(
            "M-OUT",
            datetime.utcnow() - timedelta(hours=1),
            datetime.utcnow() + timedelta(hours=1),
        )
        assert rec.order_count == 0
        assert rec.total_sales == Decimal("0")


# ── API 통합 테스트 ───────────────────────────────────────────────────


class TestAPI:
    def test_health(self, client):
        res = client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

    def test_ready(self, client):
        res = client.get("/ready")
        assert res.status_code == 200

    def test_create_order(self, client):
        payload = {
            "order_id": f"API-{uuid.uuid4().hex[:6]}",
            "merchant_id": "M-API",
            "customer_id": "C-001",
            "amount": "75000",
            "fee_rate": "0.03",
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
        }
        res = client.post("/api/v1/orders", json=payload)
        assert res.status_code == 201
        assert res.json()["order_id"] == payload["order_id"]

    def test_list_settlements(self, client):
        res = client.get("/api/v1/settlements")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_list_settlements_filter(self, client):
        res = client.get("/api/v1/settlements?merchant_id=M-001")
        assert res.status_code == 200

    def test_complete_order_not_found_api(self, client):
        """존재하지 않는 주문을 완료 처리하면 404를 반환해야 한다"""
        res = client.put("/api/v1/orders/NON-EXIST-ORDER/complete")
        assert res.status_code == 404
        assert "NON-EXIST-ORDER" in res.json()["detail"]

    def test_list_orders(self, client):
        """주문 목록 조회 (필터 없음)"""
        res = client.get("/api/v1/orders")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_list_orders_filtered_by_merchant(self, client):
        """merchant_id로 필터링된 주문 목록 조회"""
        # lifespan 시작 시 시딩되는 M-001 샘플 주문을 이용
        res = client.get("/api/v1/orders?merchant_id=M-001")
        assert res.status_code == 200
        body = res.json()
        assert all(o["merchant_id"] == "M-001" for o in body)

    def test_create_settlement(self, client):
        """정산 생성 API: 유효한 요청으로 201과 정산 레코드가 반환되어야 한다"""
        payload = {
            "merchant_id": "M-001",
            "period_start": (datetime.utcnow() - timedelta(days=10)).isoformat(),
            "period_end": (datetime.utcnow() + timedelta(days=1)).isoformat(),
        }
        res = client.post("/api/v1/settlements", json=payload)
        assert res.status_code == 201
        body = res.json()
        assert body["merchant_id"] == "M-001"
        assert "settlement_id" in body

    def test_process_settlement_not_found_api(self, client):
        """존재하지 않는 정산 ID로 처리 요청 시 404를 반환해야 한다"""
        res = client.post("/api/v1/settlements/STL-NONEXISTENT/process")
        assert res.status_code == 404
        assert "STL-NONEXISTENT" in res.json()["detail"]
