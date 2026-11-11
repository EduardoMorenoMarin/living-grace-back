class RegistrationConflict(Exception):
    pass


class CurrentPasswordIncorrect(Exception):
    pass


class PasswordChangeValidationError(Exception):
    pass


class PasswordChangePersistenceError(Exception):
    pass


class RegistrationPersistenceError(Exception):
    pass


class RegistrationValidationError(Exception):
    pass


class MinistryNotFound(Exception):
    pass


class MinistryMembersPersistenceError(Exception):
    pass


class RehearsalPersistenceError(Exception):
    pass


class RehearsalValidationError(Exception):
    pass


class RehearsalAttendanceConflict(Exception):
    pass


class RehearsalNotFound(Exception):
    pass


class RehearsalReadError(Exception):
    pass


class ProfilePersistenceError(Exception):
    pass


class ProfileUserUnavailable(Exception):
    pass


class MembershipNotFound(Exception):
    pass


class MembershipTransitionConflict(Exception):
    pass


class AttendanceSessionInvalid(Exception):
    pass


class RehearsalQrSessionNotFound(Exception):
    pass


class AttendanceCheckInForbidden(Exception):
    pass


class AttendanceCheckInPersistenceError(Exception):
    pass


class PasswordResetUserNotFound(Exception):
    pass


class PasswordResetUserIneligible(Exception):
    pass


class DashboardReadError(Exception):
    pass
