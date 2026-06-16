from __future__ import annotations

from typing import Any, Callable, TypeVar

from gql import Client
from gql.graphql_request import GraphQLRequest
from gql.transport.exceptions import TransportError, TransportQueryError, TransportServerError
from gql.transport.requests import RequestsHTTPTransport

from .models import GraphQLOperationEnvelope, GraphQLOperationError, graphql_errors_to_json


GraphQLOperationResult = TypeVar("GraphQLOperationResult")


class OfficeSpaceClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: int,
        user_agent: str,
        session_cookie_provider: Callable[[], str],
        csrf_token: str,
    ) -> None:
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self.session_cookie_provider = session_cookie_provider
        self.csrf_token = csrf_token

    def execute_operation(
        self,
        *,
        operation: dict[str, Any],
        referer: str,
        page_context: str,
        error_prefix: str,
        parser: Callable[[GraphQLOperationEnvelope], GraphQLOperationResult],
    ) -> GraphQLOperationResult:
        envelope = self.request_operation(
            url=f"{self.base_url}/graphql",
            headers={
                "Accept": "*/*",
                "Content-Type": "application/json",
                "Cookie": f"_huddle_session={self.session_cookie_provider()}",
                "Origin": self.base_url,
                "Referer": referer,
                "User-Agent": self.user_agent,
                "X-CSRF-Token": self.csrf_token,
                "X-Page-Context": page_context,
            },
            operation=operation,
            error_prefix=error_prefix,
        )

        if envelope.errors:
            raise RuntimeError(graphql_errors_to_json(envelope.errors))

        return parser(envelope)

    def request_operation(
        self,
        *,
        url: str,
        headers: dict[str, str],
        operation: dict[str, Any],
        error_prefix: str,
    ) -> GraphQLOperationEnvelope:
        query_text = operation.get("query")
        if not isinstance(query_text, str) or not query_text:
            raise RuntimeError(f"{error_prefix} requires a GraphQL query string.")

        variable_values = operation.get("variables")
        if variable_values is None:
            variable_values = {}
        if not isinstance(variable_values, dict):
            raise RuntimeError(f"{error_prefix} requires GraphQL variables to be an object.")

        operation_name = operation.get("operationName")
        if operation_name is not None and not isinstance(operation_name, str):
            raise RuntimeError(f"{error_prefix} requires operationName to be a string.")

        transport = RequestsHTTPTransport(
            url=url,
            headers=headers,
            timeout=self.timeout_seconds,
            use_json=True,
            method="POST",
        )
        client = Client(transport=transport, fetch_schema_from_transport=False)
        request_payload = GraphQLRequest(
            query_text,
            variable_values=variable_values,
            operation_name=operation_name,
        )

        try:
            data = client.execute(request_payload)
        except TransportQueryError as exc:
            return GraphQLOperationEnvelope(
                data=exc.data if isinstance(exc.data, dict) else None,
                errors=self.normalize_errors(exc.errors),
            )
        except TransportServerError as exc:
            status_code = getattr(exc, "code", None)
            if status_code is None:
                raise RuntimeError(f"{error_prefix}: {exc}") from exc
            raise RuntimeError(f"{error_prefix} with HTTP {status_code}: {exc}") from exc
        except TransportError as exc:
            raise RuntimeError(f"{error_prefix}: {exc}") from exc

        if not isinstance(data, dict):
            raise RuntimeError(f"{error_prefix} returned invalid GraphQL response: {data!r}")

        return GraphQLOperationEnvelope(data=data, errors=[])

    @staticmethod
    def normalize_errors(errors: list[Any] | None) -> list[GraphQLOperationError]:
        normalized_errors: list[GraphQLOperationError] = []
        for error_info in errors or []:
            if isinstance(error_info, GraphQLOperationError):
                normalized_errors.append(error_info)
            elif isinstance(error_info, dict):
                normalized_errors.append(GraphQLOperationError.model_validate(error_info))
            else:
                normalized_errors.append(GraphQLOperationError(message=str(error_info)))
        return normalized_errors