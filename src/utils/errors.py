def get_user_friendly_error(exc: Exception) -> str:
    exc_type = type(exc).__name__
    exc_str = str(exc).lower()
    
    if "429" in exc_str or "rate limit" in exc_str or "too many requests" in exc_str:
        return "The AI service is currently busy due to high demand. Please try again in a few moments."
    if "401" in exc_str or "unauthorized" in exc_str or "invalid_api_key" in exc_str or exc_type == "InvalidDirectAPIKeyError":
        return "Authentication failed. Please check your provided API key and try again."
    if "413" in exc_str or "too large" in exc_str:
        return "The provided request is too large. Please reduce the size of your input."
    if "invalid dbml" in exc_str or "invalid llm intent" in exc_str:
        return "The AI model returned an unexpected or invalid response. Please try rephrasing your request."
    if "unsupported ai provider" in exc_str:
        return "The requested AI provider is not supported. Please check your model settings and try again."
    if "incomplete" in exc_str and "llm settings" in exc_str:
        return "Please provide all required LLM configuration settings (provider, model, API key, and base URL)."
    if "api_key_encryption_key" in exc_str:
        return "A server configuration error occurred. Please contact support."
    
    if exc_type == "NoAvailableLLMError":
        return "We are currently experiencing high traffic and no AI models are available. Please try again later."
    if exc_type == "ProviderRequestError":
        return "An error occurred while connecting to the AI provider. Please try again later."

    return "An unexpected error occurred while processing your request. Please try again."
