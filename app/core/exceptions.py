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


class ProfilePersistenceError(Exception):
    pass


class ProfileUserUnavailable(Exception):
    pass
