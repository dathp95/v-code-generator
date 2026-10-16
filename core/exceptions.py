"""Public errors safe to display at application boundaries."""


class ActivationValidationError(ValueError):
    """Activation inputs are invalid."""


class PrivateKeyLoadError(ValueError):
    """An encrypted RSA private key could not be loaded."""
