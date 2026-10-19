class RegistrationConflict(Exception):
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


class RehearsalNotFound(Exception):
    pass


class RehearsalReadError(Exception):
    pass


class ProfilePersistenceError(Exception):
    pass


class ProfileUserUnavailable(Exception):
    pass
