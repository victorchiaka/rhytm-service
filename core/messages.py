from types import SimpleNamespace

USER_MESSAGES = SimpleNamespace(
    # Success
    OTP_SENT="Please check your mail or spam for an OTP.",
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
    UPDATED="Routine updated successfully.",
    NOT_FOUND="Routine not found.",
    DUPLICATE_NAME="{name} already exists.",
    INVALID_PERIOD_TIME="{period} routines must be between {start} and {end}.",
    ROUTINE_TIME_TOO_CLOSE="Routines on {days} must be at least 1 hour apart.",
    MAX_ROUTINES_PER_PERIOD="Maximum of {max_count} routines allowed per period.",
    ROUTINE_DURATION_OVERLAP="Overlaps with '{existing_name}' on {days}.",
    HABIT_NOT_FOUND="Habit not found.",
    HABIT_ALREADY_IN_ROUTINE="'{habit_name}' is already in another routine.",
    HABIT_DAYS_OUT_OF_RANGE="'{habit_name}' schedule doesn't match this routine.",
    HABIT_CONFLICT="'{habit_name}' conflicts with a {period} routine on {days}.",
    HABIT_REMINDER_OUT_OF_PERIOD="'{habit_name}' ({reminder_time}) is outside {period} period.",
    HABIT_REMINDER_TOO_EARLY="'{habit_name}' is {diff_mins}m earlier than routine start ({start}).",
    INVALID_DAY_DIGIT="Day must be between 0 (Sunday) and 6 (Saturday).",
    INVALID_DELETE_TOKEN="Invalid or expired deletion token.",
    DELETION_NOT_FOUND="Deletion record not found.",
    UNDO_EXPIRED="Undo window has expired.",
    DELETION_CONFIRMED="Routine deleted successfully.",
    UNDO_SUCCESS="Routine restored successfully.",
    DELETE_CHECK_SUCCESS="Routine deletion check completed.",
)


HABIT_MESSAGES = SimpleNamespace(
    CREATED="Habit created successfully.",
    UPDATED="Habit updated successfully.",
    NOT_FOUND="Habit not found.",
    DELETED="Habit deleted successfully.",
    HABIT_AND_ROUTINES_DELETED="Habit deleted, along with orphaned routines.",
    HABIT_TIME_CONFLICT="A habit is already set for {time} on {days}.",
    HABIT_DAYS_EXCEED_ROUTINE="'{habit_name}' schedule doesn't match its linked routine '{routine_name}'.",
    REMINDER_OUT_OF_PERIOD="'{habit_name}' ({reminder_time}) is outside '{routine_name}' ({period}).",
    REMINDER_TOO_EARLY="'{habit_name}' is {diff_mins}m earlier than '{routine_name}' ({start}).",
    INVALID_DAY_DIGIT="Day must be between 0 (Sunday) and 6 (Saturday).",
)

SUBSCRIPTION_MESSAGES = SimpleNamespace(
    SUBSCRIPTION_INITIATED="Subscription initiated. Complete payment to activate.",
    INVALID_PLAN="Invalid plan. Choose 'monthly' or 'yearly'.",
    USER_NOT_FOUND="User not found.",
    INVALID_WEBHOOK_SIGNATURE="Invalid webhook signature.",
)
