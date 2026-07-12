import hashlib
import html
import json
import re
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests

from time_utils import parse_datetime
from security_utils import validate_api_url


SUPPORTED_PLATFORMS = ["学习通", "中国大学 MOOC", "智慧树"]
ASSIGNMENT_PLATFORMS = ["学校作业平台"]


class ScraperError(Exception):
    pass


def supported_platforms():
    return SUPPORTED_PLATFORMS


def supported_assignment_platforms():
    return ASSIGNMENT_PLATFORMS


def platform_login_url(platform_name):
    return {
        "学习通": "https://i.chaoxing.com/",
        "中国大学 MOOC": "https://www.icourse163.org/",
        "智慧树": "https://onlineweb.zhihuishu.com/",
        "学校作业平台": "https://v.guet.edu.cn/",
    }.get(platform_name, "")


def crawl_assignment_tasks(platform_name, account):
    api_url = (account["api_url"] or "").strip()
    cookie = (account["auth_cookie"] or "").strip()
    if not api_url:
        raise ScraperError("请先填写作业通知接口 URL。")
    if not cookie:
        raise ScraperError("请先填写该平台的 Cookie。")
    if platform_name != "学校作业平台":
        raise ScraperError(f"暂不支持读取 {platform_name} 作业任务。")
    try:
        validate_api_url(api_url, platform_name)
    except ValueError as exc:
        raise ScraperError(str(exc)) from exc
    return _crawl_school_assignment_tasks(api_url, cookie, account)


def crawl_platform_courses(platform_name, account):
    api_url = (account["api_url"] or "").strip()
    cookie = (account["auth_cookie"] or "").strip()
    if not api_url:
        raise ScraperError("请先在平台绑定页填写课程接口 URL。")
    if not cookie:
        raise ScraperError("请先在平台绑定页填写该平台的 Cookie。")
    try:
        validate_api_url(api_url, platform_name)
    except ValueError as exc:
        raise ScraperError(str(exc)) from exc

    headers = {
        "Cookie": cookie,
        "User-Agent": account["user_agent"] or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0 Safari/537.36"
        ),
        "Accept": "text/html, application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }
    if account["referer"]:
        headers["Referer"] = account["referer"]

    if platform_name == "中国大学 MOOC":
        return _crawl_mooc_courses(api_url, cookie, headers, account)
    if platform_name == "智慧树":
        return _crawl_zhihuishu_courses(api_url, headers, account)

    method = (account["api_method"] or "GET").upper()
    api_body = account["api_body"] or ""
    try:
        if method == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
            response = requests.post(api_url, headers=headers, data=api_body, timeout=20)
        else:
            response = requests.get(api_url, headers=headers, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ScraperError(f"接口请求失败：{exc}") from exc

    if platform_name == "学习通":
        courses = _extract_chaoxing_courses(response.text, api_url)
        if courses:
            return courses
        raise ScraperError(
            "学习通接口请求成功，但返回内容里没有解析到课程。请重点检查 Cookie 是否完整、Referer 是否填写、请求体参数是否为 courselistdata 的 Form Data。"
        )

    data = _parse_response_json(response.text)
    courses = _extract_courses_from_json(platform_name, data)
    if not courses:
        raise ScraperError(
            "接口已返回数据，但没有识别到课程字段。请确认 URL 是“我的课程列表”接口，或把接口返回 JSON 发给我适配字段。"
        )
    return courses


def _crawl_mooc_courses(api_url, cookie, headers, account):
    csrf_key = _extract_mooc_csrf_key(api_url, cookie)
    if not csrf_key:
        raise ScraperError("中国大学 MOOC 接口缺少 csrfKey。请复制带 csrfKey 参数的接口 URL，或确认 Cookie 里包含 NTESSTUDYSI。")

    headers = dict(headers)
    headers.update(
        {
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.icourse163.org",
            "edu-script-token": csrf_key,
        }
    )

    method = (account["api_method"] or "POST").upper()
    api_body = account["api_body"] or "type=30&p=1&psize=8&courseType=2"
    first_params = _parse_form_body(api_body)
    psize = _safe_int(first_params.get("psize", ["8"])[0], 8)
    max_pages = 10
    courses = []
    seen = set()

    try:
        for page in range(1, max_pages + 1):
            request_url = _replace_query_param(api_url, "csrfKey", csrf_key)
            request_body = api_body
            if method == "POST":
                page_params = _parse_form_body(api_body)
                page_params["p"] = [str(page)]
                request_body = urlencode(page_params, doseq=True)
                response = requests.post(request_url, headers=headers, data=request_body, timeout=20)
            else:
                request_url = _replace_query_param(request_url, "p", str(page))
                response = requests.get(request_url, headers=headers, timeout=20)
            response.raise_for_status()
            data = _parse_response_json(response.text)
            page_courses = _extract_mooc_courses(data)
            for course in page_courses:
                key = f"{course['course_name']}|{course.get('teacher', '')}|{course.get('course_url', '')}"
                if key in seen:
                    continue
                seen.add(key)
                courses.append(course)
            if not page_courses or len(page_courses) < psize:
                break
    except requests.RequestException as exc:
        raise ScraperError(f"中国大学 MOOC 接口请求失败：{exc}") from exc

    if not courses:
        raise ScraperError("中国大学 MOOC 接口请求成功，但没有解析到课程。请检查 Cookie、Referer、csrfKey 和请求体参数。")
    return courses


def _crawl_zhihuishu_courses(api_url, headers, account):
    headers = dict(headers)
    headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://onlineweb.zhihuishu.com",
        }
    )
    if not headers.get("Referer"):
        headers["Referer"] = "https://onlineweb.zhihuishu.com/"

    method = (account["api_method"] or "POST").upper()
    api_body = account["api_body"] or ""
    if not api_body and method == "POST":
        raise ScraperError("智慧树接口需要填写请求体参数，例如 secretStr=...&date=...")

    try:
        if method == "POST":
            response = requests.post(api_url, headers=headers, data=api_body, timeout=20)
        else:
            response = requests.get(api_url, headers=headers, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ScraperError(f"智慧树接口请求失败：{exc}") from exc

    data = _parse_response_json(response.text)
    courses = _extract_zhihuishu_courses(data)
    if not courses:
        raise ScraperError("智慧树接口请求成功，但没有解析到课程。请确认接口返回里包含 courseOpenDtos。")
    return courses


def _crawl_school_assignment_tasks(api_url, cookie, account):
    headers = {
        "Cookie": cookie,
        "User-Agent": account["user_agent"] or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }
    if account["referer"]:
        headers["Referer"] = account["referer"]

    limit = _assignment_limit(api_url)
    max_pages = 20
    tasks = []
    seen = set()

    try:
        for page_index in range(max_pages):
            offset = page_index * limit
            request_url = _assignment_page_url(api_url, offset, limit)
            response = requests.get(request_url, headers=headers, timeout=20)
            response.raise_for_status()
            data = _parse_response_json(response.text)
            raw_items = _school_notification_items(data)
            page_tasks = _extract_school_assignment_tasks(raw_items)
            for task in page_tasks:
                deadline = _datetime_from_text(task.get("deadline_time"))
                if deadline and deadline < datetime.now():
                    continue
                key = task["external_id"]
                if key in seen:
                    continue
                seen.add(key)
                tasks.append(task)
            if not raw_items:
                break
    except requests.RequestException as exc:
        raise ScraperError(f"学校作业平台接口请求失败：{exc}") from exc

    if not tasks:
        raise ScraperError("学校作业平台接口请求成功，但没有解析到未逾期的作业/测试截止通知。请确认接口返回的是通知列表，或当前确实没有未逾期任务。")
    return tasks


def crawl_course_details(_platform_name, _account, courses):
    return courses


def _assignment_limit(api_url):
    query = parse_qs(urlparse(api_url).query)
    value = query.get("limit", ["5"])[0]
    return max(1, min(100, _safe_int(value, 5)))


def _assignment_page_url(api_url, offset, limit):
    parsed = urlparse(api_url)
    pairs = _split_query_pairs(parsed.query)
    bare_keys = [key for key, value in pairs if value is None]
    query = {}
    for key, value in pairs:
        if value is not None:
            query[key] = value
    query["limit"] = str(limit)
    query["removed"] = query.get("removed") or "only_mobile"
    query["additionalFields"] = "total_count"
    if offset > 0:
        query["offset"] = str(offset)
    else:
        query.pop("offset", None)
    query_parts = bare_keys + [f"{key}={value}" for key, value in query.items()]
    return urlunparse(parsed._replace(query="&".join(query_parts)))


def _split_query_pairs(query):
    pairs = []
    for part in (query or "").split("&"):
        if not part:
            continue
        if "=" in part:
            key, value = part.split("=", 1)
            pairs.append((key, value))
        else:
            pairs.append((part, None))
    return pairs


def _extract_total_count(data):
    for value in _walk_values(data):
        if isinstance(value, dict):
            for key in ["total_count", "totalCount", "total", "count"]:
                if key in value:
                    total = _safe_int(value.get(key), -1)
                    if total >= 0:
                        return total
    return None


def _extract_school_assignment_tasks(data):
    items = _school_notification_items(data)
    tasks = []
    seen = set()
    for item in items:
        parsed = _parse_school_notification_task(item)
        if not parsed:
            continue
        if parsed["external_id"] in seen:
            continue
        seen.add(parsed["external_id"])
        tasks.append(parsed)
    return tasks


def _school_notification_items(data):
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []

    candidates = []
    for key in ["notifications", "data", "items", "list", "results", "result"]:
        value = data.get(key)
        if isinstance(value, list):
            candidates.extend(item for item in value if isinstance(item, dict))
        elif isinstance(value, dict):
            candidates.extend(_school_notification_items(value))
    if candidates:
        return candidates

    nested = []
    for value in data.values():
        if isinstance(value, (dict, list)):
            nested.extend(_school_notification_items(value))
    return nested


def _parse_school_notification_task(item):
    raw_text = _school_notification_text(item)
    if not raw_text:
        return None
    normalized = _clean_text(raw_text)
    if "截止" not in normalized or "课程" not in normalized:
        return None
    if "作业" not in normalized and "测试" not in normalized and "在线测试" not in normalized:
        return None

    deadline = _extract_school_deadline(normalized)
    if not deadline:
        return None

    task_type = "作业" if "作业" in normalized else "在线测试"
    course_name = _extract_between(normalized, "课程", f"的{task_type}")
    if not course_name and task_type == "在线测试":
        course_name = _extract_between(normalized, "课程", "的测试")
    title = _extract_school_task_title(normalized, task_type)
    if not course_name or not title:
        return None

    publish_time = _first_time(
        item,
        [
            "created_at",
            "createdAt",
            "created_time",
            "createdTime",
            "publish_time",
            "publishTime",
            "send_time",
            "sendTime",
            "time",
        ],
    )
    status = "已截止" if _datetime_from_text(deadline) and _datetime_from_text(deadline) < datetime.now() else "进行中"
    raw_id = _first_text(item, ["id", "notification_id", "notificationId", "message_id", "messageId"])
    task_url = _first_text(item, ["url", "link", "href", "target_url", "targetUrl"])
    identity = raw_id or f"{course_name}|{task_type}|{title}|{deadline}|{normalized}"

    return {
        "platform_name": "学校作业平台",
        "task_type": task_type,
        "course_name": _clean_text(course_name),
        "task_title": _clean_text(title),
        "task_url": _clean_text(task_url),
        "publish_time": publish_time,
        "deadline_time": deadline,
        "status": status,
        "external_id": hashlib.md5(identity.encode("utf-8")).hexdigest(),
        "raw_text": normalized,
    }


def _school_notification_text(item):
    parts = []
    for key in [
        "content",
        "message",
        "body",
        "title",
        "summary",
        "description",
        "text",
        "html",
        "brief",
    ]:
        value = item.get(key)
        if value:
            parts.append(_html_to_text(str(value)))
    if not parts:
        for value in item.values():
            if isinstance(value, str) and ("课程" in value or "截止" in value):
                parts.append(_html_to_text(value))
    return " ".join(part for part in parts if part)


def _extract_school_deadline(text):
    match = re.search(r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})\s+(\d{1,2}):(\d{1,2})\s*截止", text)
    if not match:
        match = re.search(r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})\s+(\d{1,2}):(\d{1,2})", text)
    if not match:
        return None
    year, month, day, hour, minute = (int(part) for part in match.groups())
    return datetime(year, month, day, hour, minute).strftime("%Y-%m-%d %H:%M:%S")


def _extract_school_task_title(text, task_type):
    if task_type == "作业":
        patterns = [
            r"的作业\s*(.*?)\s*提交即将于",
            r"的作业\s*(.*?)\s*即将于",
        ]
    else:
        patterns = [
            r"的测试\s*(.*?)\s*即将于",
            r"的在线测试\s*(.*?)\s*即将于",
        ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def _extract_between(text, start, end):
    pattern = rf"{re.escape(start)}\s*(.*?)\s*{re.escape(end)}"
    match = re.search(pattern, text)
    return match.group(1) if match else ""


def _walk_values(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_values(child)


def _extract_zhihuishu_courses(data):
    raw_items = _zhihuishu_course_items(data)
    if not raw_items:
        raw_items = []
        _walk_json(data, raw_items)

    courses = []
    seen = set()
    for item in raw_items:
        parsed = _parse_zhihuishu_course_dict(item)
        if not parsed:
            continue
        key = f"{parsed['course_name']}|{parsed.get('teacher', '')}|{parsed.get('course_url', '')}"
        if key in seen:
            continue
        seen.add(key)
        parsed["external_id"] = hashlib.md5(key.encode("utf-8")).hexdigest()
        courses.append(parsed)
    return courses


def _zhihuishu_course_items(data):
    if not isinstance(data, dict):
        return []
    result = data.get("result")
    if isinstance(result, dict):
        for key in ["courseOpenDtos", "courseDtos", "shareCourseDtos", "courseList", "list"]:
            value = result.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _parse_zhihuishu_course_dict(item):
    name = _clean_text(_first_text(item, ["courseName", "name", "title"]))
    if not name or len(name) < 2:
        return None

    teacher = _clean_text(_first_text(item, ["teacherName", "teacher", "teacherNames"]))
    school = _clean_text(_first_text(item, ["schoolName", "school", "schoolTitle"]))
    if teacher and school:
        teacher_info = f"{teacher} · {school}"
    else:
        teacher_info = teacher or school

    progress = _first_int(item, ["progress", "studyProgress", "percent", "completeRate"])
    deadline = _first_time(item, ["courseEndTime", "endTime", "deadline", "closeTime"])
    exam_time = _first_time(item, ["examTime", "exam_time", "testTime", "examStartTime"])
    status_text = _first_text(item, ["statusName", "status", "courseStatus", "studyStatus"])
    deadline_dt = _datetime_from_text(deadline)
    if deadline_dt and deadline_dt < datetime.now():
        status = "已结束"
    elif status_text:
        status = _normalize_status(status_text, progress)
    elif progress >= 100:
        status = "已完成"
    else:
        status = "进行中"

    return {
        "course_name": name,
        "course_url": _build_platform_url("智慧树", item),
        "teacher": teacher_info,
        "progress": progress,
        "deadline_time": deadline,
        "exam_time": exam_time,
        "status": status,
    }


def _extract_mooc_courses(data):
    raw_items = _mooc_course_items(data)
    if not raw_items:
        raw_items = []
        _walk_mooc_json(data, raw_items)

    courses = []
    seen = set()
    for item in raw_items:
        parsed = _parse_mooc_course_dict(item)
        if not parsed:
            continue
        key = f"{parsed['course_name']}|{parsed.get('teacher', '')}|{parsed.get('course_url', '')}"
        if key in seen:
            continue
        seen.add(key)
        parsed["external_id"] = hashlib.md5(key.encode("utf-8")).hexdigest()
        courses.append(parsed)
    return courses


def _mooc_course_items(data):
    if not isinstance(data, dict):
        return []
    current = data.get("result")
    visited = 0
    while isinstance(current, dict) and visited < 5:
        nested = current.get("result")
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
        current = nested
        visited += 1
    return []


def _walk_mooc_json(value, output):
    if isinstance(value, dict):
        if _looks_like_mooc_course_object(value):
            output.append(value)
        for child in value.values():
            _walk_mooc_json(child, output)
    elif isinstance(value, list):
        for child in value:
            _walk_mooc_json(child, output)


def _looks_like_mooc_course_object(item):
    keys = {str(key).lower() for key in item.keys()}
    if {"supportmooc", "supportspoc", "schooltype"} & keys:
        return False
    name_keys = {
        "coursename",
        "course_name",
        "name",
        "title",
        "coursecardname",
        "productname",
        "termname",
    }
    id_keys = {
        "courseid",
        "course_id",
        "id",
        "tid",
        "termid",
        "productid",
        "learnedcourseid",
    }
    text_blob = " ".join(str(value) for value in item.values() if isinstance(value, str))
    return bool(keys & name_keys) and (bool(keys & id_keys) or "SPOC" in text_blob or "MOOC" in text_blob or "课程" in text_blob)


def _parse_mooc_course_dict(item):
    term = item.get("termPanel") if isinstance(item.get("termPanel"), dict) else {}
    school = item.get("schoolPanel") if isinstance(item.get("schoolPanel"), dict) else {}
    name = _clean_text(
        _first_text(
            item,
            [
                "courseName",
                "coursename",
                "course_name",
                "name",
                "title",
                "courseCardName",
                "productName",
                "termName",
            ],
        )
    )
    if not name or len(name) < 2:
        return None

    teacher = _first_text(school, ["name", "shortName"]) or _first_text(
        item,
        [
            "teacherName",
            "teacherNameStr",
            "teacher",
            "teachers",
            "teacherList",
            "lectorName",
            "lector",
            "instructorName",
            "schoolName",
            "university",
        ],
    )
    course_url = _first_text(
        item,
        [
            "courseUrl",
            "course_url",
            "url",
            "link",
            "href",
            "learnUrl",
            "webUrl",
        ],
    )
    progress = _first_int(
        item,
        [
            "progress",
            "learnProgress",
            "studyProgress",
            "percent",
            "completeRate",
            "finishedPercent",
        ],
    )
    deadline = _first_time(
        term,
        [
            "endTime",
            "end_time",
            "termEndTime",
            "termEndTimeStamp",
            "endTimeStamp",
            "closeTime",
            "courseEndTime",
        ],
    ) or _first_time(
        item,
        [
            "deadline",
            "endTime",
            "end_time",
            "courseEndTime",
            "termEndTime",
            "termEndTimeStamp",
            "endTimeStamp",
        ],
    )
    exam_time = _first_time(term, ["examTime", "exam_time", "testTime", "examStartTime"]) or _first_time(
        item, ["examTime", "exam_time", "testTime", "examStartTime"]
    )
    status_text = _first_text(item, ["status", "state", "courseStatus", "statusName", "learnStatus", "termStatus"])
    if status_text:
        status = _normalize_status(status_text, progress)
    elif deadline and _datetime_from_text(deadline) and _datetime_from_text(deadline) < datetime.now():
        status = "已结束"
    else:
        status = "进行中"

    return {
        "course_name": name,
        "course_url": course_url or _build_platform_url("中国大学 MOOC", item),
        "teacher": _clean_text(teacher),
        "progress": progress,
        "deadline_time": deadline,
        "exam_time": exam_time,
        "status": status,
    }


def _extract_mooc_csrf_key(api_url, cookie):
    parsed = urlparse(api_url or "")
    query_value = parse_qs(parsed.query).get("csrfKey", [""])[0]
    if query_value:
        return query_value
    match = re.search(r"(?:^|;\s*)NTESSTUDYSI=([^;]+)", cookie or "")
    return match.group(1) if match else ""


def _parse_form_body(body):
    return parse_qs(body or "", keep_blank_values=True)


def _replace_query_param(url, key, value):
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query[key] = [value]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _extract_chaoxing_courses(text, base_url):
    if not text or "<" not in text:
        return []

    anchor_pattern = re.compile(
        r"<a\b(?P<attrs>[^>]*)>(?P<title>.*?)</a>",
        flags=re.I | re.S,
    )
    matches = list(anchor_pattern.finditer(text))
    candidates = []
    for index, match in enumerate(matches):
        title = _html_to_text(match.group("title"))
        if not _looks_like_chaoxing_title(title):
            continue
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segment = text[match.start():next_start]
        segment_text = _html_to_text(segment)
        href = _extract_attr(match.group("attrs"), "href")
        parsed = _parse_chaoxing_html_segment(title, href, segment, segment_text, base_url)
        if parsed:
            candidates.append(parsed)

    candidates.extend(_extract_chaoxing_courses_from_text_blocks(text, base_url))

    deduped = []
    by_name = {}
    for course in candidates:
        key = _course_name_key(course["course_name"])
        old = by_name.get(key)
        if old is None or _course_quality_score(course) > _course_quality_score(old):
            by_name[key] = course
    for course in by_name.values():
        ext_key = f"{course['course_name']}|{course.get('teacher', '')}|{course.get('deadline_time', '')}"
        course["external_id"] = hashlib.md5(ext_key.encode("utf-8")).hexdigest()
        deduped.append(course)
    return deduped


def _extract_chaoxing_courses_from_text_blocks(text, base_url):
    plain = _html_to_text(text)
    lines = [_clean_chaoxing_line(line) for line in plain.splitlines()]
    lines = [line for line in lines if line]
    courses = []
    for i, line in enumerate(lines):
        if not _looks_like_chaoxing_title(line):
            continue
        if _looks_like_chaoxing_teacher_line(lines, i):
            continue
        if not _has_chaoxing_course_context(lines, i):
            continue
        title = line
        teacher = ""
        deadline = None
        status = "进行中"
        for candidate in lines[i + 1 : i + 7]:
            if candidate == "课程已结束":
                status = "已结束"
                continue
            if candidate.startswith("开课时间"):
                deadline = _extract_chaoxing_deadline(candidate)
                break
            if not teacher and _looks_like_chaoxing_teacher(candidate):
                teacher = candidate
        key = f"{title}|{teacher}|{deadline or ''}"
        courses.append(
            {
                "external_id": hashlib.md5(key.encode("utf-8")).hexdigest(),
                "course_name": title,
                "course_url": base_url,
                "teacher": teacher,
                "progress": 0,
                "deadline_time": deadline,
                "exam_time": None,
                "status": status,
            }
        )
    return courses


def _parse_chaoxing_html_segment(title, href, segment, segment_text, base_url):
    lines = [line.strip() for line in re.split(r"[\n\r]+", segment_text) if line.strip()]
    teacher = ""
    for line in lines:
        if line in {title, "移动到", "课程已结束"}:
            continue
        if line.startswith("开课时间"):
            break
        if len(line) <= 40:
            teacher = line
            break

    deadline = _extract_chaoxing_deadline(segment_text)
    status = "已结束" if "课程已结束" in segment_text else "进行中"
    url = urljoin(base_url, href) if href else ""
    return {
        "course_name": title,
        "course_url": url,
        "teacher": teacher,
        "progress": 0,
        "deadline_time": deadline,
        "exam_time": None,
        "status": status,
    }


def _extract_chaoxing_deadline(text):
    match = re.search(r"开课时间[:：]\s*(\d{4}-\d{2}-\d{2})\s*[~～\-至]\s*(\d{4}-\d{2}-\d{2})", text)
    if match:
        return f"{match.group(2)} 00:00:00"
    return None


def _looks_like_chaoxing_title(title):
    if not title or len(title) < 2 or len(title) > 80:
        return False
    noise = {
        "课程已结束",
        "移动到",
        "首页",
        "课程",
        "添加课程",
        "新建文件夹",
        "搜索课程",
        "我学的课",
        "我教的课",
        "置顶",
        "教师",
    }
    if title in noise:
        return False
    if title.startswith("开课时间"):
        return False
    return bool(re.search(r"[\u4e00-\u9fa5A-Za-z0-9]", title))


def _clean_chaoxing_line(line):
    line = _clean_text(line).strip("•·- ")
    if line in {"", "图片", "移动到"}:
        return ""
    return line


def _has_chaoxing_course_context(lines, index):
    window = lines[max(0, index - 2) : index + 7]
    if any(line in {"课程已结束", "移动到", "置顶"} for line in window):
        return True
    if any(line.startswith("开课时间") for line in window):
        return True
    next_lines = [line for line in lines[index + 1 : index + 4] if line]
    return bool(next_lines)


def _looks_like_chaoxing_teacher_line(lines, index):
    line = lines[index]
    previous = lines[index - 1] if index > 0 else ""
    next_line = lines[index + 1] if index + 1 < len(lines) else ""
    return (
        _looks_like_chaoxing_title(previous)
        and _looks_like_chaoxing_teacher(line)
        and (next_line.startswith("开课时间") or next_line in {"课程已结束", "移动到"})
    )


def _looks_like_chaoxing_teacher(line):
    if not line or len(line) > 40:
        return False
    if line in {"课程已结束", "移动到", "置顶", "教师"}:
        return line == "教师"
    if line.startswith("开课时间"):
        return False
    return bool(re.search(r"[\u4e00-\u9fa5A-Za-z]", line))


def _course_name_key(name):
    return re.sub(r"\s+", "", str(name or "")).strip().lower()


def _course_quality_score(course):
    return (
        (10 if course.get("deadline_time") else 0)
        + (5 if course.get("teacher") else 0)
        + (2 if course.get("course_url") else 0)
        + (1 if course.get("status") == "已结束" else 0)
    )


def _extract_attr(attrs, name):
    match = re.search(rf"{name}\s*=\s*(['\"])(.*?)\1", attrs or "", flags=re.I | re.S)
    return html.unescape(match.group(2)) if match else ""


def _html_to_text(value):
    cleaned = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", "", value or "", flags=re.I | re.S)
    cleaned = re.sub(r"<br\s*/?>", "\n", cleaned, flags=re.I)
    cleaned = re.sub(r"</(p|div|li|h\d|a)>", "\n", cleaned, flags=re.I)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"[ \t\r\f\v]+", " ", cleaned)
    cleaned = re.sub(r"\n\s*", "\n", cleaned)
    return cleaned.strip()


def _parse_response_json(text):
    cleaned = (text or "").strip()
    if not cleaned:
        raise ScraperError("接口返回内容为空，可能是 Cookie 过期或接口地址不正确。")
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}|\[.*\]", cleaned, flags=re.S)
        if not match:
            raise ScraperError("接口返回的不是 JSON 数据，请确认复制的是 Fetch/XHR 课程接口。")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ScraperError("接口返回内容不是标准 JSON，也没有可解析的课程 HTML。") from exc


def _extract_courses_from_json(platform_name, data):
    raw_items = []
    _walk_json(data, raw_items)

    courses = []
    seen = set()
    for item in raw_items:
        parsed = _parse_course_dict(platform_name, item)
        if not parsed:
            continue
        key = f"{parsed['course_name']}|{parsed.get('teacher', '')}|{parsed.get('course_url', '')}"
        if key in seen:
            continue
        seen.add(key)
        parsed["external_id"] = hashlib.md5(key.encode("utf-8")).hexdigest()
        courses.append(parsed)
    return courses[:100]


def _walk_json(value, output):
    if isinstance(value, dict):
        if _looks_like_course_object(value):
            output.append(value)
        for child in value.values():
            _walk_json(child, output)
    elif isinstance(value, list):
        for child in value:
            _walk_json(child, output)


def _looks_like_course_object(item):
    keys = {str(key).lower() for key in item.keys()}
    name_keys = {
        "coursename",
        "course_name",
        "name",
        "title",
        "course_title",
        "coursetitle",
        "clazzname",
        "classname",
        "cpi_name",
        "lessonname",
        "classroomname",
    }
    id_keys = {"courseid", "course_id", "clazzid", "classid", "id", "lessonid", "classroomid"}
    text_blob = " ".join(str(value) for value in item.values() if isinstance(value, str))
    return bool(keys & name_keys) and (bool(keys & id_keys) or "课程" in text_blob or len(text_blob) >= 3)


def _parse_course_dict(platform_name, item):
    name = _first_text(
        item,
        [
            "courseName",
            "coursename",
            "course_name",
            "name",
            "title",
            "courseTitle",
            "course_title",
            "clazzName",
            "className",
            "cpi_name",
            "lessonName",
            "classRoomName",
            "classroomName",
        ],
    )
    name = _clean_text(name)
    if not name or len(name) < 2:
        return None

    teacher = _first_text(
        item,
        [
            "teacherName",
            "teacher",
            "teachers",
            "teacherList",
            "teacherNames",
            "tName",
            "createrName",
            "ownerName",
            "userName",
            "nickName",
            "schoolName",
            "className",
            "clazzName",
        ],
    )
    course_url = _first_text(item, ["courseUrl", "url", "link", "href", "course_url"])
    progress = _first_int(item, ["progress", "studyProgress", "percent", "completeRate", "finishedPercent", "learnProgress"])
    deadline = _first_time(item, ["deadline", "endTime", "end_time", "closeTime", "courseEndTime", "termEndTime"])
    exam_time = _first_time(item, ["examTime", "exam_time", "testTime", "examStartTime"])
    status = _first_text(item, ["status", "state", "courseStatus", "statusName", "studyStatus"]) or _status_from_progress(progress)

    return {
        "course_name": name,
        "course_url": course_url or _build_platform_url(platform_name, item),
        "teacher": _clean_text(teacher),
        "progress": progress,
        "deadline_time": deadline,
        "exam_time": exam_time,
        "status": _normalize_status(status, progress),
    }


def _first_text(item, keys):
    for key in keys:
        if key in item and item[key] is not None:
            value = item[key]
            if isinstance(value, list):
                return "、".join(_stringify_value(part) for part in value if part)
            if isinstance(value, dict):
                return _stringify_value(value)
            return str(value)
    lower_map = {str(key).lower(): value for key, value in item.items()}
    for key in keys:
        value = lower_map.get(key.lower())
        if value is not None:
            return str(value)
    return ""


def _first_int(item, keys):
    value = _first_text(item, keys)
    if not value:
        return 0
    match = re.search(r"\d{1,3}", value)
    if not match:
        return 0
    return max(0, min(100, int(match.group(0))))


def _first_time(item, keys):
    value = _first_text(item, keys)
    if not value:
        return None
    value = str(value).strip()
    if value.isdigit() and len(value) >= 10:
        timestamp = int(value[:10])
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    cleaned = (
        value.replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
        .replace("/", "-")
        .strip()
    )
    parsed = parse_datetime(cleaned)
    return parsed.strftime("%Y-%m-%d %H:%M:%S") if parsed else None


def _datetime_from_text(value):
    return parse_datetime(value)


def _build_platform_url(platform_name, item):
    course_id = _first_text(item, ["courseId", "courseid", "course_id", "id", "tid", "productId"])
    clazz_id = _first_text(item, ["clazzId", "clazzid", "classId", "classid"])
    recruit_id = _first_text(item, ["recruitId", "recruitid", "recruit_id"])
    if platform_name == "学习通" and course_id:
        url = f"https://mooc1.chaoxing.com/course/{course_id}.html"
        if clazz_id:
            url += f"?clazzid={clazz_id}"
        return url
    if platform_name == "中国大学 MOOC" and course_id:
        return f"https://www.icourse163.org/course/{course_id}"
    if platform_name == "智慧树" and course_id:
        if recruit_id:
            return f"https://onlineweb.zhihuishu.com/onlinestuh5?courseId={course_id}&recruitId={recruit_id}"
        return f"https://onlineweb.zhihuishu.com/"
    return ""


def _normalize_status(status, progress):
    text = str(status or "")
    if "未完成" in text:
        return "未完成"
    if "已完成" in text or "完成" in text or progress >= 100:
        return "已完成"
    if "进行" in text or "正在" in text or "开课" in text or "学习中" in text:
        return "进行中"
    if "结束" in text or "关闭" in text:
        return "已结束"
    return "进行中" if progress > 0 else "未完成"


def _status_from_progress(progress):
    if progress >= 100:
        return "已完成"
    if progress > 0:
        return "进行中"
    return "未完成"


def _clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _stringify_value(value):
    if isinstance(value, dict):
        for key in ["name", "teacherName", "userName", "nickName", "title", "schoolName"]:
            if key in value and value[key]:
                return _clean_text(value[key])
        return _clean_text(" ".join(str(part) for part in value.values() if isinstance(part, str)))
    return _clean_text(value)
