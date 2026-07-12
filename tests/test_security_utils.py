import pytest

from security_utils import validate_api_url


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://mooc2-ans.chaoxing.com/course/list", "学习通"),
        ("https://www.icourse163.org/web/j/course.rpc", "中国大学 MOOC"),
        ("https://onlineservice-api.zhihuishu.com/gateway/course", "智慧树"),
        ("https://v.guet.edu.cn/ntf/users/example/notifications", "学校作业平台"),
    ],
)
def test_api_url_accepts_platform_allowlist(url, platform):
    assert validate_api_url(url, platform) == url


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/course/list",
        "http://127.0.0.1/course/list",
        "http://192.168.1.10/course/list",
        "https://example.com/course/list",
    ],
)
def test_api_url_rejects_unsafe_destinations(url):
    with pytest.raises(ValueError):
        validate_api_url(url, "学习通")
