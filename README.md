# 网课任务弹窗提醒助手

这是一个基于 Python + SQLite + requests + Tkinter 的本地桌面原型，用来演示“从网课平台接口读取本人课程信息，并在任务临期时弹窗提醒”的核心流程。

当前版本支持：

- 学习通
- 中国大学 MOOC
- 智慧树
- 学校作业平台

其他平台接口等拿到真实接口信息后，再继续适配。

## 已实现功能

- 平台接口信息保存：课程接口 URL、Cookie、Referer、User-Agent、POST 请求体
- 学习通课程同步：调用 `courselistdata` 接口读取“我学的课”课程数据
- 中国大学 MOOC 课程同步：调用 `learnerCourseRpcBean.getMyLearnedCoursePanelList.rpc` 接口读取课程数据
- 智慧树课程同步：调用 `queryShareCourseInfo` 接口读取课程数据
- 学校作业平台同步：调用通知接口读取作业/测试截止消息，每次最多检查 20 页，只同步未逾期任务
- 本地课程保存：使用 SQLite 保存课程、教师、状态、截止时间、考试时间等信息
- 本地作业保存：使用 SQLite 单独保存作业/测试任务、课程名、截止时间和状态
- 课程列表查看：桌面窗口中统一展示已同步课程
- 作业列表查看：任务列表里分为“课程任务”和“作业任务”两个板块
- 课程/作业删除：支持选择后删除
- 弹窗提醒：启动程序后自动检查 7 天内临期任务，也可以手动检查提醒

## 桌面版启动方式

先安装依赖：

```bash
python -m pip install -r requirements.txt
```

再启动桌面程序：

```bash
python run_desktop.py
```

启动后会在屏幕右下角显示一个小型浮窗，包含“任务列表、读取课程、读取作业、即将截止”四个入口。可以拖动浮窗位置，点击右上角 `x` 退出。

点击“读取课程”后，可以选择“学习通”、“中国大学 MOOC”或“智慧树”，每个平台的接口信息会单独保存。点击“读取作业”后，填写学校作业平台通知接口信息。

## 学习通接口填写方式

1. 先在浏览器正常登录学习通
2. 进入“课程 / 我学的课”页面
3. 按 `F12` 打开开发者工具，切到 `Network / 网络`
4. 勾选 `Fetch/XHR`
5. 刷新课程页面
6. 找到课程列表接口 `courselistdata`
7. 复制请求里的 URL、Cookie、Referer、请求体参数
8. 回到桌面程序，点击“课程同步”，平台选择“学习通”
9. 填写接口信息后点击“保存并同步”

学习通当前可用的课程列表接口示例：

- 请求方式：`POST`
- 课程接口 URL：`https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata`
- 请求体参数：`courseType=1&courseFolderId=0&query=&pageHeader=-1&single=0&superstarClass=0&isFirefly=0`
- Cookie：从该请求的 Request Headers 里复制
- Referer：从该请求的 Request Headers 里复制

## 中国大学 MOOC 接口填写方式

1. 先在浏览器正常登录中国大学 MOOC
2. 进入个人中心或“我的课程 / SPOC课程”页面
3. 按 `F12` 打开开发者工具，切到 `Network / 网络`
4. 勾选 `Fetch/XHR`
5. 刷新课程页面
6. 找到课程列表接口 `learnerCourseRpcBean.getMyLearnedCoursePanelList.rpc`
7. 复制请求里的 URL、Cookie、Referer、请求体参数
8. 回到桌面程序，点击“课程同步”，平台选择“中国大学 MOOC”
9. 填写接口信息后点击“保存并同步”

中国大学 MOOC 当前可用的课程列表接口示例：

- 请求方式：`POST`
- 课程接口 URL：`https://www.icourse163.org/web/j/learnerCourseRpcBean.getMyLearnedCoursePanelList.rpc?csrfKey=你的csrfKey`
- 请求体参数：`type=30&p=1&psize=8&courseType=2`
- Cookie：从该请求的 Request Headers 里复制
- Referer：从该请求的 Request Headers 里复制

程序会自动从 URL 的 `csrfKey` 参数中提取 token，并加入 `edu-script-token` 请求头。如果 URL 没有 `csrfKey`，会尝试从 Cookie 的 `NTESSTUDYSI` 中读取。

## 智慧树接口填写方式

1. 先在浏览器正常登录智慧树
2. 进入能看到课程的页面，例如共享课或智慧课程页面
3. 按 `F12` 打开开发者工具，切到 `Network / 网络`
4. 勾选 `Fetch/XHR`
5. 刷新课程页面
6. 找到课程列表接口 `queryShareCourseInfo`
7. 复制请求里的 URL、Cookie、Referer、请求体参数
8. 回到桌面程序，点击“课程同步”，平台选择“智慧树”
9. 填写接口信息后点击“保存并同步”

智慧树当前可用的课程列表接口示例：

- 请求方式：`POST`
- 课程接口 URL：`https://onlineservice-api.zhihuishu.com/gateway/t/v1/student/course/share/queryShareCourseInfo`
- 请求体参数：`secretStr=...&date=...`
- Cookie：从该请求的 Request Headers 里复制
- Referer：`https://onlineweb.zhihuishu.com/`

程序会从返回数据中的 `courseOpenDtos` 读取 `courseName`、`teacherName`、`schoolName`、`progress`、`courseStartTime` 和 `courseEndTime`。

## 学校作业平台接口填写方式

1. 先在浏览器正常登录学校作业平台
2. 进入能看到“作业即将截止 / 在线测试即将截止”的通知页面
3. 按 `F12` 打开开发者工具，切到 `Network / 网络`
4. 勾选 `Fetch/XHR`
5. 翻到第一页通知，找到 `/ntf/users/.../notifications` 接口
6. 复制请求里的 URL、Cookie、Referer
7. 回到桌面程序，点击“读取作业”
8. 填写接口信息后点击“保存并读取作业”

学校作业平台当前可用的通知接口示例：

- 请求方式：`GET`
- 作业通知接口 URL：从浏览器开发者工具复制当前账号的完整通知接口，不要把其中的用户标识分享给他人
- Cookie：从该请求的 Request Headers 里复制
- Referer：从该请求的 Request Headers 里复制

程序会自动按 `offset=0,5,10...95` 最多读取 20 页通知，只导入包含“作业/测试”和“截止时间”且尚未逾期的任务。已有作业如果超过截止时间，会在同步或提醒检查时更新为 `已截止`。

## 网页原型

项目里仍保留原来的 Flask 网页原型，方便展示网页端登录、绑定、课程筛选、批量清除等功能。
网页版是独立演示原型，使用项目目录下的 `data.sqlite3`；桌面版使用 Windows AppData 下的数据库。两者数据不共享，不应同时当作同一份正式数据使用。

```bash
python run_server.py
```

浏览器打开：

```text
http://127.0.0.1:5000
```

## 注意

系统不会自动刷课、自动答题、绕过验证码或破解接口。用户必须先正常登录自己的网课账号，并主动授权本系统使用自己的 Cookie 读取本人课程数据。

Cookie 属于敏感登录凭证，不要发给无关人员，也不要写进代码。Cookie 可能会过期，过期后需要重新从浏览器复制。
