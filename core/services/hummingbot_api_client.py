"""Async client for Hummingbot Backend API and Condor-facing workflows."""
from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import aiohttp

from core.services.client_base import ClientBase


class HummingbotAPIClient(ClientBase):
    """
    Lightweight async client with endpoint fallbacks for Hummingbot API variants.

    The official Hummingbot API and Condor projects expose a richer router-based
    client surface. This local implementation keeps Quants-Lab decoupled from an
    extra dependency while aligning method names and payloads with that shape.
    """

    HEALTH_ENDPOINTS = ("health", "healthz")
    BOTS_STATUS_ENDPOINTS = ("bots/status", "bots", "bots/all")
    BOT_RUNS_ENDPOINTS = ("bots/runs", "bots/history", "bots")
    BALANCE_ENDPOINTS = (
        "portfolio/state",
        "portfolio/balances",
        "balances",
        "portfolio",
        "wallet/balances",
        "accounts/balances",
    )
    PERFORMANCE_ENDPOINTS = (
        "portfolio/summary",
        "portfolio/performance",
        "performance",
        "bots/performance",
        "portfolio",
    )
    CONTROLLER_CONFIG_ENDPOINTS = ("controllers/configs", "controllers/configurations")
    CONTROLLER_SCHEMA_ENDPOINTS = (
        "controllers/{controller_name}/schema",
        "controllers/schema/{controller_name}",
        "controllers/{controller_name}",
    )
    CONTROLLERS_ENDPOINTS = ("controllers", "controllers/list")
    CONTAINERS_ENDPOINTS = ("docker/containers/active", "containers/active", "containers")
    DOCKER_STATUS_ENDPOINTS = ("docker/status", "containers/status", "docker")
    EXECUTORS_ENDPOINTS = ("executors/active", "bots/executors", "active-executors")

    def __init__(
        self,
        server: str = "localhost",
        port: int = 8000,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        parsed = self._parse_server(server, port)
        super().__init__(host=parsed["host"], port=parsed["port"])
        self.base_url = f"{parsed['scheme']}://{parsed['host']}:{parsed['port']}"
        self.username = username or os.getenv("HUMMINGBOT_API_USERNAME") or os.getenv("BACKEND_API_USERNAME")
        self.password = password or os.getenv("HUMMINGBOT_API_PASSWORD") or os.getenv("BACKEND_API_PASSWORD")
        self.auth = aiohttp.BasicAuth(self.username, self.password) if self.username and self.password else None

    @staticmethod
    def _parse_server(server: str, default_port: int) -> Dict[str, Any]:
        if server.startswith("http://") or server.startswith("https://"):
            parsed = urlparse(server)
            return {
                "scheme": parsed.scheme or "http",
                "host": parsed.hostname or "localhost",
                "port": parsed.port or default_port,
            }

        if ":" in server:
            host, port = server.rsplit(":", 1)
            if port.isdigit():
                return {"scheme": "http", "host": host or "localhost", "port": int(port)}

        return {"scheme": "http", "host": server or "localhost", "port": default_port}

    async def __aenter__(self):
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def get(self, endpoint: str, params: Optional[Dict] = None, auth: Optional[aiohttp.BasicAuth] = None):
        return await self._request_json("GET", endpoint, params=params, auth=auth or self.auth)

    async def post(
        self,
        endpoint: str,
        payload: Optional[Dict] = None,
        params: Optional[Dict] = None,
        auth: Optional[aiohttp.BasicAuth] = None,
    ):
        return await self._request_json("POST", endpoint, payload=payload, params=params, auth=auth or self.auth)

    async def put(
        self,
        endpoint: str,
        payload: Optional[Dict] = None,
        params: Optional[Dict] = None,
        auth: Optional[aiohttp.BasicAuth] = None,
    ):
        return await self._request_json("PUT", endpoint, payload=payload, params=params, auth=auth or self.auth)

    async def delete(
        self,
        endpoint: str,
        payload: Optional[Dict] = None,
        params: Optional[Dict] = None,
        auth: Optional[aiohttp.BasicAuth] = None,
    ):
        return await self._request_json("DELETE", endpoint, payload=payload, params=params, auth=auth or self.auth)

    async def _request_json(
        self,
        method: str,
        endpoint: str,
        payload: Optional[Dict] = None,
        params: Optional[Dict] = None,
        auth: Optional[aiohttp.BasicAuth] = None,
    ):
        await self._ensure_session()
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = {"accept": "application/json"}
        request_kwargs: Dict[str, Any] = {"params": params, "headers": headers, "auth": auth}
        if payload is not None:
            request_kwargs["json"] = payload
            request_kwargs["headers"] = {**headers, "Content-Type": "application/json"}
        response = await self.session.request(method.upper(), url, **request_kwargs)
        return await self._process_response(response)

    async def request_first(
        self,
        method: str,
        endpoints: Sequence[str],
        payload: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> Tuple[Optional[str], Optional[Any]]:
        candidates = [{"method": method, "endpoint": endpoint, "payload": payload, "params": params} for endpoint in endpoints]
        return await self.request_candidates(candidates)

    async def request_candidates(self, candidates: Iterable[Dict[str, Any]]) -> Tuple[Optional[str], Optional[Any]]:
        last_response = None
        for candidate in candidates:
            method = candidate.get("method", "GET")
            endpoint = candidate["endpoint"]
            payload = candidate.get("payload")
            params = candidate.get("params")

            if method.upper() == "GET":
                response = await self.get(endpoint, params=params)
            elif method.upper() == "POST":
                response = await self.post(endpoint, payload=payload, params=params)
            elif method.upper() == "PUT":
                response = await self.put(endpoint, payload=payload, params=params)
            elif method.upper() == "DELETE":
                response = await self.delete(endpoint, payload=payload, params=params)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            if self.is_success_response(response):
                return endpoint, response
            last_response = response
        return None, last_response

    @staticmethod
    def is_success_response(response: Any) -> bool:
        if response is None:
            return False
        if isinstance(response, dict):
            if response.get("error"):
                return False
            status = response.get("status")
            if isinstance(status, int) and status >= 400:
                return False
        return True

    @staticmethod
    def unwrap_data(payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload
        for key in ("data", "result", "payload"):
            value = payload.get(key)
            if value is not None:
                return value
        return payload

    @staticmethod
    def coerce_float(value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def health_check(self) -> bool:
        endpoint, response = await self.request_first("GET", self.HEALTH_ENDPOINTS)
        return endpoint is not None and self.is_success_response(response)

    async def get_health(self) -> Dict[str, Any]:
        endpoint, response = await self.request_first("GET", self.HEALTH_ENDPOINTS)
        return {
            "healthy": endpoint is not None and self.is_success_response(response),
            "endpoint": endpoint,
            "response": response,
        }

    async def list_bots(self):
        endpoint, response = await self.request_first("GET", self.BOTS_STATUS_ENDPOINTS)
        return response if endpoint else {}

    async def get_active_bots_status(self):
        return await self.list_bots()

    async def get_active_bots_status_map(self) -> Dict[str, Any]:
        payload = await self.get_active_bots_status()
        return self.normalize_named_map(self.unwrap_data(payload))

    async def get_bot_status(self, bot_name: str):
        endpoints = (
            f"bots/{bot_name}/status",
            f"bots/{bot_name}",
        )
        endpoint, response = await self.request_first("GET", endpoints)
        return response if endpoint else {}

    async def get_bot_runs(self, limit: Optional[int] = None):
        params = {"limit": limit} if limit is not None else None
        endpoint, response = await self.request_first("GET", self.BOT_RUNS_ENDPOINTS, params=params)
        return response if endpoint else {}

    async def get_balances(self):
        endpoint, response = await self.request_first("GET", self.BALANCE_ENDPOINTS)
        return response if endpoint else {}

    async def get_portfolio(self):
        return await self.get_balances()

    async def get_portfolio_summary(self):
        endpoint, response = await self.request_first("GET", self.PERFORMANCE_ENDPOINTS)
        return response if endpoint else {}

    async def get_active_executors(self):
        endpoint, response = await self.request_first("GET", self.EXECUTORS_ENDPOINTS)
        return response if endpoint else {}

    async def list_controllers(self):
        endpoint, response = await self.request_first("GET", self.CONTROLLERS_ENDPOINTS)
        return response if endpoint else {}

    async def get_controller_schema(self, controller_name: str):
        endpoints = tuple(
            endpoint.format(controller_name=controller_name) for endpoint in self.CONTROLLER_SCHEMA_ENDPOINTS
        )
        endpoint, response = await self.request_first("GET", endpoints)
        return response if endpoint else {}

    async def list_controller_configs(self):
        endpoint, response = await self.request_first("GET", self.CONTROLLER_CONFIG_ENDPOINTS)
        return response if endpoint else {}

    async def get_bot_controller_configs(self, bot_name: str):
        endpoints = (
            f"bots/{bot_name}/controllers/configs",
            f"bots/{bot_name}/controllers",
        )
        endpoint, response = await self.request_first("GET", endpoints)
        return response if endpoint else {}

    async def add_controller_config(self, config: Dict[str, Any]):
        config_id = config.get("id")
        candidates: List[Dict[str, Any]] = []
        if config_id:
            candidates.extend(
                [
                    {"method": "PUT", "endpoint": f"controllers/configs/{config_id}", "payload": config},
                    {"method": "PUT", "endpoint": f"controllers/configurations/{config_id}", "payload": config},
                ]
            )
        candidates.extend(
            {"method": "POST", "endpoint": endpoint, "payload": config}
            for endpoint in self.CONTROLLER_CONFIG_ENDPOINTS
        )
        endpoint, response = await self.request_candidates(candidates)
        return response if endpoint else {"error": "Unable to store controller config", "status": 404}

    async def create_or_update_controller(self, controller_type: str, name: str, data: Dict[str, Any]):
        candidates = [
            {"method": "PUT", "endpoint": f"controllers/{controller_type}/{name}", "payload": data},
            {"method": "POST", "endpoint": f"controllers/{controller_type}/{name}", "payload": data},
        ]
        endpoint, response = await self.request_candidates(candidates)
        return response if endpoint else {"error": "Unable to create or update controller", "status": 404}

    async def deploy_v2_controllers(
        self,
        name: str,
        profile: str,
        controllers: Sequence[Any],
        script_name: str = "v2_with_controllers.py",
        image_name: str = "hummingbot/hummingbot:latest",
        credentials: Optional[str] = None,
        time_to_cash_out: Optional[int] = None,
    ):
        controller_refs = self._as_controller_references(controllers)
        credentials_name = credentials or profile
        candidates = [
            {
                "method": "POST",
                "endpoint": "bots/deploy-v2/controllers",
                "payload": {
                    "name": name,
                    "profile": profile,
                    "controllers": controller_refs,
                    "time_to_cash_out": time_to_cash_out,
                },
            },
            {
                "method": "POST",
                "endpoint": "bots/v2/deploy/controllers",
                "payload": {
                    "name": name,
                    "profile": profile,
                    "controllers": controller_refs,
                    "time_to_cash_out": time_to_cash_out,
                },
            },
            {
                "method": "POST",
                "endpoint": "bots/deploy",
                "payload": {
                    "bot_name": name,
                    "controller_configs": controller_refs,
                    "script_name": script_name,
                    "image_name": image_name,
                    "credentials": credentials_name,
                    "time_to_cash_out": time_to_cash_out,
                },
            },
        ]
        endpoint, response = await self.request_candidates(candidates)
        return response if endpoint else {"error": "Unable to deploy controller bot", "status": 404}

    async def create_bot(
        self,
        name: str,
        profile: str,
        controllers: Sequence[Any],
        script_name: str = "v2_with_controllers.py",
        image_name: str = "hummingbot/hummingbot:latest",
        time_to_cash_out: Optional[int] = None,
    ):
        return await self.deploy_v2_controllers(
            name=name,
            profile=profile,
            controllers=controllers,
            script_name=script_name,
            image_name=image_name,
            credentials=profile,
            time_to_cash_out=time_to_cash_out,
        )

    async def deploy_script_with_controllers(
        self,
        bot_name: str,
        controller_configs: Sequence[Any],
        script_name: str,
        image_name: str,
        credentials: str,
        time_to_cash_out: Optional[int] = None,
    ):
        return await self.deploy_v2_controllers(
            name=bot_name,
            profile=credentials,
            controllers=controller_configs,
            script_name=script_name,
            image_name=image_name,
            credentials=credentials,
            time_to_cash_out=time_to_cash_out,
        )

    async def stop_controller_from_bot(self, bot_name: str, controller_id: str):
        candidates = [
            {"method": "POST", "endpoint": f"bots/{bot_name}/controllers/{controller_id}/stop"},
            {
                "method": "POST",
                "endpoint": f"bots/{bot_name}/controllers/stop",
                "payload": {"controller_id": controller_id},
            },
        ]
        endpoint, response = await self.request_candidates(candidates)
        return response if endpoint else {"error": "Unable to stop controller", "status": 404}

    async def stop_bot(self, bot_name: str):
        endpoints = (
            f"bots/{bot_name}/stop",
            f"bot-orchestration/{bot_name}/stop",
        )
        endpoint, response = await self.request_first("POST", endpoints)
        return response if endpoint else {"error": "Unable to stop bot", "status": 404}

    async def start_bot(self, bot_name: str):
        endpoints = (
            f"bots/{bot_name}/start",
            f"bot-orchestration/{bot_name}/start",
        )
        endpoint, response = await self.request_first("POST", endpoints)
        return response if endpoint else {"error": "Unable to start bot", "status": 404}

    async def start_container(self, container_name: str):
        endpoints = (
            f"docker/containers/{container_name}/start",
            f"containers/{container_name}/start",
        )
        endpoint, response = await self.request_first("POST", endpoints)
        return response if endpoint else {"error": "Unable to start container", "status": 404}

    async def stop_container(self, bot_name: str):
        endpoints = (
            f"docker/containers/{bot_name}/stop",
            f"containers/{bot_name}/stop",
        )
        endpoint, response = await self.request_first("POST", endpoints)
        return response if endpoint else {"error": "Unable to stop container", "status": 404}

    async def remove_container(self, bot_name: str, archive_locally: bool = False):
        candidates = [
            {
                "method": "DELETE",
                "endpoint": f"docker/containers/{bot_name}",
                "payload": {"archive_locally": archive_locally},
            },
            {
                "method": "POST",
                "endpoint": f"containers/{bot_name}/remove",
                "payload": {"archive_locally": archive_locally},
            },
        ]
        endpoint, response = await self.request_candidates(candidates)
        return response if endpoint else {"error": "Unable to remove container", "status": 404}

    async def docker_is_running(self):
        endpoint, response = await self.request_first("GET", self.DOCKER_STATUS_ENDPOINTS)
        return response if endpoint else {}

    async def get_active_containers(self):
        endpoint, response = await self.request_first("GET", self.CONTAINERS_ENDPOINTS)
        return response if endpoint else {}

    async def get_runtime_snapshot(self) -> Dict[str, Any]:
        health = await self.get_health()
        bots_endpoint, bots_payload = await self.request_first("GET", self.BOTS_STATUS_ENDPOINTS)
        balances_endpoint, balances_payload = await self.request_first("GET", self.BALANCE_ENDPOINTS)
        performance_endpoint, performance_payload = await self.request_first("GET", self.PERFORMANCE_ENDPOINTS)

        bots = self.merge_bot_entries(
            self.normalize_bot_entries(bots_payload),
            self.normalize_bot_entries(performance_payload),
        )
        balances = self.normalize_balance_entries(balances_payload)
        portfolio_value = self.extract_portfolio_value(balances_payload, performance_payload, balances)
        bot_summary = self.summarize_bots(bots)

        return {
            "api_available": health["healthy"] or any([bots_endpoint, balances_endpoint, performance_endpoint]),
            "health": health,
            "sources": {
                "bots_endpoint": bots_endpoint,
                "balances_endpoint": balances_endpoint,
                "performance_endpoint": performance_endpoint,
            },
            "balances": balances,
            "bots": bots,
            "portfolio_value": portfolio_value,
            "bot_summary": bot_summary,
            "raw": {
                "bots": bots_payload,
                "balances": balances_payload,
                "performance": performance_payload,
            },
        }

    @classmethod
    def normalize_named_map(cls, payload: Any) -> Dict[str, Any]:
        data = cls.unwrap_data(payload)
        if isinstance(data, dict):
            if "bots" in data and isinstance(data["bots"], dict):
                data = data["bots"]
            return {str(key): value for key, value in data.items()}
        if isinstance(data, list):
            result: Dict[str, Any] = {}
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("bot_name") or entry.get("name") or entry.get("id") or f"bot_{len(result)}")
                result[name] = entry
            return result
        return {}

    @classmethod
    def normalize_bot_entries(cls, payload: Any) -> List[Dict[str, Any]]:
        data = cls.unwrap_data(payload)
        if isinstance(data, dict) and "bots" in data and data["bots"] is not None:
            data = data["bots"]

        normalized: List[Dict[str, Any]] = []
        if isinstance(data, dict):
            for bot_name, value in data.items():
                entry = value if isinstance(value, dict) else {"status": value}
                normalized.append(cls._normalize_bot_entry(entry, fallback_name=str(bot_name)))
        elif isinstance(data, list):
            for entry in data:
                if isinstance(entry, dict):
                    normalized.append(cls._normalize_bot_entry(entry))
        return normalized

    @classmethod
    def normalize_balance_entries(cls, payload: Any) -> List[Dict[str, Any]]:
        data = cls.unwrap_data(payload)
        if isinstance(data, dict):
            for key in ("balances", "assets", "tokens", "holdings"):
                if key in data and data[key] is not None:
                    data = data[key]
                    break

        normalized: List[Dict[str, Any]] = []
        if isinstance(data, dict):
            for asset, value in data.items():
                if isinstance(value, dict):
                    normalized.append(
                        {
                            "asset": str(value.get("asset") or value.get("symbol") or asset),
                            "total": cls.extract_numeric(value, ["total", "balance", "amount", "quantity"]),
                            "available": cls.extract_numeric(value, ["available", "free", "available_balance"]),
                            "usd_value": cls.extract_numeric(
                                value,
                                ["usd_value", "value_usd", "notional_usd", "total_value_usd", "total_value", "value"],
                            ),
                        }
                    )
                else:
                    normalized.append(
                        {"asset": str(asset), "total": cls.coerce_float(value), "available": None, "usd_value": None}
                    )
        elif isinstance(data, list):
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                normalized.append(
                    {
                        "asset": str(entry.get("asset") or entry.get("symbol") or entry.get("token") or "unknown"),
                        "total": cls.extract_numeric(entry, ["total", "balance", "amount", "quantity"]),
                        "available": cls.extract_numeric(entry, ["available", "free", "available_balance"]),
                        "usd_value": cls.extract_numeric(
                            entry,
                            ["usd_value", "value_usd", "notional_usd", "total_value_usd", "total_value", "value"],
                        ),
                    }
                )

        normalized.sort(key=lambda item: item.get("usd_value") or 0.0, reverse=True)
        return normalized

    @classmethod
    def merge_bot_entries(cls, primary: List[Dict[str, Any]], secondary: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        for entry in primary + secondary:
            bot_name = entry.get("bot_name") or "unknown"
            current = merged.setdefault(bot_name, {"bot_name": bot_name})
            for key, value in entry.items():
                if value is not None and value != "unknown":
                    current[key] = value
        result = list(merged.values())
        result.sort(key=lambda item: item.get("bot_name", ""))
        return result

    @classmethod
    def summarize_bots(cls, bots: List[Dict[str, Any]]) -> Dict[str, Any]:
        active_statuses = {"running", "active", "online"}
        stopped_statuses = {"stopped", "offline", "error", "failed", "inactive"}

        active = 0
        stopped = 0
        total_pnl = 0.0
        pnl_count = 0
        for bot in bots:
            status = str(bot.get("status") or "unknown").lower()
            if status in active_statuses:
                active += 1
            elif status in stopped_statuses:
                stopped += 1

            pnl = bot.get("pnl")
            if pnl is not None:
                total_pnl += float(pnl)
                pnl_count += 1

        return {
            "total": len(bots),
            "active": active,
            "stopped": stopped,
            "unknown": max(0, len(bots) - active - stopped),
            "total_pnl": total_pnl if pnl_count else None,
        }

    @classmethod
    def extract_portfolio_value(
        cls,
        balances_payload: Any,
        performance_payload: Any,
        balances: List[Dict[str, Any]],
    ) -> Optional[float]:
        for payload in (performance_payload, balances_payload):
            data = cls.unwrap_data(payload)
            if isinstance(data, dict):
                for key in ("portfolio_value", "total_value", "total_value_usd", "nav", "equity", "usd_value", "value"):
                    value = cls.coerce_float(data.get(key))
                    if value is not None:
                        return value

        usd_values = [balance.get("usd_value") for balance in balances if balance.get("usd_value") is not None]
        if usd_values:
            return float(sum(usd_values))
        return None

    @classmethod
    def extract_bot_performance_map(cls, bot_payload: Any) -> Dict[str, Any]:
        if not isinstance(bot_payload, dict):
            return {}

        candidate_values = [
            bot_payload.get("performance"),
            bot_payload.get("controllers"),
            bot_payload.get("controller_performance"),
        ]
        for candidate in candidate_values:
            if isinstance(candidate, dict):
                if "controllers" in candidate and isinstance(candidate["controllers"], dict):
                    return candidate["controllers"]
                if any(isinstance(value, dict) for value in candidate.values()):
                    return candidate
        return {}

    @staticmethod
    def extract_error_logs(bot_payload: Any) -> List[Any]:
        if not isinstance(bot_payload, dict):
            return []
        for key in ("error_logs", "errors", "logs"):
            value = bot_payload.get(key)
            if isinstance(value, list):
                return value
        return []

    @classmethod
    def extract_numeric(cls, payload: Dict[str, Any], keys: List[str]) -> Optional[float]:
        for key in keys:
            if key in payload:
                value = cls.coerce_float(payload.get(key))
                if value is not None:
                    return value
        return None

    @classmethod
    def _normalize_bot_entry(cls, entry: Dict[str, Any], fallback_name: str = "unknown") -> Dict[str, Any]:
        return {
            "bot_name": str(entry.get("bot_name") or entry.get("name") or entry.get("id") or fallback_name),
            "status": cls._normalize_status(entry),
            "pnl": cls.extract_numeric(entry, ["pnl_quote", "net_pnl", "pnl", "pnl_usd", "total_pnl", "performance"]),
            "trading_pair": entry.get("trading_pair") or entry.get("market"),
            "connector": entry.get("connector") or entry.get("exchange"),
            "strategy": entry.get("strategy") or entry.get("controller"),
            "error": entry.get("error"),
        }

    @staticmethod
    def _normalize_status(entry: Dict[str, Any]) -> str:
        status = entry.get("status") or entry.get("state")
        if isinstance(status, bool):
            return "running" if status else "stopped"
        if status is not None:
            return str(status).lower()
        if entry.get("running") is True or entry.get("is_running") is True:
            return "running"
        if entry.get("running") is False or entry.get("is_running") is False:
            return "stopped"
        return "unknown"

    @staticmethod
    def _as_controller_references(controllers: Sequence[Any]) -> List[str]:
        references: List[str] = []
        for controller in controllers:
            if isinstance(controller, str):
                references.append(controller if controller.endswith(".yml") else f"{controller}.yml")
                continue

            if isinstance(controller, dict):
                controller_id = controller.get("id") or controller.get("name")
                if controller_id:
                    references.append(f"{controller_id}.yml")
        return references
