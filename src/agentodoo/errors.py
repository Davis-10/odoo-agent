class AgentOdooError(Exception):
    """An expected, user-actionable error. Its message is safe to print."""


class ConfigurationError(AgentOdooError):
    """A profile or local configuration problem."""


class CredentialError(AgentOdooError):
    """A missing or unusable credential."""


class OdooError(AgentOdooError):
    """The Odoo server could not be reached or rejected a request."""


class OdooRejectedError(OdooError):
    """Odoo raised an error for the request and rolled it back: nothing was written."""


class OdooUnreachableError(OdooError):
    """The request or its answer was lost (network, HTTP): whether Odoo applied it is unknown."""


class RefusedError(AgentOdooError):
    """A plan or apply was refused by a safety rule before anything was sent to Odoo for writing."""


class NotAllowedError(AgentOdooError):
    """The operation is outside the tool's allowlist."""
