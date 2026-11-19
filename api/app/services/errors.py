"""Domain errors shared by the REST API and the MCP server.

Services raise these; each interface maps them to its own error format
(HTTP status codes in FastAPI, ToolError/ResourceError in MCP).
"""


class DomainError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFoundError(DomainError):
    status_code = 404


class ForbiddenError(DomainError):
    status_code = 403


class InvalidRequestError(DomainError):
    status_code = 422


class UpstreamError(DomainError):
    """The LLM provider (or another dependency) failed."""
    status_code = 502
