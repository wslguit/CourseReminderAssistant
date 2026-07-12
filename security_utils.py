import ipaddress
from urllib.parse import urlparse


PLATFORM_ALLOWED_DOMAINS = {
    "学习通": ("chaoxing.com",),
    "中国大学 MOOC": ("icourse163.org",),
    "智慧树": ("zhihuishu.com",),
    "学校作业平台": ("guet.edu.cn",),
}


def validate_api_url(url, platform_name):
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("接口 URL 只允许使用 HTTP 或 HTTPS。")
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if not hostname or hostname == "localhost":
        raise ValueError("接口 URL 缺少有效域名。")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and (not address.is_global or address.is_loopback):
        raise ValueError("接口 URL 不允许使用本机或内网 IP。")

    allowed_domains = PLATFORM_ALLOWED_DOMAINS.get(platform_name, ())
    if not any(hostname == domain or hostname.endswith(f".{domain}") for domain in allowed_domains):
        allowed_text = "、".join(allowed_domains) or "该平台的受信任域名"
        raise ValueError(f"接口 URL 必须属于 {allowed_text}。")
    return parsed.geturl()
