# app/domain/exceptions.py
class AppError(Exception):
    def __init__(self, message: str, code: str = "internal_error", status_code: int = 500):
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)

class InfraError(AppError):
    pass

class VaultError(InfraError):
    pass

class DatabaseError(InfraError):
    pass
class DomainError(AppError):
    """Expected domain error (not found, conflict, validation)."""
    pass