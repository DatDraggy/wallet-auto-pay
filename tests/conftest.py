import asyncio
import sys
import pytest

# Monkeypatch pytest_socket to do nothing so ProactorEventLoop can create a socketpair
import pytest_socket
def do_nothing(*args, **kwargs):
    pass
pytest_socket.disable_socket = do_nothing
pytest_socket.SocketBlockedError = Exception

# On Windows, pytest-asyncio and Home Assistant's event loop can clash 
# with pytest-socket because Windows uses ProactorEventLoop by default,
# which creates an AF_INET socket for its self-pipe.
# We switch to the SelectorEventLoop to avoid this issue.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom components during testing."""
    yield