from platform_api import (
    _extract_chaoxing_courses,
    _extract_mooc_courses,
    _extract_school_assignment_tasks,
    _extract_zhihuishu_courses,
)


def test_school_notification_parser():
    data = {
        "notifications": [
            {
                "id": "notice-1",
                "content": "课程 高等数学 的作业 第一章练习 提交即将于 2099-08-01 23:30 截止",
                "created_at": "2026-07-12 08:00:00",
            }
        ]
    }
    tasks = _extract_school_assignment_tasks(data)
    assert len(tasks) == 1
    assert tasks[0]["course_name"] == "高等数学"
    assert tasks[0]["task_title"] == "第一章练习"
    assert tasks[0]["deadline_time"] == "2099-08-01 23:30:00"


def test_chaoxing_basic_course_sample():
    html = """
    <a href="/mycourse/1">Python 程序设计</a>
    张老师
    开课时间：2026-02-01 ~ 2026-12-31
    """
    courses = _extract_chaoxing_courses(html, "https://mooc2-ans.chaoxing.com/courselistdata")
    assert len(courses) == 1
    assert courses[0]["course_name"] == "Python 程序设计"
    assert courses[0]["deadline_time"] == "2026-12-31 00:00:00"


def test_mooc_basic_course_sample():
    data = {
        "result": {
            "result": [
                {
                    "courseId": "mooc-1",
                    "courseName": "形势与政策",
                    "teacherName": "李老师",
                    "progress": 35,
                    "termPanel": {"endTime": "2099-06-30 23:59:00"},
                }
            ]
        }
    }
    courses = _extract_mooc_courses(data)
    assert len(courses) == 1
    assert courses[0]["course_name"] == "形势与政策"
    assert courses[0]["progress"] == 35


def test_zhihuishu_basic_course_sample():
    data = {
        "result": {
            "courseOpenDtos": [
                {
                    "courseId": "tree-1",
                    "courseName": "数字文化观赏",
                    "teacherName": "荣老师",
                    "schoolName": "示例大学",
                    "progress": 20,
                    "courseEndTime": "2099-12-31",
                }
            ]
        }
    }
    courses = _extract_zhihuishu_courses(data)
    assert len(courses) == 1
    assert courses[0]["course_name"] == "数字文化观赏"
    assert courses[0]["teacher"] == "荣老师 · 示例大学"
