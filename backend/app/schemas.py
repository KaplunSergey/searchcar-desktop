from typing import Literal
from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

PRICE_FILTER_MODES = ("LINK", "CUSTOM")
MAX_PROJECT_PRICE_KRW = 100_000_000


def validate_price_filter(
    mode: str,
    minimum: int | None,
    maximum: int | None,
) -> None:
    if mode not in PRICE_FILTER_MODES:
        raise ValueError("unsupported_price_filter_mode")
    if mode != "CUSTOM":
        if minimum is not None or maximum is not None:
            raise ValueError("price_bounds_require_custom_mode")
        return
    if minimum is None and maximum is None:
        raise ValueError("custom_price_range_requires_a_bound")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("invalid_price_range")
    for value in (minimum, maximum):
        if value is not None and value % 10_000:
            raise ValueError("price_must_use_10000_krw_steps")

class LoginIn(BaseModel):
    username:str=Field(min_length=1,max_length=64)
    password:str=Field(min_length=8,max_length=128)
class PasswordChangeIn(BaseModel):
    current_password:str=Field(min_length=8,max_length=128)
    new_password:str=Field(min_length=8,max_length=128)
class ProfilePatch(BaseModel):
    preferred_locale:Literal["ru","uk"]
class RegistrationIn(BaseModel):
    username:str=Field(min_length=3,max_length=64)
    password:str=Field(min_length=8,max_length=128)
class AdminUserIn(BaseModel):
    username:str=Field(min_length=3,max_length=64)
    password:str=Field(min_length=8,max_length=128)
    role:Literal["ADMIN","USER"]="USER"
    status:Literal["ACTIVE","BLOCKED","PENDING_APPROVAL"]="ACTIVE"
    project_limit:int|None=Field(default=1,ge=0)
    must_change_password:bool=True
class AdminUserPatch(BaseModel):
    username:str|None=Field(default=None,min_length=3,max_length=64)
    role:Literal["ADMIN","USER"]|None=None
    status:Literal["ACTIVE","BLOCKED","PENDING_APPROVAL"]|None=None
    project_limit:int|None=Field(default=None,ge=0)
    must_change_password:bool|None=None
class AdminPasswordResetIn(BaseModel):
    password:str=Field(min_length=8,max_length=128)
    must_change_password:bool=True

class ProjectIn(BaseModel):
    name: str = Field(min_length=1,max_length=140)
    search_url: str
    telegram_url: str|None=None
    scan_mode: Literal["FAST","ACCURATE"]="FAST"
    search_page_mode: Literal["FIRST_PAGE","ALL_PAGES"]="ALL_PAGES"
    price_filter_mode: Literal["LINK", "CUSTOM"]="LINK"
    price_min_krw: int|None=Field(default=None,ge=0,le=MAX_PROJECT_PRICE_KRW)
    price_max_krw: int|None=Field(default=None,ge=0,le=MAX_PROJECT_PRICE_KRW)
    auto_update: bool=True
    @model_validator(mode="after")
    def price_filter_is_valid(self):
        validate_price_filter(
            self.price_filter_mode,
            self.price_min_krw,
            self.price_max_krw,
        )
        return self
    @field_validator("search_url")
    @classmethod
    def encar_url(cls,v):
        from urllib.parse import urlparse
        host=(urlparse(v).hostname or "").lower()
        if host not in {"encar.com","www.encar.com","fem.encar.com","m.encar.com"} and not host.endswith(".encar.com"):
            raise ValueError("unsupported_encar_domain")
        return v
    @field_validator("telegram_url")
    @classmethod
    def telegram_channel_url(cls,v):
        if not v:
            return None
        from urllib.parse import urlparse
        value=v.strip()
        parsed=urlparse(value)
        if parsed.scheme not in {"http","https"} or (parsed.hostname or "").lower() not in {
            "t.me","www.t.me","telegram.me","www.telegram.me"
        }:
            raise ValueError("unsupported_telegram_domain")
        return value
class ProjectPatch(BaseModel):
    name:str|None=None; search_url:str|None=None; telegram_url:str|None=None; scan_mode:Literal["FAST","ACCURATE"]|None=None; search_page_mode:Literal["FIRST_PAGE","ALL_PAGES"]|None=None; price_filter_mode:Literal["LINK", "CUSTOM"]|None=None; price_min_krw:int|None=Field(default=None,ge=0,le=MAX_PROJECT_PRICE_KRW); price_max_krw:int|None=Field(default=None,ge=0,le=MAX_PROJECT_PRICE_KRW); auto_update:bool|None=None
    @field_validator("search_url")
    @classmethod
    def encar_url(cls,v):
        return ProjectIn.encar_url(v)
    @field_validator("telegram_url")
    @classmethod
    def telegram_channel_url(cls,v):
        return ProjectIn.telegram_channel_url(v)
class BoolPatch(BaseModel): value:bool
class CommentPatch(BaseModel): comment:str
class RatingPatch(BaseModel): rating:int|None=Field(default=None,ge=1,le=5)
class ScanProjectsIn(BaseModel): project_ids:list[int]
class ScanCarsIn(BaseModel): car_ids:list[int]; project_id:int|None=None
class SchedulerIn(BaseModel):
    enabled:bool; paused:bool=False; interval_minutes:Literal[60,180,360,720,1440]; project_ids:list[int]

class DesktopMigrationIn(BaseModel):
    source_database_url:str=Field(min_length=8,max_length=2048)
    source_storage_path:str=Field(min_length=1,max_length=2048)

class ExternalUrlIn(BaseModel):
    url:str=Field(min_length=8,max_length=2048)

class DesktopLicenseTrialIn(BaseModel):
    subject:str=Field(min_length=3,max_length=254)

class DesktopLicenseRedeemIn(BaseModel):
    activation_code:str=Field(min_length=10,max_length=40)

class DesktopOnboardingActivateIn(DesktopLicenseRedeemIn):
    preferred_locale:Literal["ru","uk"]="ru"

class DesktopLicenseTransferClaimIn(BaseModel):
    transfer_code: str = Field(min_length=10, max_length=40)
    claim_token: str = Field(min_length=32, max_length=64)


class DesktopOnboardingTransferClaimIn(DesktopLicenseTransferClaimIn):
    preferred_locale: Literal["ru", "uk"] = "ru"
