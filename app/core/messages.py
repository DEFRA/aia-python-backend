class AppMessages:
    # Auth
    MISSING_USER_ID_HEADER = "Missing {header} header"
    MISSING_AUTH_HEADER = "Missing Authorization header"
    AUTH_IDENTITY_MISMATCH = "Forbidden: Identity mismatch"
    INVALID_TOKEN_MISSING_SUB = "Invalid token: missing subject claim"
    TOKEN_EXPIRED = "Token has expired"
    TOKEN_INVALID_SIGNATURE = "Invalid token: Signature verification failed"
    TOKEN_INVALID_FORMAT = "Invalid token: {error}"
    INTERNAL_AUTH_ERROR = "Internal authentication error"

    # Documents
    FILE_ALREADY_UPLOADED = "A file named '{file_name}' has already been uploaded by user '{user_id}'."
    DOC_METADATA_SAVE_FAILED = "Failed to record document metadata."
    DOC_NOT_FOUND = "Document '{doc_id}' not found."
    USER_NOT_FOUND = "User not found."


messages = AppMessages()
