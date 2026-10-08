def get_user_friendly_error(exc: Exception) -> str:
    exc_type = type(exc).__name__
    exc_str = str(exc).lower()
    
    if "429" in exc_str or "rate limit" in exc_str or "too many requests" in exc_str:
        return "The AI service is currently busy due to high demand. Please try again in a few moments."
    if "401" in exc_str or "unauthorized" in exc_str or "invalid_api_key" in exc_str or exc_type == "InvalidDirectAPIKeyError":
        return "Authentication failed. Please check your provided API key and try again."
    if "413" in exc_str or "too large" in exc_str:
        return "The provided request is too large. Please reduce the size of your input."
    
    if exc_type == "NoAvailableLLMError":
        return "We are currently experiencing high traffic and no AI models are available. Please try again later."
    if exc_type == "ProviderRequestError":
        return "An error occurred while connecting to the AI provider. Please try again later."

    return "An unexpected error occurred while processing your request. Please try again."
