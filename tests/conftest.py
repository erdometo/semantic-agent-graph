import os
import pytest

@pytest.fixture(autouse=True)
def clean_env():
    """
    Fixture that runs automatically before every test to clear any live API keys.
    This guarantees hermetic testing and prevents tests from making live network calls.
    """
    # Save original environment values
    orig_openrouter = os.environ.get("OPENROUTER_API_KEY")
    orig_gemini = os.environ.get("GEMINI_API_KEY")
    
    # Clear keys for the duration of the test
    if "OPENROUTER_API_KEY" in os.environ:
        del os.environ["OPENROUTER_API_KEY"]
    if "GEMINI_API_KEY" in os.environ:
        del os.environ["GEMINI_API_KEY"]
        
    yield
    
    # Restore original environment values after the test
    if orig_openrouter is not None:
        os.environ["OPENROUTER_API_KEY"] = orig_openrouter
    if orig_gemini is not None:
        os.environ["GEMINI_API_KEY"] = orig_gemini
