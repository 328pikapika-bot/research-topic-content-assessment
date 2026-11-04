from fastapi import Header, HTTPException

BLOCKED_USER_SCOPES = {"", "default", "unknown", "mock_user_id", "test_user", "test_account"}
BLOCKED_USER_SCOPE_PREFIXES = ("nstl_user_", "mock_", "fake_", "test_")


def resolve_user_scope(
    x_user_scope: str | None = Header(default=None, alias="X-User-Scope"),
) -> str:
    scope = (x_user_scope or "").strip()
    lower_scope = scope.lower()
    if lower_scope in BLOCKED_USER_SCOPES or lower_scope.startswith(BLOCKED_USER_SCOPE_PREFIXES):
        raise HTTPException(status_code=401, detail="请先登录 NSTL 账号")
    return scope
