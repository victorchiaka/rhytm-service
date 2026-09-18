from types import SimpleNamespace

USER_MESSAGES = SimpleNamespace(
    # Success
    OTP_SENT="An OTP was sent to your email.",
    ACCOUNT_CREATED="Account created successfully.",
    LOGIN_SUCCESS="Login successful.",
    LOGOUT_SUCCESS="Logged out successfully.",
    PASSWORD_RESET_SUCCESS="Password reset successfully.",
    ACCOUNT_DELETED="Account deleted successfully.",
    TOKEN_REFRESH_SUCCESS="Token refreshed successfully.",
    # Errors
    USER_EXISTS="User with this email already exists.",
    USER_NOT_FOUND="User not found.",
    INVALID_OR_EXPIRED_OTP="Invalid or expired OTP.",
    INVALID_CREDENTIALS="Invalid email or password.",
    UNAUTHORIZED="Authentication required.",
    INVALID_TOKEN="Invalid or expired token.",
    SESSION_EXPIRED="Session expired or invalid.",
)

ROUTINE_MESSAGES = SimpleNamespace(
    CREATED="Routine created successfully.",
    DUPLICATE_NAME="{name} already exists.",
    DUPLICATE_PERIOD_ON_DAY="You already have a {period} routine on {days}.",
    HABIT_DAYS_OUT_OF_RANGE="'{habit_name}' days must match the routine's days.",
    HABIT_CONFLICT="'{habit_name}' is already in another {period} routine on {days}.",
)
