"""Cloud provider abstraction for remote-claude.

Supports Hetzner Cloud and DigitalOcean via a common interface.
"""

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod


class CloudProvider(ABC):
    """Abstract cloud provider interface."""

    @abstractmethod
    def create_server(
        self, name: str, server_type: str, region: str, user_data: str
    ) -> dict:
        """Create a cloud server.

        Args:
            name: Server hostname
            server_type: Provider-specific server size
            region: Provider-specific region shorthand
            user_data: Cloud-init user data

        Returns:
            {"server_id": int}
        """

    @abstractmethod
    def destroy_server(self, server_id: int) -> bool:
        """Destroy a cloud server.

        Args:
            server_id: Provider-specific server ID

        Returns:
            True if destroyed successfully
        """

    @abstractmethod
    def default_server_type(self) -> str:
        """Default server size for this provider."""

    @abstractmethod
    def default_region(self) -> str:
        """Default region for this provider."""

    @abstractmethod
    def region_map(self) -> dict[str, str]:
        """Map of region shorthands to provider-specific identifiers."""

    def _api_request(
        self, method: str, url: str, token: str, data: dict | None = None
    ) -> dict:
        """Make an authenticated API request.

        Both Hetzner and DigitalOcean use Bearer token auth with JSON bodies.
        """
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                response_body = resp.read().decode()
                return json.loads(response_body) if response_body else {}
        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            raise RuntimeError(
                f"API error: {e.code} {e.reason}\n{error_body}"
            )


class HetznerProvider(CloudProvider):
    """Hetzner Cloud provider."""

    API_URL = "https://api.hetzner.cloud/v1"
    LABEL_KEY = "purpose"
    LABEL_VALUE = "remote-claude"

    def __init__(self, token: str):
        self._token = token

    def create_server(
        self, name: str, server_type: str, region: str, user_data: str
    ) -> dict:
        datacenter = self.region_map().get(region, region)

        resp = self._api_request("POST", f"{self.API_URL}/servers", self._token, {
            "name": name,
            "server_type": server_type,
            "image": "ubuntu-24.04",
            "datacenter": datacenter,
            "user_data": user_data,
            "labels": {self.LABEL_KEY: self.LABEL_VALUE},
            "public_net": {
                "enable_ipv4": True,
                "enable_ipv6": True,
            },
        })

        server = resp.get("server", {})
        server_id = server.get("id", 0)
        if not server_id:
            raise RuntimeError(f"Failed to create server: {json.dumps(resp, indent=2)}")

        return {"server_id": server_id}

    def destroy_server(self, server_id: int) -> bool:
        self._api_request("DELETE", f"{self.API_URL}/servers/{server_id}", self._token)
        return True

    def default_server_type(self) -> str:
        return "cpx21"

    def default_region(self) -> str:
        return "ash"

    def region_map(self) -> dict[str, str]:
        return {
            "ash": "ash-dc1",
            "hil": "hil-dc1",
            "fsn": "fsn1-dc14",
            "nbg": "nbg1-dc3",
            "hel": "hel1-dc2",
        }


class DigitalOceanProvider(CloudProvider):
    """DigitalOcean provider."""

    API_URL = "https://api.digitalocean.com/v2"

    def __init__(self, token: str):
        self._token = token

    def create_server(
        self, name: str, server_type: str, region: str, user_data: str
    ) -> dict:
        do_region = self.region_map().get(region, region)

        resp = self._api_request("POST", f"{self.API_URL}/droplets", self._token, {
            "name": name,
            "size": server_type,
            "image": "ubuntu-24-04-x64",
            "region": do_region,
            "user_data": user_data,
            "tags": ["remote-claude"],
        })

        droplet = resp.get("droplet", {})
        droplet_id = droplet.get("id", 0)
        if not droplet_id:
            raise RuntimeError(f"Failed to create droplet: {json.dumps(resp, indent=2)}")

        return {"server_id": droplet_id}

    def destroy_server(self, server_id: int) -> bool:
        self._api_request(
            "DELETE", f"{self.API_URL}/droplets/{server_id}", self._token
        )
        return True

    def default_server_type(self) -> str:
        return "s-2vcpu-4gb"

    def default_region(self) -> str:
        return "nyc3"

    def region_map(self) -> dict[str, str]:
        return {
            "nyc": "nyc3",
            "sfo": "sfo3",
            "lon": "lon1",
            "ams": "ams3",
            "sgp": "sgp1",
            "tor": "tor1",
            "blr": "blr1",
            "syd": "syd1",
        }


PROVIDERS = {
    "hetzner": HetznerProvider,
    "digitalocean": DigitalOceanProvider,
}


def get_provider(provider_name: str, token: str) -> CloudProvider:
    """Create a cloud provider instance.

    Args:
        provider_name: "hetzner" or "digitalocean"
        token: API token

    Returns:
        CloudProvider instance
    """
    cls = PROVIDERS.get(provider_name)
    if not cls:
        raise RuntimeError(
            f"Unknown cloud provider: {provider_name!r}. "
            f"Supported: {', '.join(PROVIDERS)}"
        )
    return cls(token)
