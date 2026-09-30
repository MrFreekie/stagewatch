"""Which addresses can tablets use to reach this computer?  (stdlib + ifaddr; no server imports.)"""

from __future__ import annotations

import ipaddress

import ifaddr


def local_ipv4() -> list[str]:
    """LAN IPv4 addresses of this machine: loopback and link-local (169.254.x.x) excluded,
    no duplicates, adapter order kept."""
    out: list[str] = []
    for adapter in ifaddr.get_adapters():
        for ip in adapter.ips:
            if isinstance(ip.ip, str):
                try:
                    addr = ipaddress.ip_address(ip.ip)
                except ValueError:
                    continue
                if addr.is_loopback or addr.is_link_local or addr.is_unspecified or addr.is_multicast:
                    continue
                if ip.ip not in out:
                    out.append(ip.ip)
    return out


def connect_urls(addresses: list[str], port: int, slug: str = "", mdns_name: str = "") -> dict:
    """URLs for a tablet: one per LAN address, plus the optional `<name>.local` one."""
    path = f"/d/{slug}" if slug else ""
    return {
        "ip": [f"http://{a}:{int(port)}{path}" for a in addresses],
        "mdns": f"http://{mdns_name}.local:{int(port)}{path}" if mdns_name else "",
    }
