from __future__ import annotations

from fastapi import Request, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.main import activate_desktop_workspace, desktop_onboarding_status
from app.schemas import DesktopOnboardingActivateIn


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/desktop/onboarding/activate",
            "headers": [(b"origin", b"http://localhost:3000")],
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
            "server": ("localhost", 8000),
            "query_string": b"",
        }
    )


def test_desktop_activation_creates_passwordless_workspace_and_session(
    tmp_path, monkeypatch
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setenv("SEARCHCAR_DESKTOP_DATA_DIR", str(tmp_path))
    calls: list[str] = []

    def redeemed(action, operation):
        calls.append(action)
        return {"status": "active"}

    monkeypatch.setattr("app.main._desktop_license_operation", redeemed)
    with Session(engine, expire_on_commit=False) as db:
        assert desktop_onboarding_status(db) == {"required": True}
        response = Response()
        result = activate_desktop_workspace(
            DesktopOnboardingActivateIn(
                activation_code="SC-ABCDE-FGHIJ-KLMNO-PQRST",
                preferred_locale="uk",
            ),
            _request(),
            response,
            db,
        )

        assert calls == ["desktop_onboarding_redeem"]
        assert result["user"]["username"] == "SearchCar"
        assert result["user"]["passwordless_workspace"] is True
        assert result["user"]["preferred_locale"] == "uk"
        assert result["csrf_token"]
        cookies = "\n".join(response.headers.getlist("set-cookie"))
        assert "encar_session=" in cookies
        assert "encar_csrf=" in cookies
        assert desktop_onboarding_status(db) == {"required": False}
