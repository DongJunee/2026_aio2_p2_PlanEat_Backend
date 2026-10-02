"""실 Tool Hub 연결 전 워크플로우 테스트용 fake provider입니다."""

from app.agent.tools.contracts import ToolRequest, ToolResult


class FakeToolHubProvider:
    """네트워크 I/O 없이 Tool 호출 경계만 검증하는 결정적 fake입니다."""

    async def execute(self, request: ToolRequest) -> ToolResult:
        """실행된 도구 이름을 포함한 최소 성공 결과를 반환합니다."""

        return ToolResult(
            result={"accepted_tool_name": request.tool_name},
            source_metadata={"provider": "fake-tool-hub"},
        )
