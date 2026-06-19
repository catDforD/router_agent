## ADDED Requirements

### Requirement: Main Agent supports OpenAI-compatible Chat Completions providers
The backend SHALL support Main Agent model execution through OpenAI-compatible Chat Completions providers configured independently from PLC worker simulation providers.

#### Scenario: Main Agent provider settings load from environment
- **WHEN** Main Agent provider environment variables are set before backend startup
- **THEN** settings include the Main Agent API key, base URL, model, timeout, max turns, and streaming preference
- **AND** these values do not overwrite `DEEPSEEK_*` worker simulation settings

#### Scenario: Provider diagnostics redact secrets
- **WHEN** diagnostics or startup logs include Main Agent provider configuration
- **THEN** API keys, authorization values, and secret-bearing URLs are redacted
- **AND** non-secret values such as provider mode, model, timeout, and streaming preference remain diagnosable

### Requirement: Main Agent model calls avoid structured-output-only features
The backend SHALL run production Main Agent model calls without requiring Responses API, OpenAI Agents SDK `output_type`, or Chat Completions `response_format`.

#### Scenario: Chat Completions request omits response_format
- **WHEN** the production Main Agent runner sends a request to the configured provider
- **THEN** the request uses Chat Completions messages and tool definitions
- **AND** the request does not include `response_format`
- **AND** the request does not require a Responses API endpoint

#### Scenario: Structured episode output is not required
- **WHEN** a Main Agent episode reaches terminal delivery through tools
- **THEN** the episode can complete without parsing a model-returned `MainAgentEpisodeOutput`

### Requirement: Main Agent runs as a tool-calling conversation loop
The backend SHALL drive Main Agent orchestration as a bounded conversation loop over assistant messages, tool calls, and tool results.

#### Scenario: Tool call is executed and returned to the model
- **WHEN** the provider response contains a valid tool call
- **THEN** the backend validates the tool arguments
- **AND** invokes the matching Router tool
- **AND** appends a tool result message to the conversation before the next model turn

#### Scenario: Assistant public message is persisted
- **WHEN** the provider response contains assistant content intended for the user-visible transcript
- **THEN** the backend records a bounded public Main Agent message event
- **AND** stores the same normalized entry in the Main Agent replay log

#### Scenario: Max turn limit stops the loop
- **WHEN** the conversation reaches the configured maximum turn count without a terminal tool result
- **THEN** Runtime records an observable Main Agent failure
- **AND** does not mark the task `succeeded`

### Requirement: Streaming is supported with non-streaming fallback
The backend SHALL support provider streaming for public Main Agent progress when available and SHALL provide a non-streaming fallback for providers with incomplete streaming tool-call support.

#### Scenario: Streaming provider emits public message progress
- **WHEN** streaming is enabled and the provider emits assistant content chunks
- **THEN** the backend normalizes the chunks into replayable public message events
- **AND** does not expose raw provider chunks as the frontend contract

#### Scenario: Non-streaming fallback preserves tool loop behavior
- **WHEN** streaming is disabled or a provider cannot stream tool calls reliably
- **THEN** the backend can run the same Main Agent tool loop through non-streaming Chat Completions responses
- **AND** still emits public assistant messages, tool call events, tool result events, and completion events
