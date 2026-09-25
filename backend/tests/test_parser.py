from app.parser import *
import pytest

from app.scanner import (
    IdentityMismatchError,
    is_sold_page,
    listing_sections,
    parse_list_row,
    resolve_listing_identity,
)
def test_ids(): assert extract_car_id("/cars/detail/38920017")=="38920017" and extract_real_encar_id("등록번호 39001111")=="39001111"
def test_sold_page_marker():
    assert is_sold_page("이 차량은 판매되었거나 삭제된 차량입니다.")
    assert not is_sold_page("현재 판매 중인 차량입니다.")
def test_parsers():
    assert parse_price_krw("3,190만원")==31_900_000
    assert parse_detail_price_krw("추천 3,600만원\n알림등록\n2,630만원\n총비용계산기")==26_300_000
    assert parse_detail_price_krw("2,650만원(계약중)\n총비용계산기")==26_500_000
    assert parse_detail_price_krw("2,650만원\n계약중\n엔카\n기본정보")==26_500_000
    assert parse_detail_price_krw("동급매물\n2,960만원\n알림등록") is None
    assert parse_contract_status("2,650만원(계약중)\n총비용계산기") is True
    assert parse_contract_status("동급매물\n2,960만원\n계약중\n2,650만원\n총비용계산기") is False
    assert parse_new_car_price_percent("신차대비 52%") == 52
    assert parse_contract_status("2,650만원 (계약중)") is True
    assert parse_contract_status("2,650만원") is False
    assert parse_mileage_km("52,100 km")==52_100 and parse_year_month("21/11식")=="2021/11"
    assert detect_fuel("연료\n가솔린\n전기 시트") == "GASOLINE"


def test_rental_offer_parses_list_and_detail_terms():
    list_text = """현대 쏘나타 디 엣지(DN8) 1.6 터보 S
26/07식 · 1km · 가솔린 · 서울
월 36만원/24개월
렌트
"""
    detail_text = """월36만원
월렌트료(24개월)
인수금
0만원
차량가격
864 만원
"""

    assert parse_rental_terms(list_text) == {
        "offer_type": "RENT",
        "rental_monthly_payment_krw": 360_000,
        "rental_term_months": 24,
        "rental_acquisition_price_krw": None,
        "vehicle_price_krw": None,
    }
    assert parse_rental_terms(detail_text) == {
        "offer_type": "RENT",
        "rental_monthly_payment_krw": 360_000,
        "rental_term_months": 24,
        "rental_acquisition_price_krw": 0,
        "vehicle_price_krw": 8_640_000,
    }
    assert parse_list_row({"list_text": list_text})["price_krw"] == 360_000
    assert parse_list_row({"list_text": list_text})["offer_type"] == "RENT"


def test_monthly_finance_hint_is_not_mistaken_for_a_rental_offer():
    text = "예상 월 36만원\n할부 계산\n2,630만원\n총비용계산기"
    assert parse_rental_terms(text) is None


def test_lease_offer_parses_visible_and_embedded_encar_formats():
    visible = "월 31만원/22개월\n리스"
    embedded = '''"leaseRentType":"LEASE","advertisement":{"price":1480,
    "leaseRentInfo":{"residualMonth":22,"monthlyFee":31}}'''
    expected = {
        "offer_type": "LEASE",
        "lease_monthly_payment_krw": 310_000,
        "lease_term_months": 22,
    }

    assert parse_lease_terms(visible) == expected
    assert parse_lease_terms(embedded) == expected
    assert parse_list_row({"list_text": visible})["price_krw"] == 310_000
    assert parse_list_row({"list_text": visible})["offer_type"] == "LEASE"
def test_detection():
    assert detect_fuel("가솔린+전기")=="HYBRID" and detect_drivetrain("사륜")=="AWD"
    assert classify_accident("무사고 확인\n내차 피해\n없음\n타차 가해\n없음")["summary"]=="NO_PROBLEMS_STATED"
    assert classify_accident("내차 피해\n총 889,520원 (1회)")["summary"]=="INSURANCE_CLAIM"
def test_price_and_fast(): assert price_change(30_000_000,29_000_000)["type"]=="PRICE_DROP" and should_read_detail("FAST",is_new=True)


def test_fast_scan_uses_price_status_reads_without_reloading_full_profile():
    assert detail_read_kind("FAST", is_new=False, list_price_changed=False) is None
    assert detail_read_kind("FAST", is_new=False, list_price_changed=True) == "PRICE_STATUS"
    assert detail_read_kind("FAST", is_new=False, missing_from_search=True) == "PRICE_STATUS"
    assert detail_read_kind("FAST", is_new=True) == "FULL"
    assert detail_read_kind("ACCURATE", is_new=False) == "FULL"
def test_missing(): 
    count,status=apply_missing(True,False,0,"FOUND"); assert (count,status)==(1,"NOT_FOUND_IN_SEARCH")
    count,status=apply_missing(True,False,count,status); assert (count,status)==(2,"NOT_FOUND_IN_SEARCH")
    count,status=apply_missing(True,False,count,status); assert (count,status)==(3,"NOT_FOUND_IN_SEARCH")
    assert apply_missing(False,False,count,status)==(3,"NOT_FOUND_IN_SEARCH")
    assert apply_missing(True,True,3,status)==(0,"RELISTED")
def test_alias_merge(): assert merge_aliases([{"car_id":"1","canonical_car_id":"2","source_car_id":"1"},{"car_id":"2","canonical_car_id":"2"}])[0]["aliases"]==["1"]


def test_listing_identity_accepts_distinct_url_and_registration_ids():
    assert resolve_listing_identity(
        "42319346",
        "등록번호 42318013",
        resolved_url="https://fem.encar.com/cars/detail/42319346",
    ) == ("42319346", "42318013")


def test_listing_identity_rejects_redirect_to_another_car():
    with pytest.raises(IdentityMismatchError) as error:
        resolve_listing_identity(
            "42178983",
            "등록번호 42170001",
            resolved_url="https://fem.encar.com/cars/detail/42172299",
        )
    assert error.value.requested_id == "42178983"
    assert error.value.resolved_id == "42172299"


def test_sold_page_keeps_requested_identity_even_with_recommendation():
    assert resolve_listing_identity(
        "41093659",
        "이 차량은 판매되었거나 삭제된 차량입니다\n등록번호 99999999",
        sold=True,
        resolved_url="https://fem.encar.com/cars/detail/99999999",
    ) == ("41093659", "99999999")

def test_vehicle_and_condition_fields():
    text="""더 뉴 투싼
25/02식
차량번호
219주1935
변속기
자동
색 상 : 쥐색
교환
없음
판금
없음
프레임 진단
정상
성능점검을 등록한 차량이에요
내차 피해
없음
타차 가해
총 3,965,235원 (2회)
"""
    fields=parse_vehicle_fields(text,"더 뉴 투싼 전북 중고차 : 내차팔기·내차사기")
    assert fields["registration_number"]=="219주1935"
    assert fields["transmission"]=="자동"
    condition,summary=parse_condition(text)
    assert summary=="INSURANCE_CLAIM"
    assert condition["third_party_payments"]["value"]==3_965_235
    assert condition["replaced_panels"]["status"]=="NOT_FOUND"


def test_option_variants_from_real_encar_descriptions():
    for text in (
        "운전석 전동시트(8way, 자세 메모리 시스템)",
        "- 앞좌석메모리/통풍시트 -",
        "전동 + 메모리 시트",
        "운전석 메모리시트",
    ):
        assert parse_options(text)["memory_seat"] is True
    assert parse_options("메모리 시트 없음")["memory_seat"] is False
    assert parse_options("천연가죽 시트")["leather_seat"] is True
    assert parse_options("옵션 정보가 아직 로딩 중입니다") == {}


def test_listing_sections_exclude_seller_description_and_similar_cars():
    body = """기본정보
연료
가솔린
차량 상태
무사고 확인
프레임 진단
정상
외부패널 진단
정상
성능점검을 등록한 차량이에요
교환
없음
판금
없음
보증 현황
판매자 정보
침수 유무를 보증합니다
렌트 이력 없는 차량입니다
동급매물
전기
계약중
"""
    main, _, condition_text = listing_sections(body)
    condition, summary = parse_condition(condition_text)
    assert "침수 유무" not in main
    assert condition["flood_history"]["status"] == "UNVERIFIED"
    assert condition["rental_use"]["status"] == "UNVERIFIED"
    assert summary == "NO_PROBLEMS_STATED"


def test_fingerprint_and_material_changes_ignore_evidence_only_updates():
    old = {
        "canonical_car_id": "1",
        "condition": {
            "inspection_report": {
                "status": "CONFIRMED",
                "original_evidence": ["короткое доказательство"],
            }
        },
    }
    new = {
        **old,
        "condition": {
            "inspection_report": {
                "status": "CONFIRMED",
                "original_evidence": ["более длинное доказательство"],
            }
        },
    }
    assert fingerprint_listing(old) == fingerprint_listing(new)
    assert material_changes(old, new) == []
    assert accident_signature(
        {
            "status": "CONFIRMED",
            "summary": "INSURANCE_CLAIM",
            "condition": old["condition"],
            "original_evidence": ["короткое доказательство"],
        }
    ) == accident_signature(
        {
            "status": "CONFIRMED",
            "summary": "INSURANCE_CLAIM",
            "condition": new["condition"],
            "original_evidence": ["другое доказательство"],
        }
    )
