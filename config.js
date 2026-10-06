window.AGENT_MONITOR_CONFIG = {
  pollMs: 30000,
  staleMs: 120000,
  timeoutMs: 2200,
  agents: {
    hermes: {
      statusUrl: "http://127.0.0.1:8892/status/hermes"
    },
    claude: {
      statusUrl: "http://127.0.0.1:8892/status/claude"
    },
    codex: {
      statusUrl: "http://127.0.0.1:8892/status/codex"
    }
  }
};
