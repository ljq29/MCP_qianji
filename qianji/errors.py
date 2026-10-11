class QianjiError(Exception):
    def __init__(self, ec: int, em: str, data: dict | None = None):
        self.ec = ec
        self.em = em
        self.data = data
        super().__init__(f"[{ec}] {em}")


class AuthenticationError(QianjiError):
    pass


class SignatureError(QianjiError):
    pass


class NotLoggedInError(QianjiError):
    def __init__(self):
        super().__init__(0, "Not logged in. Call login() first.")


class TagFeatureDisabledError(QianjiError):
    def __init__(self):
        super().__init__(409, "Qianji tag feature is disabled. Call set_tags_enabled(True) before passing tags.")


# HTTP-facing hint for TagFeatureDisabledError: the SDK message above refers to
# the Python API, while HTTP callers enable the feature via PUT /tags/enabled.
TAG_FEATURE_DISABLED_HTTP_MESSAGE = (
    "Qianji tag feature is disabled. Enable it with PUT /tags/enabled before passing tags."
)


class UpstreamError(QianjiError):
    """Upstream returned a non-2xx status or an unparseable body."""


class UpstreamTimeoutError(UpstreamError):
    """Upstream did not respond within the timeout window."""
