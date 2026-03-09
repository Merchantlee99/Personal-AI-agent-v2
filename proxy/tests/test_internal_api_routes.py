import unittest
from unittest.mock import patch

import app.main as main
from app.http_routes import chat_reply
from app.models import AgentResponse, ChatRequest


class InternalApiRouteContractTests(unittest.TestCase):
    def test_chat_route_requires_internal_auth_dependency(self) -> None:
        route = next(route for route in main.app.routes if getattr(route, "path", "") == "/api/chat")
        dependency_names = [getattr(dep.call, "__name__", "") for dep in route.dependant.dependencies]
        self.assertIn("verify_internal_request", dependency_names)

    def test_runtime_metrics_route_requires_internal_auth_dependency(self) -> None:
        route = next(route for route in main.app.routes if getattr(route, "path", "") == "/api/runtime-metrics")
        dependency_names = [getattr(dep.call, "__name__", "") for dep in route.dependant.dependencies]
        self.assertIn("verify_internal_request", dependency_names)

    def test_orchestration_events_route_requires_internal_auth_dependency(self) -> None:
        route = next(route for route in main.app.routes if getattr(route, "path", "") == "/api/orchestration/events")
        dependency_names = [getattr(dep.call, "__name__", "") for dep in route.dependant.dependencies]
        self.assertIn("verify_internal_request", dependency_names)

    def test_chat_reply_handles_missing_memory_context_field(self) -> None:
        with (
            patch("app.http_routes.build_agent_memory_context", return_value="minerva context"),
            patch(
                "app.http_routes.run_agent_pipeline",
                return_value=AgentResponse(
                    agent_id="minerva",
                    model="mock-model",
                    reply="ok",
                    role_boundary="orchestrator",
                ),
            ),
        ):
            result = chat_reply(ChatRequest(agentId="minerva", message="hello"), None)

        self.assertEqual(result["agentId"], "minerva")
        self.assertEqual(result["reply"], "ok")


if __name__ == "__main__":
    unittest.main()
