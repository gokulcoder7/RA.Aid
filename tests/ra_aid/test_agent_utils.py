"""Unit tests for agent_utils.py."""

from typing import Any, Dict, Literal
from unittest.mock import Mock, patch

import litellm
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ra_aid.agent_context import (
    agent_context,
)
from ra_aid.agent_utils import (
    AgentState,
    create_agent,
    get_model_token_limit,
    is_anthropic_claude,
    state_modifier,
)
from ra_aid.models_params import DEFAULT_TOKEN_LIMIT, models_params


@pytest.fixture
def mock_model():
    """Fixture providing a mock LLM model."""
    model = Mock(spec=BaseChatModel)
    return model


@pytest.fixture
def mock_memory():
    """Fixture providing a mock global memory store."""
    with patch("ra_aid.agent_utils._global_memory") as mock_mem:
        mock_mem.get.return_value = {}
        yield mock_mem


def test_get_model_token_limit_anthropic(mock_memory):
    """Test get_model_token_limit with Anthropic model."""
    config = {"provider": "anthropic", "model": "claude2"}

    token_limit = get_model_token_limit(config, "default")
    assert token_limit == models_params["anthropic"]["claude2"]["token_limit"]


def test_get_model_token_limit_openai(mock_memory):
    """Test get_model_token_limit with OpenAI model."""
    config = {"provider": "openai", "model": "gpt-4"}

    token_limit = get_model_token_limit(config, "default")
    assert token_limit == models_params["openai"]["gpt-4"]["token_limit"]


def test_get_model_token_limit_unknown(mock_memory):
    """Test get_model_token_limit with unknown provider/model."""
    config = {"provider": "unknown", "model": "unknown-model"}

    token_limit = get_model_token_limit(config, "default")
    assert token_limit is None


def test_get_model_token_limit_missing_config(mock_memory):
    """Test get_model_token_limit with missing configuration."""
    config = {}

    token_limit = get_model_token_limit(config, "default")
    assert token_limit is None


def test_get_model_token_limit_litellm_success():
    """Test get_model_token_limit successfully getting limit from litellm."""
    config = {"provider": "anthropic", "model": "claude-2"}

    with patch("ra_aid.agent_utils.get_model_info") as mock_get_info:
        mock_get_info.return_value = {"max_input_tokens": 100000}
        token_limit = get_model_token_limit(config, "default")
        assert token_limit == 100000


def test_get_model_token_limit_litellm_not_found():
    """Test fallback to models_tokens when litellm raises NotFoundError."""
    config = {"provider": "anthropic", "model": "claude-2"}

    with patch("ra_aid.agent_utils.get_model_info") as mock_get_info:
        mock_get_info.side_effect = litellm.exceptions.NotFoundError(
            message="Model not found", model="claude-2", llm_provider="anthropic"
        )
        token_limit = get_model_token_limit(config, "default")
        assert token_limit == models_params["anthropic"]["claude2"]["token_limit"]


def test_get_model_token_limit_litellm_error():
    """Test fallback to models_tokens when litellm raises other exceptions."""
    config = {"provider": "anthropic", "model": "claude-2"}

    with patch("ra_aid.agent_utils.get_model_info") as mock_get_info:
        mock_get_info.side_effect = Exception("Unknown error")
        token_limit = get_model_token_limit(config, "default")
        assert token_limit == models_params["anthropic"]["claude2"]["token_limit"]


def test_get_model_token_limit_unexpected_error():
    """Test returning None when unexpected errors occur."""
    config = None  # This will cause an attribute error when accessed

    token_limit = get_model_token_limit(config, "default")
    assert token_limit is None


def test_create_agent_anthropic(mock_model, mock_memory):
    """Test create_agent with Anthropic Claude model."""
    mock_memory.get.return_value = {"provider": "anthropic", "model": "claude-2"}

    with patch("ra_aid.agent_utils.create_react_agent") as mock_react:
        mock_react.return_value = "react_agent"
        agent = create_agent(mock_model, [])

        assert agent == "react_agent"
        mock_react.assert_called_once_with(
            mock_model,
            [],
            version="v2",
            state_modifier=mock_react.call_args[1]["state_modifier"],
        )


def test_create_agent_openai(mock_model, mock_memory):
    """Test create_agent with OpenAI model."""
    mock_memory.get.return_value = {"provider": "openai", "model": "gpt-4"}

    with patch("ra_aid.agent_utils.CiaynAgent") as mock_ciayn:
        mock_ciayn.return_value = "ciayn_agent"
        agent = create_agent(mock_model, [])

        assert agent == "ciayn_agent"
        mock_ciayn.assert_called_once_with(
            mock_model,
            [],
            max_tokens=models_params["openai"]["gpt-4"]["token_limit"],
            config={"provider": "openai", "model": "gpt-4"},
        )


def test_create_agent_no_token_limit(mock_model, mock_memory):
    """Test create_agent when no token limit is found."""
    mock_memory.get.return_value = {"provider": "unknown", "model": "unknown-model"}

    with patch("ra_aid.agent_utils.CiaynAgent") as mock_ciayn:
        mock_ciayn.return_value = "ciayn_agent"
        agent = create_agent(mock_model, [])

        assert agent == "ciayn_agent"
        mock_ciayn.assert_called_once_with(
            mock_model,
            [],
            max_tokens=DEFAULT_TOKEN_LIMIT,
            config={"provider": "unknown", "model": "unknown-model"},
        )


def test_create_agent_missing_config(mock_model, mock_memory):
    """Test create_agent with missing configuration."""
    mock_memory.get.return_value = {"provider": "openai"}

    with patch("ra_aid.agent_utils.CiaynAgent") as mock_ciayn:
        mock_ciayn.return_value = "ciayn_agent"
        agent = create_agent(mock_model, [])

        assert agent == "ciayn_agent"
        mock_ciayn.assert_called_once_with(
            mock_model,
            [],
            max_tokens=DEFAULT_TOKEN_LIMIT,
            config={"provider": "openai"},
        )


@pytest.fixture
def mock_messages():
    """Fixture providing mock message objects."""

    return [
        SystemMessage(content="System prompt"),
        HumanMessage(content="Human message 1"),
        AIMessage(content="AI response 1"),
        HumanMessage(content="Human message 2"),
        AIMessage(content="AI response 2"),
    ]


def test_state_modifier(mock_messages):
    """Test that state_modifier correctly trims recent messages while preserving the first message when total tokens > max_tokens."""
    state = AgentState(messages=mock_messages)

    with patch(
        "ra_aid.agent_backends.ciayn_agent.CiaynAgent._estimate_tokens"
    ) as mock_estimate:
        mock_estimate.side_effect = lambda msg: 100 if msg else 0

        result = state_modifier(state, max_input_tokens=250)

        assert len(result) < len(mock_messages)
        assert isinstance(result[0], SystemMessage)
        assert result[-1] == mock_messages[-1]


def test_create_agent_with_checkpointer(mock_model, mock_memory):
    """Test create_agent with checkpointer argument."""
    mock_memory.get.return_value = {"provider": "openai", "model": "gpt-4"}
    mock_checkpointer = Mock()

    with patch("ra_aid.agent_utils.CiaynAgent") as mock_ciayn:
        mock_ciayn.return_value = "ciayn_agent"
        agent = create_agent(mock_model, [], checkpointer=mock_checkpointer)

        assert agent == "ciayn_agent"
        mock_ciayn.assert_called_once_with(
            mock_model,
            [],
            max_tokens=models_params["openai"]["gpt-4"]["token_limit"],
            config={"provider": "openai", "model": "gpt-4"},
        )


def test_create_agent_anthropic_token_limiting_enabled(mock_model, mock_memory):
    """Test create_agent sets up token limiting for Claude models when enabled."""
    mock_memory.get.return_value = {
        "provider": "anthropic",
        "model": "claude-2",
        "limit_tokens": True,
    }

    with (
        patch("ra_aid.agent_utils.create_react_agent") as mock_react,
        patch("ra_aid.agent_utils.get_model_token_limit") as mock_limit,
    ):
        mock_react.return_value = "react_agent"
        mock_limit.return_value = 100000

        agent = create_agent(mock_model, [])

        assert agent == "react_agent"
        args = mock_react.call_args
        assert "state_modifier" in args[1]
        assert callable(args[1]["state_modifier"])


def test_create_agent_anthropic_token_limiting_disabled(mock_model, mock_memory):
    """Test create_agent doesn't set up token limiting for Claude models when disabled."""
    mock_memory.get.return_value = {
        "provider": "anthropic",
        "model": "claude-2",
        "limit_tokens": False,
    }

    with (
        patch("ra_aid.agent_utils.create_react_agent") as mock_react,
        patch("ra_aid.agent_utils.get_model_token_limit") as mock_limit,
    ):
        mock_react.return_value = "react_agent"
        mock_limit.return_value = 100000

        agent = create_agent(mock_model, [])

        assert agent == "react_agent"
        mock_react.assert_called_once_with(mock_model, [], version="v2")


def test_get_model_token_limit_research(mock_memory):
    """Test get_model_token_limit with research provider and model."""
    config = {
        "provider": "openai",
        "model": "gpt-4",
        "research_provider": "anthropic",
        "research_model": "claude-2",
    }
    with patch("ra_aid.agent_utils.get_model_info") as mock_get_info:
        mock_get_info.return_value = {"max_input_tokens": 150000}
        token_limit = get_model_token_limit(config, "research")
        assert token_limit == 150000


def test_get_model_token_limit_planner(mock_memory):
    """Test get_model_token_limit with planner provider and model."""
    config = {
        "provider": "openai",
        "model": "gpt-4",
        "planner_provider": "deepseek",
        "planner_model": "dsm-1",
    }
    with patch("ra_aid.agent_utils.get_model_info") as mock_get_info:
        mock_get_info.return_value = {"max_input_tokens": 120000}
        token_limit = get_model_token_limit(config, "planner")
        assert token_limit == 120000


# New tests for private helper methods in agent_utils.py


def test_setup_and_restore_interrupt_handling():
    import signal

    from ra_aid.agent_utils import (
        _request_interrupt,
        _restore_interrupt_handling,
        _setup_interrupt_handling,
    )

    original_handler = signal.getsignal(signal.SIGINT)
    handler = _setup_interrupt_handling()
    # Verify the SIGINT handler is set to _request_interrupt
    assert signal.getsignal(signal.SIGINT) == _request_interrupt
    _restore_interrupt_handling(handler)
    # Verify the SIGINT handler is restored to the original
    assert signal.getsignal(signal.SIGINT) == original_handler


def test_increment_and_decrement_agent_depth():
    from ra_aid.agent_utils import (
        _decrement_agent_depth,
        _global_memory,
        _increment_agent_depth,
    )

    _global_memory["agent_depth"] = 10
    _increment_agent_depth()
    assert _global_memory["agent_depth"] == 11
    _decrement_agent_depth()
    assert _global_memory["agent_depth"] == 10


def test_run_agent_stream(monkeypatch):
    from ra_aid.agent_utils import _run_agent_stream

    # Create a dummy agent that yields one chunk
    class DummyAgent:
        def stream(self, input_data, cfg: dict):
            yield {"content": "chunk1"}

    dummy_agent = DummyAgent()
    # Set flags so that _run_agent_stream will reset them
    with agent_context() as ctx:
        ctx.plan_completed = True
        ctx.task_completed = True
        ctx.completion_message = "existing"

    call_flag = {"called": False}

    def fake_print_agent_output(
        chunk: Dict[str, Any], agent_type: Literal["CiaynAgent", "React"]
    ):
        call_flag["called"] = True

    monkeypatch.setattr(
        "ra_aid.agent_utils.print_agent_output", fake_print_agent_output
    )
    _run_agent_stream(dummy_agent, [HumanMessage("dummy prompt")], {})
    assert call_flag["called"]

    with agent_context() as ctx:
        assert ctx.plan_completed is False
        assert ctx.task_completed is False
        assert ctx.completion_message == ""


def test_execute_test_command_wrapper(monkeypatch):
    from ra_aid.agent_utils import _execute_test_command_wrapper

    # Patch execute_test_command to return a testable tuple
    def fake_execute(config, orig, tests, auto):
        return (True, "new prompt", auto, tests + 1)

    monkeypatch.setattr("ra_aid.agent_utils.execute_test_command", fake_execute)
    result = _execute_test_command_wrapper("orig", {}, 0, False)
    assert result == (True, "new prompt", False, 1)


def test_handle_api_error_valueerror():
    import pytest

    from ra_aid.agent_utils import _handle_api_error

    # ValueError not containing "code" or rate limit phrases should be re-raised
    with pytest.raises(ValueError):
        _handle_api_error(ValueError("some unrelated error"), 0, 5, 1)
        
    # ValueError with "429" should be handled without raising
    _handle_api_error(ValueError("error code 429"), 0, 5, 1)
    
    # ValueError with "rate limit" phrase should be handled without raising
    _handle_api_error(ValueError("hit rate limit"), 0, 5, 1)
    
    # ValueError with "too many requests" phrase should be handled without raising
    _handle_api_error(ValueError("too many requests, try later"), 0, 5, 1)
    
    # ValueError with "quota exceeded" phrase should be handled without raising
    _handle_api_error(ValueError("quota exceeded for this month"), 0, 5, 1)


def test_handle_api_error_status_code():
    from ra_aid.agent_utils import _handle_api_error
    
    # Error with status_code=429 attribute should be handled without raising
    error_with_status = Exception("Rate limited")
    error_with_status.status_code = 429
    _handle_api_error(error_with_status, 0, 5, 1)
    
    # Error with http_status=429 attribute should be handled without raising
    error_with_http_status = Exception("Too many requests")
    error_with_http_status.http_status = 429
    _handle_api_error(error_with_http_status, 0, 5, 1)


def test_handle_api_error_rate_limit_phrases():
    from ra_aid.agent_utils import _handle_api_error
    
    # Generic exception with "rate limit" phrase should be handled without raising
    _handle_api_error(Exception("You have exceeded your rate limit"), 0, 5, 1)
    
    # Generic exception with "too many requests" phrase should be handled without raising
    _handle_api_error(Exception("Too many requests, please slow down"), 0, 5, 1)
    
    # Generic exception with "quota exceeded" phrase should be handled without raising
    _handle_api_error(Exception("API quota exceeded for this billing period"), 0, 5, 1)
    
    # Generic exception with "rate" and "limit" separate but in message should be handled
    _handle_api_error(Exception("You hit the rate at which we limit requests"), 0, 5, 1)


def test_handle_api_error_max_retries():
    import pytest

    from ra_aid.agent_utils import _handle_api_error

    # When attempt reaches max retries, a RuntimeError should be raised
    with pytest.raises(RuntimeError):
        _handle_api_error(Exception("error code 429"), 4, 5, 1)


def test_handle_api_error_retry(monkeypatch):
    import time

    from ra_aid.agent_utils import _handle_api_error

    # Patch time.monotonic and time.sleep to simulate immediate delay expiration
    fake_time = [0]

    def fake_monotonic():
        fake_time[0] += 0.5
        return fake_time[0]

    monkeypatch.setattr(time, "monotonic", fake_monotonic)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    # Should not raise error when attempt is lower than max retries
    _handle_api_error(Exception("error code 429"), 0, 5, 1)


def test_is_anthropic_claude():
    """Test is_anthropic_claude function with various configurations."""
    # Test Anthropic provider cases
    assert is_anthropic_claude({"provider": "anthropic", "model": "claude-2"})
    assert is_anthropic_claude({"provider": "ANTHROPIC", "model": "claude-instant"})
    assert not is_anthropic_claude({"provider": "anthropic", "model": "gpt-4"})

    # Test OpenRouter provider cases
    assert is_anthropic_claude(
        {"provider": "openrouter", "model": "anthropic/claude-2"}
    )
    assert is_anthropic_claude(
        {"provider": "openrouter", "model": "anthropic/claude-instant"}
    )
    assert not is_anthropic_claude({"provider": "openrouter", "model": "openai/gpt-4"})

    # Test edge cases
    assert not is_anthropic_claude({})  # Empty config
    assert not is_anthropic_claude({"provider": "anthropic"})  # Missing model
    assert not is_anthropic_claude({"model": "claude-2"})  # Missing provider
    assert not is_anthropic_claude(
        {"provider": "other", "model": "claude-2"}
    )  # Wrong provider


def test_run_agent_with_retry_checks_crash_status(monkeypatch):
    """Test that run_agent_with_retry checks for crash status at the beginning of each iteration."""
    from ra_aid.agent_context import agent_context, mark_agent_crashed
    from ra_aid.agent_utils import run_agent_with_retry

    # Setup mocks for dependencies to isolate our test
    dummy_agent = Mock()

    # Track function calls
    mock_calls = {"run_agent_stream": 0}

    def mock_run_agent_stream(*args, **kwargs):
        mock_calls["run_agent_stream"] += 1

    def mock_setup_interrupt_handling():
        return None

    def mock_restore_interrupt_handling(handler):
        pass

    def mock_increment_agent_depth():
        pass

    def mock_decrement_agent_depth():
        pass

    def mock_is_crashed():
        return ctx.is_crashed() if ctx else False

    def mock_get_crash_message():
        return ctx.agent_crashed_message if ctx and ctx.is_crashed() else None

    # Apply mocks
    monkeypatch.setattr("ra_aid.agent_utils._run_agent_stream", mock_run_agent_stream)
    monkeypatch.setattr(
        "ra_aid.agent_utils._setup_interrupt_handling", mock_setup_interrupt_handling
    )
    monkeypatch.setattr(
        "ra_aid.agent_utils._restore_interrupt_handling",
        mock_restore_interrupt_handling,
    )
    monkeypatch.setattr(
        "ra_aid.agent_utils._increment_agent_depth", mock_increment_agent_depth
    )
    monkeypatch.setattr(
        "ra_aid.agent_utils._decrement_agent_depth", mock_decrement_agent_depth
    )
    monkeypatch.setattr("ra_aid.agent_utils.check_interrupt", lambda: None)

    # First, run without a crash - agent should be run
    with agent_context() as ctx:
        monkeypatch.setattr("ra_aid.agent_context.is_crashed", mock_is_crashed)
        monkeypatch.setattr(
            "ra_aid.agent_context.get_crash_message", mock_get_crash_message
        )
        result = run_agent_with_retry(dummy_agent, "test prompt", {})
        assert mock_calls["run_agent_stream"] == 1

    # Reset call counter
    mock_calls["run_agent_stream"] = 0

    # Now run with a crash - agent should not be run
    with agent_context() as ctx:
        mark_agent_crashed("Test crash message")
        monkeypatch.setattr("ra_aid.agent_context.is_crashed", mock_is_crashed)
        monkeypatch.setattr(
            "ra_aid.agent_context.get_crash_message", mock_get_crash_message
        )
        result = run_agent_with_retry(dummy_agent, "test prompt", {})
        # Verify _run_agent_stream was not called
        assert mock_calls["run_agent_stream"] == 0
        # Verify the result contains the crash message
        assert "Agent has crashed: Test crash message" in result


def test_run_agent_with_retry_handles_badrequest_error(monkeypatch):
    """Test that run_agent_with_retry properly handles BadRequestError as unretryable."""
    from ra_aid.agent_context import agent_context, is_crashed
    from ra_aid.agent_utils import run_agent_with_retry
    from ra_aid.exceptions import ToolExecutionError

    # Setup mocks
    dummy_agent = Mock()

    # Track function calls and simulate BadRequestError
    run_count = [0]

    def mock_run_agent_stream(*args, **kwargs):
        run_count[0] += 1
        if run_count[0] == 1:
            # First call throws a 400 BadRequestError
            raise ToolExecutionError("400 Bad Request: Invalid input")
        # If it's called again, it should run normally

    def mock_setup_interrupt_handling():
        return None

    def mock_restore_interrupt_handling(handler):
        pass

    def mock_increment_agent_depth():
        pass

    def mock_decrement_agent_depth():
        pass

    def mock_mark_agent_crashed(message):
        ctx.agent_has_crashed = True
        ctx.agent_crashed_message = message

    def mock_is_crashed():
        return ctx.is_crashed() if ctx else False

    # Apply mocks
    monkeypatch.setattr("ra_aid.agent_utils._run_agent_stream", mock_run_agent_stream)
    monkeypatch.setattr(
        "ra_aid.agent_utils._setup_interrupt_handling", mock_setup_interrupt_handling
    )
    monkeypatch.setattr(
        "ra_aid.agent_utils._restore_interrupt_handling",
        mock_restore_interrupt_handling,
    )
    monkeypatch.setattr(
        "ra_aid.agent_utils._increment_agent_depth", mock_increment_agent_depth
    )
    monkeypatch.setattr(
        "ra_aid.agent_utils._decrement_agent_depth", mock_decrement_agent_depth
    )
    monkeypatch.setattr("ra_aid.agent_utils.check_interrupt", lambda: None)

    with agent_context() as ctx:
        monkeypatch.setattr(
            "ra_aid.agent_context.mark_agent_crashed", mock_mark_agent_crashed
        )
        monkeypatch.setattr("ra_aid.agent_context.is_crashed", mock_is_crashed)

        result = run_agent_with_retry(dummy_agent, "test prompt", {})
        # Verify the agent was only run once and not retried
        assert run_count[0] == 1
        # Verify the result contains the crash message
        assert "Agent has crashed: Unretryable error" in result
        # Verify the agent is marked as crashed
        assert is_crashed()


def test_run_agent_with_retry_handles_api_badrequest_error(monkeypatch):
    """Test that run_agent_with_retry properly handles API BadRequestError as unretryable."""
    # Import APIError from anthropic module and patch it on the agent_utils module

    from ra_aid.agent_context import agent_context, is_crashed
    from ra_aid.agent_utils import run_agent_with_retry

    # Setup mocks
    dummy_agent = Mock()

    # Track function calls and simulate BadRequestError
    run_count = [0]

    # Create a mock APIError class that simulates Anthropic's APIError
    class MockAPIError(Exception):
        pass

    def mock_run_agent_stream(*args, **kwargs):
        run_count[0] += 1
        if run_count[0] == 1:
            # First call throws a 400 Bad Request APIError
            mock_error = MockAPIError("400 Bad Request")
            mock_error.__class__.__name__ = (
                "APIError"  # Make it look like Anthropic's APIError
            )
            raise mock_error
        # If it's called again, it should run normally

    def mock_setup_interrupt_handling():
        return None

    def mock_restore_interrupt_handling(handler):
        pass

    def mock_increment_agent_depth():
        pass

    def mock_decrement_agent_depth():
        pass

    def mock_mark_agent_crashed(message):
        ctx.agent_has_crashed = True
        ctx.agent_crashed_message = message

    def mock_is_crashed():
        return ctx.is_crashed() if ctx else False

    # Apply mocks
    monkeypatch.setattr("ra_aid.agent_utils._run_agent_stream", mock_run_agent_stream)
    monkeypatch.setattr(
        "ra_aid.agent_utils._setup_interrupt_handling", mock_setup_interrupt_handling
    )
    monkeypatch.setattr(
        "ra_aid.agent_utils._restore_interrupt_handling",
        mock_restore_interrupt_handling,
    )
    monkeypatch.setattr(
        "ra_aid.agent_utils._increment_agent_depth", mock_increment_agent_depth
    )
    monkeypatch.setattr(
        "ra_aid.agent_utils._decrement_agent_depth", mock_decrement_agent_depth
    )
    monkeypatch.setattr("ra_aid.agent_utils.check_interrupt", lambda: None)
    monkeypatch.setattr("ra_aid.agent_utils._handle_api_error", lambda *args: None)
    monkeypatch.setattr("ra_aid.agent_utils.APIError", MockAPIError)
    monkeypatch.setattr(
        "ra_aid.agent_context.mark_agent_crashed", mock_mark_agent_crashed
    )
    monkeypatch.setattr("ra_aid.agent_context.is_crashed", mock_is_crashed)

    with agent_context() as ctx:
        result = run_agent_with_retry(dummy_agent, "test prompt", {})
        # Verify the agent was only run once and not retried
        assert run_count[0] == 1
        # Verify the result contains the crash message
        assert "Agent has crashed: Unretryable API error" in result
        # Verify the agent is marked as crashed
        assert is_crashed()

def test_handle_api_error_resource_exhausted():
    from google.api_core.exceptions import ResourceExhausted
    from ra_aid.agent_utils import _handle_api_error
    
    # ResourceExhausted exception should be handled without raising
    resource_exhausted_error = ResourceExhausted("429 Resource has been exhausted (e.g. check quota).")
    _handle_api_error(resource_exhausted_error, 0, 5, 1)