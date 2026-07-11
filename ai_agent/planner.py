from datetime import datetime


def format_learning_context(courses, assignments, now=None):
    now = now or datetime.now()
    lines = [
        f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "课程数据：",
    ]
    if courses:
        for index, course in enumerate(courses, 1):
            lines.append(
                (
                    f"{index}. 平台：{course.get('platform_name') or '未知'}；"
                    f"课程：{course.get('course_name') or '未命名'}；"
                    f"进度：{course.get('progress', 0)}%；"
                    f"截止：{course.get('deadline_time') or '暂无'}；"
                    f"考试：{course.get('exam_time') or '暂无'}；"
                    f"状态：{course.get('status') or '未知'}"
                )
            )
    else:
        lines.append("暂无课程数据。")

    lines.extend(["", "作业/测试任务数据："])
    if assignments:
        for index, task in enumerate(assignments, 1):
            lines.append(
                (
                    f"{index}. 平台：{task.get('platform_name') or '未知'}；"
                    f"课程：{task.get('course_name') or '未知课程'}；"
                    f"类型：{task.get('task_type') or '任务'}；"
                    f"标题：{task.get('task_title') or '未命名'}；"
                    f"截止：{task.get('deadline_time') or '暂无'}；"
                    f"状态：{task.get('status') or '未知'}"
                )
            )
    else:
        lines.append("暂无作业/测试任务数据。")

    return "\n".join(lines)


def plan_instruction():
    return (
        "请根据以上课程和作业任务数据，生成“今日学习计划”。"
        "请先判断最紧急的任务，再给出按时间段排列的计划。"
        "如果多数任务已经逾期，请优先安排补救顺序；如果没有有效截止时间，请说明需要用户补充数据。"
    )
