from __future__ import annotations


def _looks_like_login_required(url: str, body_text: str) -> bool:
    if _is_login_url(url):
        return True
    login_score = sum(1 for term in _LOGIN_TERMS if term in body_text)
    business_score = sum(1 for term in _BUSINESS_TERMS if term in body_text)
    return login_score >= 2 and business_score <= 1


def _looks_like_verification_required(url: str, body_text: str) -> bool:
    normalized_url = url.lower()
    if any(token in normalized_url for token in ("captcha", "verify", "security", "risk")):
        return True
    return any(term in body_text for term in _VERIFICATION_TERMS)


def _is_ctrip_url(url: str) -> bool:
    return "ctrip.com" in url.lower()


def _is_login_url(url: str) -> bool:
    normalized = url.lower()
    return any(token in normalized for token in ("login", "passport", "accounts"))


def _dedupe_keep_order(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


_LOGIN_TERMS = (
    "登录",
    "登陆",
    "密码",
    "账号",
    "账户",
    "用户名",
    "手机号",
    "验证码",
    "Login",
    "Password",
)

_VERIFICATION_TERMS = (
    "验证码",
    "滑块",
    "短信",
    "扫码",
    "二维码",
    "设备验证",
    "安全验证",
    "风险验证",
    "风控",
    "人机验证",
    "CAPTCHA",
    "verification",
    "risk control",
)

_BUSINESS_TERMS = (
    "订单",
    "房态",
    "房价",
    "入住",
    "离店",
    "点评",
    "收益",
    "携程",
    "EBooking",
)
